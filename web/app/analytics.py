"""Consultas de solo lectura sobre Parquet con DuckDB (sin escribir)."""

from __future__ import annotations

from typing import Any, Sequence

import duckdb

from .config import settings


def available() -> bool:
    root = settings.data_dir / "silver" / "case_expressions"
    return root.exists() and any(root.glob("**/*.parquet"))


def _connect() -> duckdb.DuckDBPyConnection:
    con = duckdb.connect()
    con.execute(f"SET memory_limit = '{settings.duckdb_memory_limit}'")
    con.execute(f"SET threads = {settings.duckdb_threads}")
    return con


def expression_values(
    gene_id: str, projects: Sequence[str] | None = None
) -> list[dict[str, Any]]:
    if not available():
        return []
    glob = (settings.data_dir / "silver" / "case_expressions" / "**" / "*.parquet").as_posix()
    sql = """
        SELECT sample_id, case_id, sample_type, fpkm_unstranded, tpm_unstranded
        FROM read_parquet(?, union_by_name = true)
        WHERE gene_id = ? AND fpkm_unstranded IS NOT NULL
    """
    params: list[Any] = [glob, gene_id]
    if projects:
        sql += " AND project_id IN (" + ", ".join("?" for _ in projects) + ")"
        params.extend(projects)
    con = _connect()
    try:
        cursor = con.execute(sql, params)
        cols = [d[0] for d in cursor.description]
        return [dict(zip(cols, row)) for row in cursor.fetchall()]
    finally:
        con.close()


def gene_percentiles(
    gene_id: str,
    metric: str = "fpkm_unstranded",
    projects: Sequence[str] | None = None,
) -> list[dict[str, Any]]:
    """Percentiles (p25, median, p75, p90) por sample_type para un gen.

    Solo lee ~1200 filas (una por muestra) → cabe en unos pocos MB.
    """
    if not available():
        return []
    allowed = {"fpkm_unstranded", "tpm_unstranded"}
    if metric not in allowed:
        metric = "fpkm_unstranded"
    glob = (settings.data_dir / "silver" / "case_expressions" / "**" / "*.parquet").as_posix()
    sql = f"""
        SELECT
            COALESCE(NULLIF(sample_type, ''), 'Sin dato') AS sample_type,
            count({metric})   AS n_samples,
            sum(CASE WHEN {metric} > 0 THEN 1 ELSE 0 END) AS n_expressed,
            avg({metric})     AS mean,
            stddev_samp({metric}) AS std,
            approx_quantile({metric}, 0.25) AS p25,
            approx_quantile({metric}, 0.5)  AS median,
            approx_quantile({metric}, 0.75) AS p75,
            approx_quantile({metric}, 0.90) AS p90
        FROM read_parquet(?, union_by_name = true)
        WHERE gene_id = ? AND {metric} IS NOT NULL
    """
    params: list[Any] = [glob, gene_id]
    if projects:
        sql += " AND project_id IN (" + ", ".join("?" for _ in projects) + ")"
        params.extend(projects)
    sql += " GROUP BY sample_type ORDER BY sample_type"
    con = _connect()
    try:
        cursor = con.execute(sql, params)
        cols = [d[0] for d in cursor.description]
        return [dict(zip(cols, row)) for row in cursor.fetchall()]
    finally:
        con.close()


CLINICAL_MARKERS = [
    ("ENSG00000091831", "ESR1",  "Receptor de estrógeno — subtipo Luminal A/B"),
    ("ENSG00000082175", "PGR",   "Receptor de progesterona — subtipo Luminal"),
    ("ENSG00000141736", "ERBB2", "HER2 — subtipo HER2-enriched"),
    ("ENSG00000148773", "MKI67", "Proliferación — índice pronóstico"),
    ("ENSG00000141510", "TP53",  "Supresor tumoral — Triple Negativo"),
    ("ENSG00000012048", "BRCA1", "Predisposición hereditaria"),
    ("ENSG00000039068", "CDH1",  "Carcinoma lobulillar"),
    ("ENSG00000087586", "AURKA", "Ciclo celular — resistencia a quimioterapia"),
]


def expression_top_genes(
    file_id: str, limit: int = 50, gene_type_filter: str | None = None
) -> list[dict[str, Any]]:
    """Top N genes por TPM de un archivo de expresión específico."""
    if not available():
        return []
    glob = (settings.data_dir / "silver" / "case_expressions" / "**" / "*.parquet").as_posix()
    sql = """
        SELECT gene_id, gene_name, gene_type,
               tpm_unstranded, fpkm_unstranded, unstranded
        FROM read_parquet(?, union_by_name = true)
        WHERE file_id = ?
          AND NOT starts_with(gene_id, 'N_')
    """
    params: list[Any] = [glob, file_id]
    if gene_type_filter:
        sql += " AND gene_type = ?"
        params.append(gene_type_filter)
    sql += " ORDER BY tpm_unstranded DESC NULLS LAST LIMIT ?"
    params.append(limit)
    con = _connect()
    try:
        cursor = con.execute(sql, params)
        cols = [d[0] for d in cursor.description]
        return [dict(zip(cols, row)) for row in cursor.fetchall()]
    finally:
        con.close()


def expression_gene_types(file_id: str) -> list[str]:
    """Lista de gene_type disponibles para un archivo dado."""
    if not available():
        return []
    glob = (settings.data_dir / "silver" / "case_expressions" / "**" / "*.parquet").as_posix()
    con = _connect()
    try:
        rows = con.execute(
            "SELECT DISTINCT gene_type FROM read_parquet(?, union_by_name=true) "
            "WHERE file_id = ? AND gene_type IS NOT NULL ORDER BY 1",
            [glob, file_id],
        ).fetchall()
        return [r[0] for r in rows]
    finally:
        con.close()


def expression_markers(file_id: str) -> list[dict[str, Any]]:
    """Valores de expresión de los marcadores clínicos para una muestra.

    Añade contexto de percentil comparando contra gold.gene_stats (media cohorte).
    Los percentiles se calculan contra toda la cohorte de silver/case_expressions.
    """
    if not available():
        return []
    glob = (settings.data_dir / "silver" / "case_expressions" / "**" / "*.parquet").as_posix()
    marker_ids = [m[0] for m in CLINICAL_MARKERS]
    placeholders = ", ".join("?" for _ in marker_ids)

    con = _connect()
    try:
        # Valores de la muestra
        cursor = con.execute(
            f"SELECT gene_id, gene_name, tpm_unstranded, fpkm_unstranded "
            f"FROM read_parquet(?, union_by_name=true) "
            f"WHERE file_id = ? AND gene_id IN ({placeholders})",
            [glob, file_id] + marker_ids,
        )
        cols = [d[0] for d in cursor.description]
        sample_rows = {r[0]: dict(zip(cols, r)) for r in cursor.fetchall()}

        # Percentil: qué porcentaje de muestras tienen TPM menor al de esta muestra
        result = []
        for gene_id, gene_name, description in CLINICAL_MARKERS:
            row = sample_rows.get(gene_id)
            tpm = row["tpm_unstranded"] if row else None

            percentile = None
            if tpm is not None:
                pct_row = con.execute(
                    "SELECT round(100.0 * count(*) FILTER (WHERE tpm_unstranded < ?) "
                    "       / nullif(count(*), 0), 1) AS pct "
                    "FROM read_parquet(?, union_by_name=true) "
                    "WHERE gene_id = ? AND tpm_unstranded IS NOT NULL",
                    [tpm, glob, gene_id],
                ).fetchone()
                percentile = pct_row[0] if pct_row else None

            result.append({
                "gene_id": gene_id,
                "gene_name": gene_name,
                "description": description,
                "tpm": tpm,
                "percentile": percentile,
            })
        return result
    finally:
        con.close()


def expression_file_stats() -> dict[str, Any]:
    if not available():
        return {"files": 0, "rows": 0}
    glob = (settings.data_dir / "silver" / "case_expressions" / "**" / "*.parquet").as_posix()
    con = _connect()
    try:
        rows, files = con.execute(
            "SELECT count(*), count(DISTINCT file_id) FROM read_parquet(?, union_by_name = true)",
            [glob],
        ).fetchone()
        return {"rows": rows, "files": files}
    finally:
        con.close()
