"""E05: TSV de expresión a Parquet bronze con DuckDB."""

from __future__ import annotations

import shutil
from pathlib import Path

from ..config import Settings, get_settings
from ..duck import duck_connect, sql_literal
from ..expression import iter_expression_rows
from ..gdc.fields import EXPECTED_GENES_PER_FILE
from ..logging_utils import get_logger
from ..ops.runs import pipeline_step, record_dq, recorded_release

log = get_logger(__name__)

FILE_ID_REGEX = r"/([^/]+)/([0-9a-fA-F-]{36})/[^/]+$"


def _as_posix(path: Path) -> str:
    return path.as_posix()


def bronze_expressions(projects: list[str], settings: Settings | None = None) -> dict[str, int]:
    settings = settings or get_settings()
    result: dict[str, int] = {}
    release = recorded_release()
    out_root = settings.bronze_root() / "expressions"

    for project in projects:
        source_dir = settings.landing_expressions_dir(project)
        files = sorted(source_dir.glob("*/*.tsv"))
        with pipeline_step("bronze_expressions", project) as run:
            if not files:
                raise FileNotFoundError(
                    f"No hay TSV de expresión en {source_dir}. Ejecuta download-expressions."
                )
            parsed_rows = sum(1 for _ in iter_expression_rows(files[0]))
            partition = out_root / f"project_id={project}"
            shutil.rmtree(partition, ignore_errors=True)
            glob = _as_posix(source_dir / "*" / "*.tsv")
            parquet_glob = _as_posix(out_root / "**" / "*.parquet")
            release_sql = sql_literal(release) if release else "NULL"
            con = duck_connect(settings)
            try:
                query = f"""
                COPY (
                    WITH src AS (
                        SELECT *, filename AS _source_file
                        FROM read_csv(
                            {sql_literal(glob)},
                            header = true,
                            delim = '\\t',
                            comment = '#',
                            filename = true,
                            union_by_name = true,
                            null_padding = true
                        )
                    )
                    SELECT
                        {sql_literal(project)} AS project_id,
                        regexp_extract(
                            replace(_source_file, chr(92), '/'),
                            {sql_literal(FILE_ID_REGEX)}, 2
                        ) AS file_id,
                        gene_id,
                        gene_name,
                        gene_type,
                        unstranded,
                        stranded_first,
                        stranded_second,
                        tpm_unstranded,
                        fpkm_unstranded,
                        fpkm_uq_unstranded,
                        now() AS _ingested_at,
                        _source_file,
                        {release_sql} AS _gdc_data_release
                    FROM src
                    WHERE NOT starts_with(gene_id, 'N_')
                ) TO {sql_literal(_as_posix(out_root))}
                  (FORMAT PARQUET, PARTITION_BY (project_id), OVERWRITE_OR_IGNORE true)
                """
                con.execute(query)
                rows = con.execute(
                    f"SELECT count(*) FROM read_parquet({sql_literal(parquet_glob)}) "
                    "WHERE project_id = ?",
                    [project],
                ).fetchone()[0]
                gene_counts = con.execute(
                    f"""
                    SELECT file_id, count(*) AS n
                    FROM read_parquet({sql_literal(parquet_glob)})
                    WHERE project_id = ?
                    GROUP BY file_id
                    """,
                    [project],
                ).fetchall()
                bad = [(fid, n) for fid, n in gene_counts if n != EXPECTED_GENES_PER_FILE]
                nulls = con.execute(
                    f"""
                    SELECT count(*) FROM read_parquet({sql_literal(parquet_glob)})
                    WHERE project_id = ? AND (gene_id IS NULL OR file_id IS NULL OR file_id = '')
                    """,
                    [project],
                ).fetchone()[0]
            finally:
                con.close()

            run.count(rows_in=len(files), rows_out=rows)
            record_dq(
                run.run_id,
                run.step,
                project,
                "expressions_gene_id_not_null",
                "critical",
                nulls == 0,
                f"filas_con_gene_id_nulo_o_file_id_vacio={nulls}",
            )
            record_dq(
                run.run_id,
                run.step,
                project,
                "expressions_expected_gene_count",
                "warning",
                not bad,
                (
                    f"archivos={len(gene_counts)} fuera_de_rango={len(bad)} "
                    f"esperado~{EXPECTED_GENES_PER_FILE}"
                    + (f" ejemplo={bad[:3]}" if bad else "")
                ),
            )
            record_dq(
                run.run_id,
                run.step,
                project,
                "expressions_files_on_disk",
                "warning",
                len(files) == len(gene_counts),
                f"tsv={len(files)} con_datos={len(gene_counts)}",
            )
            record_dq(
                run.run_id,
                run.step,
                project,
                "expressions_tsv_parser",
                "critical",
                parsed_rows > 0,
                f"filas_parseadas={parsed_rows} archivo={files[0].name}",
            )
            result[project] = rows
            log.info(
                "Bronze de expresiones",
                extra={"event": "bronze_expressions", "rows": rows, "files": len(files)},
            )
    return result


def expression_glob(settings: Settings | None = None) -> str:
    settings = settings or get_settings()
    return (settings.bronze_root() / "expressions" / "**" / "*.parquet").as_posix()
