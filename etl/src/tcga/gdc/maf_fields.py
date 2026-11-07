"""Campos y filtros del GDC para archivos MAF (mutaciones somáticas)."""

from __future__ import annotations

# Columnas estándar del MAF del GDC (Mutation Annotation Format v2.4)
# Verificadas contra: https://docs.gdc.cancer.gov/Data/File_Formats/MAF_Format/
MAF_COLUMNS: tuple[str, ...] = (
    "Hugo_Symbol",
    "Entrez_Gene_Id",
    "NCBI_Build",
    "Chromosome",
    "Start_Position",
    "End_Position",
    "Strand",
    "Variant_Classification",
    "Variant_Type",
    "Reference_Allele",
    "Tumor_Seq_Allele1",
    "Tumor_Seq_Allele2",
    "Tumor_Sample_Barcode",
    "Matched_Norm_Sample_Barcode",
    "HGVSc",
    "HGVSp",
    "HGVSp_Short",
    "Transcript_ID",
    "Exon_Number",
    "t_depth",
    "t_ref_count",
    "t_alt_count",
    "n_depth",
    "FILTER",
    "gnomAD_AF",
    "gnomAD_AFR_AF",
    "gnomAD_AMR_AF",
    "gnomAD_EAS_AF",
    "gnomAD_NFE_AF",
    "gnomAD_SAS_AF",
    "IMPACT",
    "SIFT",
    "PolyPhen",
    "CLIN_SIG",
    "SOMATIC",
    "case_id",
    "GDC_Tumor_DNA_Barcode",
    "GDC_Normal_DNA_Barcode",
    "case_submitter_id",
    "project_id",
)

# Campos del endpoint /files para buscar MAFs
MAF_FILE_FIELDS: tuple[str, ...] = (
    "file_id",
    "file_name",
    "file_size",
    "md5sum",
    "access",
    "data_category",
    "data_type",
    "data_format",
    "experimental_strategy",
    "analysis.workflow_type",
    "cases.case_id",
    "cases.project.project_id",
    "cases.submitter_id",
)


def maf_filters(project: str) -> dict:
    """Filtros para MAF masked somatic mutations (acceso abierto)."""
    return {
        "op": "and",
        "content": [
            {"op": "=", "content": {"field": "cases.project.project_id", "value": project}},
            {"op": "=", "content": {"field": "data_category", "value": "Simple Nucleotide Variation"}},
            {"op": "=", "content": {"field": "data_type", "value": "Masked Somatic Mutation"}},
            {"op": "=", "content": {"field": "data_format", "value": "MAF"}},
            {"op": "=", "content": {"field": "access", "value": "open"}},
        ],
    }
