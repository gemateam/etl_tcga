"""S03: mutaciones somáticas normalizadas en la capa silver."""

from __future__ import annotations

from ..config import Settings, get_settings
from ..db import connect
from ..logging_utils import get_logger
from ..ops.runs import pipeline_step, record_dq

log = get_logger(__name__)


def silver_mutations(
    projects: list[str],
    settings: Settings | None = None,
) -> dict[str, int]:
    """
    Construye silver.mutations desde bronze.mutations.

    Aplica:
    - Deduplicación por mutation_id (mantiene la primera ocurrencia)
    - Filtro de variantes de baja calidad (FILTER != 'PASS')
    - Enriquecimiento con case_id desde silver.cases para integridad referencial
    """
    settings = settings or get_settings()
    result: dict[str, int] = {}

    for project in projects:
        with pipeline_step("silver_mutations", project) as run:
            with connect() as conn:
                # Limpia silver para este proyecto
                conn.execute(
                    "DELETE FROM silver.mutations WHERE project_id = %s", (project,)
                )

                # Inserta desde bronze con deduplicación y filtro de calidad
                # Solo incluye casos que ya están en silver.cases (integridad)
                result_insert = conn.execute(
                    """
                    INSERT INTO silver.mutations (
                        mutation_id, file_id, case_id, case_submitter_id,
                        tumor_sample_barcode, project_id,
                        hugo_symbol, entrez_gene_id, chromosome,
                        start_position, end_position, strand,
                        variant_classification, variant_type,
                        reference_allele, tumor_seq_allele2,
                        hgvsc, hgvsp_short, transcript_id, exon_number,
                        t_depth, t_ref_count, t_alt_count, n_depth,
                        filter_status, gnomad_af, impact, sift, polyphen,
                        clin_sig, somatic, _gdc_data_release, _updated_at
                    )
                    SELECT DISTINCT ON (bm.mutation_id)
                        bm.mutation_id, bm.file_id, bm.case_id,
                        bm.case_submitter_id, bm.tumor_sample_barcode,
                        bm.project_id,
                        bm.hugo_symbol, bm.entrez_gene_id, bm.chromosome,
                        bm.start_position, bm.end_position, bm.strand,
                        bm.variant_classification, bm.variant_type,
                        bm.reference_allele, bm.tumor_seq_allele2,
                        bm.hgvsc, bm.hgvsp_short, bm.transcript_id, bm.exon_number,
                        bm.t_depth, bm.t_ref_count, bm.t_alt_count, bm.n_depth,
                        bm.filter_status, bm.gnomad_af, bm.impact,
                        bm.sift, bm.polyphen, bm.clin_sig, bm.somatic,
                        bm._gdc_data_release, now()
                    FROM bronze.mutations bm
                    JOIN silver.cases sc ON sc.case_id = bm.case_id
                    WHERE bm.project_id = %s
                      AND (bm.filter_status = 'PASS' OR bm.filter_status IS NULL)
                    ORDER BY bm.mutation_id, bm._ingested_at
                    """,
                    (project,),
                )
                n_inserted = result_insert.rowcount or 0

                # DQ: conteos básicos
                total_bronze = conn.execute(
                    "SELECT count(*) AS n FROM bronze.mutations WHERE project_id = %s",
                    (project,),
                ).fetchone()["n"]

                n_pass = conn.execute(
                    "SELECT count(*) AS n FROM silver.mutations WHERE project_id = %s",
                    (project,),
                ).fetchone()["n"]

                n_genes = conn.execute(
                    "SELECT count(DISTINCT hugo_symbol) AS n FROM silver.mutations WHERE project_id = %s",
                    (project,),
                ).fetchone()["n"]

            run.count(rows_in=total_bronze, rows_out=n_inserted)

            record_dq(
                run.run_id, run.step, project,
                "silver_mutations_not_empty", "warning",
                n_pass > 0,
                f"mutaciones_pass={n_pass} bronze_total={total_bronze}",
            )
            record_dq(
                run.run_id, run.step, project,
                "silver_mutations_genes_present", "warning",
                n_genes > 0,
                f"genes_únicos={n_genes}",
            )

            result[project] = n_inserted
            log.info(
                "Silver de mutaciones",
                extra={
                    "event": "silver_mutations",
                    "rows": n_inserted,
                    "genes": n_genes,
                },
            )

    return result
