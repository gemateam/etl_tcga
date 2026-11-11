"""S01: expresión génica en formato narrow unida a caso y muestra."""

from __future__ import annotations

import shutil

from ..config import Settings, get_settings
from ..db import connect
from ..duck import duck_connect, sql_literal
from ..logging_utils import get_logger
from ..ops.runs import pipeline_step, record_dq

log = get_logger(__name__)


def silver_case_expressions(
    projects: list[str], settings: Settings | None = None
) -> dict[str, int]:
    settings = settings or get_settings()
    result: dict[str, int] = {}
    out_root = settings.silver_root() / "case_expressions"
    bronze_expr = settings.bronze_root() / "expressions" / "**" / "*.parquet"
    bronze_files = settings.bronze_root() / "files" / "**" / "files.parquet"
    if not any((settings.bronze_root() / "files").glob("**/files.parquet")):
        raise FileNotFoundError(f"Falta {bronze_files}; ejecuta bronze-files.")
    out_root.mkdir(parents=True, exist_ok=True)

    for project in projects:
        with pipeline_step("silver_case_expressions", project) as run:
            partition = out_root / f"project_id={project}"
            shutil.rmtree(partition, ignore_errors=True)
            con = duck_connect(settings)
            try:
                rows = con.execute(
                    f"SELECT count(*) FROM read_parquet({sql_literal(bronze_expr.as_posix())}) WHERE project_id = ?",
                    [project],
                ).fetchone()[0]
                if rows == 0:
                    raise FileNotFoundError(
                        f"No hay expresiones bronze para {project}; ejecuta bronze-expressions."
                    )
                query = f"""
                COPY (
                    WITH e AS (
                        SELECT * FROM read_parquet({sql_literal(bronze_expr.as_posix())})
                        WHERE project_id = {sql_literal(project)}
                    ),
                    f AS (
                        SELECT
                            file_id,
                            any_value(case_id) AS case_id,
                            any_value(sample_id) AS sample_id,
                            any_value(sample_type) AS sample_type,
                            any_value(project_id) AS file_project_id
                        FROM read_parquet({sql_literal(bronze_files.as_posix())})
                        GROUP BY file_id
                    )
                    SELECT
                        e.project_id,
                        e.file_id,
                        f.case_id,
                        f.sample_id,
                        f.sample_type,
                        e.gene_id,
                        e.gene_name,
                        e.gene_type,
                        CAST(TRY_CAST(e.unstranded AS BIGINT) AS BIGINT) AS unstranded,
                        CAST(TRY_CAST(e.stranded_first AS BIGINT) AS BIGINT) AS stranded_first,
                        CAST(TRY_CAST(e.stranded_second AS BIGINT) AS BIGINT) AS stranded_second,
                        CAST(TRY_CAST(e.tpm_unstranded AS DOUBLE) AS DOUBLE) AS tpm_unstranded,
                        CAST(TRY_CAST(e.fpkm_unstranded AS DOUBLE) AS DOUBLE) AS fpkm_unstranded,
                        CAST(TRY_CAST(e.fpkm_uq_unstranded AS DOUBLE) AS DOUBLE) AS fpkm_uq_unstranded,
                        e._gdc_data_release
                    FROM e
                    JOIN f USING (file_id)
                    WHERE e.gene_id IS NOT NULL
                      AND NOT starts_with(e.gene_id, 'N_')
                ) TO {sql_literal(out_root.as_posix())}
                  (FORMAT PARQUET, PARTITION_BY (project_id), OVERWRITE_OR_IGNORE true)
                """
                con.execute(query)
                written = con.execute(
                    f"SELECT count(*) FROM read_parquet({sql_literal((out_root / '**' / '*.parquet').as_posix())}) WHERE project_id = ?",
                    [project],
                ).fetchone()[0]
                duplicates = con.execute(
                    f"""
                    SELECT count(*) FROM (
                        SELECT file_id, gene_id
                        FROM read_parquet({sql_literal((out_root / '**' / '*.parquet').as_posix())})
                        WHERE project_id = ?
                        GROUP BY file_id, gene_id
                        HAVING count(*) > 1
                    )
                    """,
                    [project],
                ).fetchone()[0]
                null_keys = con.execute(
                    f"""
                    SELECT count(*) FROM read_parquet({sql_literal((out_root / '**' / '*.parquet').as_posix())})
                    WHERE project_id = ? AND (file_id IS NULL OR gene_id IS NULL OR case_id IS NULL)
                    """,
                    [project],
                ).fetchone()[0]
                distinct_files = con.execute(
                    f"""
                    SELECT count(DISTINCT file_id) FROM read_parquet({sql_literal((out_root / '**' / '*.parquet').as_posix())})
                    WHERE project_id = ?
                    """,
                    [project],
                ).fetchone()[0]
                expression_cases = con.execute(
                    f"""
                    SELECT count(DISTINCT case_id) FROM read_parquet({sql_literal((out_root / '**' / '*.parquet').as_posix())})
                    WHERE project_id = ?
                    """,
                    [project],
                ).fetchone()[0]
            finally:
                con.close()

            run.count(rows_in=rows, rows_out=written)
            record_dq(
                run.run_id,
                run.step,
                project,
                "case_expressions_unique_file_gene",
                "critical",
                duplicates == 0,
                f"duplicados={duplicates}",
            )
            record_dq(
                run.run_id,
                run.step,
                project,
                "case_expressions_keys_not_null",
                "critical",
                null_keys == 0,
                f"filas_con_clave_nula={null_keys}",
            )
            with connect() as conn:
                total_files = conn.execute(
                    "SELECT count(*) AS n FROM silver.files WHERE project_id = %s", (project,)
                ).fetchone()["n"]
                total_cases = conn.execute(
                    "SELECT count(*) AS n FROM silver.cases WHERE project_id = %s", (project,)
                ).fetchone()["n"]
            record_dq(
                run.run_id,
                run.step,
                project,
                "case_expressions_file_coverage",
                "warning",
                distinct_files == total_files,
                f"archivos_con_expresion={distinct_files} archivos_manifiesto={total_files}",
            )
            record_dq(
                run.run_id,
                run.step,
                project,
                "case_expressions_case_coverage",
                "warning",
                expression_cases == total_cases,
                f"casos_con_expresion={expression_cases} casos_silver={total_cases}",
            )
            result[project] = written
            log.info(
                "Silver de expresiones",
                extra={"event": "silver_case_expressions", "rows": written},
            )
    return result
