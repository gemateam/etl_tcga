"""Tablas gold de apoyo: resumen de muestras, estadísticas por gen y marcadores."""

from __future__ import annotations

import shutil

from ..config import Settings, get_settings
from ..db import connect, copy_rows
from ..duck import duck_connect, sql_literal
from ..logging_utils import get_logger
from ..ops.runs import pipeline_step, record_dq, recorded_release

log = get_logger(__name__)

# ----------------------------------------------------------------- marcadores
MARKER_GENES: list[tuple[str, str, str, str | None]] = [
    # (gene_name, gene_id_prefix, description, note)
    # gene_id_prefix es el Ensembl base sin versión (ENSG00000091831)
    # La búsqueda en la BD usa LIKE 'prefix%' para tolerar sufijos de versión
    ("ESR1",  "ENSG00000091831",
     "Receptor de estrógeno alfa",
     "Alta expresión asociada a subtipos luminales; confirmar con IHQ."),
    ("PGR",   "ENSG00000082175",
     "Receptor de progesterona",
     "Alta expresión en subtipos luminales; confirmar con IHQ."),
    ("ERBB2", "ENSG00000141736",
     "Receptor 2 del factor de crecimiento epidérmico (HER2)",
     "Amplificación y sobreexpresión definen el subtipo HER2-enriquecido; confirmar con IHQ/FISH."),
    ("MKI67", "ENSG00000148773",
     "Proteína Ki-67: marcador de proliferación celular",
     "Índice Ki-67 elevado se asocia a mayor grado tumoral y peor pronóstico."),
    ("TP53",  "ENSG00000141510",
     "Proteína p53: supresor tumoral",
     "Mutaciones frecuentes en tumores de alto grado y en el subtipo basal-like."),
    ("BRCA1", "ENSG00000012048",
     "Proteína BRCA1: reparación del ADN por recombinación homóloga",
     "Pérdida de función asociada a cáncer de mama hereditario y subtipo basal-like."),
    ("CDH1",  "ENSG00000039068",
     "E-cadherina: molécula de adhesión celular",
     "Pérdida de expresión característica del carcinoma lobulillar invasivo."),
    ("AURKA", "ENSG00000087586",
     "Aurora cinasa A: regulador del ciclo celular",
     "Sobreexpresión asociada a inestabilidad cromosómica y resistencia a quimioterapia."),
]


def _in_placeholders(n: int) -> str:
    return ", ".join("?" for _ in range(n))


# ---------------------------------------------------------------- sample_summary
def sample_summary(projects: list[str], settings: Settings | None = None) -> dict[str, int]:
    settings = settings or get_settings()
    release = recorded_release()
    result: dict[str, int] = {}
    for project in projects:
        with pipeline_step("gold_sample_summary", project) as run:
            with connect() as conn:
                conn.execute(
                    "DELETE FROM gold.sample_summary WHERE project_id = %s", (project,)
                )
                conn.execute(
                    """
                    WITH dx AS (
                        SELECT DISTINCT ON (d.case_id)
                            d.case_id,
                            COALESCE(NULLIF(d.ajcc_pathologic_stage, ''), 'Sin dato') AS stage
                        FROM silver.cases_diagnoses d
                        JOIN silver.cases c USING (case_id)
                        WHERE c.project_id = %s
                        ORDER BY d.case_id,
                                 d.diagnosis_is_primary_disease DESC NULLS LAST,
                                 d.days_to_diagnosis NULLS LAST,
                                 d.diagnosis_id
                    )
                    INSERT INTO gold.sample_summary
                        (project_id, sample_type, vital_status, stage,
                         n_samples, n_cases, gdc_data_release, computed_at)
                    SELECT
                        c.project_id,
                        COALESCE(NULLIF(s.sample_type, ''), 'Sin dato'),
                        COALESCE(NULLIF(d.vital_status, ''), 'Sin dato'),
                        COALESCE(dx.stage, 'Sin dato'),
                        count(DISTINCT s.sample_id),
                        count(DISTINCT c.case_id),
                        %s,
                        now()
                    FROM silver.cases c
                    LEFT JOIN silver.cases_demographics d USING (case_id)
                    LEFT JOIN dx USING (case_id)
                    JOIN silver.samples s ON s.case_id = c.case_id
                    WHERE c.project_id = %s
                    GROUP BY 1, 2, 3, 4
                    """,
                    (project, release, project),
                )
                rows = conn.execute(
                    "SELECT count(*) AS n FROM gold.sample_summary WHERE project_id = %s",
                    (project,),
                ).fetchone()["n"]
            run.count(rows_in=rows, rows_out=rows)
            record_dq(run.run_id, run.step, project, "sample_summary_not_empty",
                      "warning", rows > 0, f"filas={rows}")
            result[project] = rows
            log.info("Gold resumen de muestras",
                     extra={"event": "gold_sample_summary", "rows": rows})
    return result


# -------------------------------------------------------------------- gene_stats
def gene_stats(projects: list[str], settings: Settings | None = None) -> dict[str, int]:
    """Estadísticas por gen, tipo de muestra y métrica (fpkm + tpm).

    Calcula media, varianza, std, n_samples y n_expressed en DuckDB.
    Los percentiles (median, p25, p75, p90) se calculan a petición en la web
    para el gen consultado, ya que calcularlos para 60K genes × 2 métricas × 3
    tipos de muestra agota la RAM disponible (7.6 GB) en este entorno.
    """
    settings = settings or get_settings()
    release = recorded_release()
    silver_glob = (
        settings.silver_root() / "case_expressions" / "**" / "*.parquet"
    ).as_posix()
    out_dir = settings.gold_root() / "gene_stats"
    shutil.rmtree(out_dir, ignore_errors=True)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_parquet = out_dir / "data.parquet"

    with pipeline_step(
        "gold_gene_stats", ",".join(projects), params={"projects": projects}
    ) as run:
        con = duck_connect(settings)
        try:
            con.execute("""
                CREATE TABLE _gs (
                    project_id TEXT, gene_id TEXT, gene_name TEXT,
                    gene_type TEXT, sample_type TEXT, metric TEXT,
                    n_samples BIGINT, n_expressed BIGINT,
                    mean DOUBLE, std DOUBLE, variance DOUBLE,
                    median DOUBLE, p25 DOUBLE, p75 DOUBLE, p90 DOUBLE
                )
            """)
            for metric_col in ("fpkm_unstranded", "tpm_unstranded"):
                metric_literal = sql_literal(metric_col)
                con.execute(
                    f"""
                    INSERT INTO _gs
                    SELECT
                        project_id,
                        gene_id,
                        any_value(gene_name)  AS gene_name,
                        any_value(gene_type)  AS gene_type,
                        COALESCE(NULLIF(sample_type, ''), 'Sin dato') AS sample_type,
                        {metric_literal}      AS metric,
                        count({metric_col})   AS n_samples,
                        sum(CASE WHEN {metric_col} > 0 THEN 1 ELSE 0 END) AS n_expressed,
                        avg({metric_col})     AS mean,
                        stddev_samp({metric_col}) AS std,
                        var_samp({metric_col}) AS variance,
                        NULL::DOUBLE           AS median,
                        NULL::DOUBLE           AS p25,
                        NULL::DOUBLE           AS p75,
                        NULL::DOUBLE           AS p90
                    FROM read_parquet({sql_literal(silver_glob)})
                    WHERE project_id IN ({_in_placeholders(len(projects))})
                      AND {metric_col} IS NOT NULL
                    GROUP BY project_id, gene_id, sample_type
                    """,
                    list(projects),
                )
                n = con.execute("SELECT count(*) FROM _gs").fetchone()[0]
                log.info("Gene stats métrica completada",
                         extra={"event": "gene_stats_metric",
                                "detail": f"metric={metric_col} total_acum={n}"})
            con.execute(
                f"COPY _gs TO {sql_literal(out_parquet.as_posix())} (FORMAT PARQUET)"
            )
            total = con.execute("SELECT count(*) FROM _gs").fetchone()[0]
        finally:
            con.close()

        COLS = (
            "project_id", "gene_id", "gene_name", "gene_type", "sample_type",
            "metric", "n_samples", "n_expressed", "mean", "std", "variance",
            "median", "p25", "p75", "p90", "gdc_data_release",
        )
        with connect() as conn:
            conn.execute(
                "DELETE FROM gold.gene_stats WHERE project_id = ANY(%s)", (list(projects),)
            )
            import duckdb as _ddb
            chunk_con = _ddb.connect()
            cursor = chunk_con.execute(
                f"SELECT * FROM read_parquet({sql_literal(out_parquet.as_posix())})"
            )
            written = 0
            while True:
                chunk = cursor.fetchmany(5_000)
                if not chunk:
                    break
                copy_rows(conn, "gold.gene_stats", COLS,
                          [tuple(r) + (release,) for r in chunk])
                written += len(chunk)
            chunk_con.close()

        run.count(rows_in=total, rows_out=written)
        record_dq(run.run_id, run.step, ",".join(projects),
                  "gene_stats_not_empty", "warning",
                  written > 0, f"filas={written}")
        result: dict[str, int] = {}
        for project in projects:
            result[project] = written // max(len(projects), 1)
        log.info("Gold estadísticas de genes",
                 extra={"event": "gold_gene_stats", "rows": written})
    return result


# -------------------------------------------------------------------- gene_dim
def populate_gene_dim(projects: list[str], settings: Settings | None = None) -> int:
    """Llena gold.gene_dim con TODOS los genes de silver (independiente del pivot)."""
    settings = settings or get_settings()
    silver_glob = (
        settings.silver_root() / "case_expressions" / "**" / "*.parquet"
    ).as_posix()
    con = duck_connect(settings)
    try:
        cursor = con.execute(
            f"""
            SELECT DISTINCT project_id, gene_id, gene_name, gene_type
            FROM read_parquet({sql_literal(silver_glob)})
            WHERE project_id IN ({_in_placeholders(len(projects))})
              AND gene_id IS NOT NULL
            ORDER BY project_id, gene_id
            """,
            list(projects),
        )
        all_genes: list[tuple] = cursor.fetchall()
    finally:
        con.close()

    if not all_genes:
        return 0

    with connect() as conn:
        conn.execute(
            "DELETE FROM gold.gene_dim WHERE project_id = ANY(%s)", (list(projects),)
        )
        copy_rows(conn, "gold.gene_dim",
                  ("project_id", "gene_id", "gene_name", "gene_type"),
                  all_genes)
    log.info("Gold gene_dim poblado",
             extra={"event": "gold_gene_dim", "rows": len(all_genes)})
    return len(all_genes)


# --------------------------------------------------------- marker_genes (G03a)
def seed_marker_genes() -> int:
    """Inserta los marcadores en gold.marker_genes resolviendo el gene_id real."""
    with connect() as conn:
        # Resuelve el gene_id real (con sufijo de versión) desde gene_dim
        resolved = []
        for gene_name, gene_id_prefix, description, note in MARKER_GENES:
            row = conn.execute(
                "SELECT gene_id FROM gold.gene_dim WHERE gene_id LIKE %s LIMIT 1",
                (gene_id_prefix + "%",),
            ).fetchone()
            real_id = row["gene_id"] if row else None
            resolved.append((gene_name, real_id or gene_id_prefix, description, note))
        conn.execute("DELETE FROM gold.marker_genes")
        copy_rows(conn, "gold.marker_genes",
                  ("gene_name", "gene_id", "description", "note"),
                  resolved)
    log.info("Gold marker_genes sembrado",
             extra={"event": "gold_marker_genes", "rows": len(resolved)})
    return len(resolved)


# ------------------------------------------------------- sample_marker_values (G03b)
def sample_marker_values(
    projects: list[str], settings: Settings | None = None
) -> dict[str, int]:
    """G03: TPM y percentil de cada marcador por muestra y tipo de muestra.

    Busca los gene_id reales desde gold.marker_genes (que ya resolvió los
    sufijos de versión Ensembl con LIKE durante seed_marker_genes).
    """
    settings = settings or get_settings()
    release = recorded_release()
    silver_glob = (
        settings.silver_root() / "case_expressions" / "**" / "*.parquet"
    ).as_posix()
    result: dict[str, int] = {}

    # Lee los gene_id reales (con versión) desde gold.marker_genes
    with connect() as conn:
        marker_rows = conn.execute(
            "SELECT gene_name, gene_id FROM gold.marker_genes WHERE gene_id IS NOT NULL"
        ).fetchall()
    marker_ids = [r["gene_id"] for r in marker_rows]

    if not marker_ids:
        log.warning("No hay marcadores resueltos en gold.marker_genes; "
                    "ejecuta gold-extras primero")
        return {p: 0 for p in projects}

    for project in projects:
        with pipeline_step("gold_sample_marker_values", project) as run:
            con = duck_connect(settings)
            try:
                cursor = con.execute(
                    f"""
                    WITH vals AS (
                        SELECT
                            project_id,
                            sample_id,
                            any_value(case_id)   AS case_id,
                            COALESCE(NULLIF(sample_type, ''), 'Sin dato') AS sample_type,
                            gene_id,
                            any_value(gene_name) AS gene_name,
                            avg(tpm_unstranded)  AS tpm
                        FROM read_parquet({sql_literal(silver_glob)})
                        WHERE project_id = ?
                          AND gene_id IN ({_in_placeholders(len(marker_ids))})
                        GROUP BY project_id, sample_id, sample_type, gene_id
                    ),
                    ranked AS (
                        SELECT *,
                            percent_rank() OVER (
                                PARTITION BY project_id, sample_type, gene_id
                                ORDER BY tpm
                            ) * 100 AS pct_rank
                        FROM vals
                    )
                    SELECT
                        project_id, sample_id, case_id, sample_type,
                        gene_id, gene_name, tpm,
                        round(pct_rank, 1) AS pct_rank,
                        CASE
                            WHEN pct_rank >= 75 THEN 'Alto'
                            WHEN pct_rank < 25  THEN 'Bajo'
                            ELSE 'Medio'
                        END AS category
                    FROM ranked
                    ORDER BY sample_id, gene_id
                    """,
                    [project] + marker_ids,
                )
                rows_data = cursor.fetchall()
            finally:
                con.close()

            COLS = (
                "project_id", "sample_id", "case_id", "sample_type",
                "gene_id", "gene_name", "tpm", "pct_rank", "category",
                "gdc_data_release",
            )
            with connect() as conn:
                conn.execute(
                    "DELETE FROM gold.sample_marker_values WHERE project_id = %s",
                    (project,),
                )
                if rows_data:
                    copy_rows(conn, "gold.sample_marker_values", COLS,
                              [tuple(r) + (release,) for r in rows_data])

            n = len(rows_data)
            run.count(rows_in=n, rows_out=n)

            with connect() as conn:
                found = conn.execute(
                    "SELECT count(DISTINCT gene_id) AS n FROM gold.gene_dim "
                    "WHERE project_id = %s AND gene_id = ANY(%s)",
                    (project, marker_ids),
                ).fetchone()["n"]
            record_dq(
                run.run_id, run.step, project,
                "all_markers_in_gene_dim", "warning",
                found == len(marker_ids),
                f"marcadores_resueltos={found}/{len(marker_ids)}",
            )
            result[project] = n
            log.info("Gold sample_marker_values",
                     extra={"event": "gold_sample_marker_values",
                            "rows": n, "detail": f"proyecto={project}"})
    return result
