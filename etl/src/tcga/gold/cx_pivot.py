"""G02: varianza por gen y matriz ancha (gene variance pivot)."""

from __future__ import annotations

import re
import shutil
from typing import Any

from ..config import Settings, get_settings
from ..db import connect, copy_rows
from ..duck import duck_connect, sql_literal
from ..logging_utils import get_logger
from ..ops.runs import pipeline_step, record_dq, recorded_release

log = get_logger(__name__)

ALLOWED_METRICS = {"fpkm_unstranded", "tpm_unstranded", "fpkm_uq_unstranded"}
IDENT_RE = re.compile(r"^[A-Za-z0-9_.\-]+$")


def _ident(name: str) -> str:
    if not IDENT_RE.fullmatch(name):
        raise ValueError(f"Identificador no permitido: {name!r}")
    return '"' + name.replace('"', '""') + '"'


def _in_placeholders(n: int) -> str:
    return ", ".join("?" for _ in range(n))


def gold_cx_pivot(
    projects: list[str],
    top_genes: int = 100,
    metric: str = "fpkm_unstranded",
    log2: bool = False,
    sample_type: str | None = None,
    settings: Settings | None = None,
) -> int:
    settings = settings or get_settings()
    if metric not in ALLOWED_METRICS:
        raise ValueError(f"metric debe ser uno de {sorted(ALLOWED_METRICS)}")
    if top_genes < 1:
        raise ValueError("top_genes debe ser >= 1")
    release = recorded_release()
    silver_glob = (
        settings.silver_root() / "case_expressions" / "**" / "*.parquet"
    ).as_posix()
    gold_dir = settings.gold_root() / "cx_pivot"
    shutil.rmtree(gold_dir, ignore_errors=True)
    gold_dir.mkdir(parents=True, exist_ok=True)
    out_parquet = gold_dir / "data.parquet"

    with pipeline_step(
        "gold_cx_pivot",
        None,
        params={
            "projects": projects,
            "top_genes": top_genes,
            "metric": metric,
            "log2": log2,
            "sample_type": sample_type,
        },
    ) as run:
        con = duck_connect(settings)
        try:
            params: list[Any] = list(projects)
            where_sample = ""
            if sample_type:
                where_sample = " AND sample_type = ?"
                params.append(sample_type)
            base_value = f"CAST({metric} AS DOUBLE)"
            if log2:
                base_value = f"(ln({base_value} + 1) / ln(2))"
            con.execute(
                f"""
                CREATE TEMP TABLE vals AS
                SELECT project_id, sample_id, case_id, sample_type,
                       gene_id, gene_name, gene_type,
                       {base_value} AS val
                FROM read_parquet({sql_literal(silver_glob)})
                WHERE project_id IN ({_in_placeholders(len(projects))})
                  AND {metric} IS NOT NULL
                  {where_sample}
                """,
                params,
            )
            con.execute(
                """
                CREATE TEMP TABLE top_genes AS
                SELECT gene_id,
                       any_value(gene_name) AS gene_name,
                       any_value(gene_type) AS gene_type,
                       var_samp(val) AS variance,
                       count(val) AS n
                FROM vals
                GROUP BY gene_id
                HAVING count(val) > 1
                ORDER BY variance DESC NULLS LAST
                LIMIT ?
                """,
                [top_genes],
            )
            genes = con.execute(
                "SELECT gene_id, gene_name, gene_type, variance, n FROM top_genes ORDER BY variance DESC"
            ).fetchall()
            if not genes:
                raise RuntimeError("No hay genes con varianza calculable")
            gene_ids = [g[0] for g in genes]
            aggregations = ",\n".join(
                f"max(val) FILTER (WHERE gene_id = {sql_literal(g)}) AS {_ident(g)}"
                for g in gene_ids
            )
            con.execute(
                f"""
                COPY (
                    SELECT
                        sample_id,
                        any_value(project_id) AS project_id,
                        any_value(case_id) AS case_id,
                        any_value(sample_type) AS sample_type,
                        {aggregations}
                    FROM vals
                    WHERE gene_id IN (SELECT gene_id FROM top_genes)
                    GROUP BY sample_id
                ) TO {sql_literal(out_parquet.as_posix())} (FORMAT PARQUET)
                """
            )
            pivot_cursor = con.execute(
                f"SELECT * FROM read_parquet({sql_literal(out_parquet.as_posix())})"
            )
            pivot_cols = [d[0] for d in pivot_cursor.description]
            pivot_rows = pivot_cursor.fetchall()
            all_genes = con.execute(
                f"""
                SELECT DISTINCT project_id, gene_id, gene_name, gene_type
                FROM read_parquet({sql_literal(silver_glob)})
                WHERE project_id IN ({_in_placeholders(len(projects))})
                """,
                list(projects),
            ).fetchall()
            sample_count = len(pivot_rows)
        finally:
            con.close()

        columns = ["sample_id", "project_id", "case_id", "sample_type"] + pivot_cols[4:] + [
            "gdc_data_release"
        ]
        gene_columns = pivot_cols[4:]
        ddl_parts = [
            '"sample_id" TEXT PRIMARY KEY',
            '"project_id" TEXT',
            '"case_id" TEXT',
            '"sample_type" TEXT',
        ]
        ddl_parts += [f"{_ident(c)} DOUBLE PRECISION" for c in gene_columns]
        ddl_parts += [
            '"gdc_data_release" TEXT',
            '"computed_at" TIMESTAMPTZ NOT NULL DEFAULT now()',
        ]
        ddl_columns = ",\n".join(ddl_parts)
        with connect() as conn:
            conn.execute("DROP TABLE IF EXISTS gold.cx_pivot")
            conn.execute(f"CREATE TABLE gold.cx_pivot ({ddl_columns})")
            rows = [tuple(row) + (release,) for row in pivot_rows]
            copy_rows(conn, "gold.cx_pivot", columns, rows)
            # gene_dim lo gestiona populate_gene_dim (todos los genes del silver)
            conn.execute("DELETE FROM gold.gene_dim WHERE project_id = ANY(%s)", (list(projects),))
            copy_rows(
                conn,
                "gold.gene_dim",
                ("project_id", "gene_id", "gene_name", "gene_type"),
                all_genes,
            )

        run.count(rows_in=sample_count, rows_out=sample_count)
        record_dq(
            run.run_id,
            run.step,
            ",".join(projects),
            "cx_pivot_samples",
            "critical",
            sample_count > 0,
            f"muestras={sample_count} genes={len(gene_columns)}",
        )
        record_dq(
            run.run_id,
            run.step,
            ",".join(projects),
            "cx_pivot_top_genes",
            "warning",
            len(gene_columns) == top_genes,
            f"genes={len(gene_columns)} solicitados={top_genes}",
        )
        log.info(
            "Gold gene variance pivot",
            extra={
                "event": "gold_cx_pivot",
                "rows": sample_count,
                "detail": f"genes={len(gene_columns)} metric={metric} log2={log2}",
            },
        )
        return sample_count
