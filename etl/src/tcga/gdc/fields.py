"""Campos del GDC utilizados por el pipeline.

Todos los nombres fueron verificados contra:
  GET https://api.gdc.cancer.gov/cases/_mapping
  GET https://api.gdc.cancer.gov/files/_mapping
No se usan campos que no aparezcan en esos mappings.
"""

from __future__ import annotations

# Campos de /cases. Se piden arrays anidados completos para demografía,
# diagnósticos (con tratamientos), exposiciones y muestras.
CASE_FIELDS: tuple[str, ...] = (
    "case_id",
    "submitter_id",
    "primary_site",
    "disease_type",
    "consent_type",
    "project.project_id",
    "project.name",
    "project.primary_site",
    "project.disease_type",
    "demographic.demographic_id",
    "demographic.sex_at_birth",
    "demographic.race",
    "demographic.ethnicity",
    "demographic.vital_status",
    "demographic.days_to_death",
    "demographic.days_to_birth",
    "demographic.age_at_index",
    "demographic.year_of_birth",
    "demographic.year_of_death",
    "demographic.cause_of_death",
    "demographic.marital_status",
    "demographic.education_level",
    "demographic.country_of_residence_at_enrollment",
    "demographic.state",
    "demographic.updated_datetime",
    "diagnoses.diagnosis_id",
    "diagnoses.submitter_id",
    "diagnoses.primary_diagnosis",
    "diagnoses.tissue_or_organ_of_origin",
    "diagnoses.classification_of_tumor",
    "diagnoses.tumor_grade",
    "diagnoses.ajcc_pathologic_stage",
    "diagnoses.ajcc_pathologic_t",
    "diagnoses.ajcc_pathologic_n",
    "diagnoses.ajcc_pathologic_m",
    "diagnoses.ajcc_clinical_stage",
    "diagnoses.age_at_diagnosis",
    "diagnoses.days_to_diagnosis",
    "diagnoses.days_to_last_follow_up",
    "diagnoses.last_known_disease_status",
    "diagnoses.morphology",
    "diagnoses.laterality",
    "diagnoses.site_of_resection_or_biopsy",
    "diagnoses.year_of_diagnosis",
    "diagnoses.days_to_recurrence",
    "diagnoses.progression_or_recurrence",
    "diagnoses.diagnosis_is_primary_disease",
    "diagnoses.treatments.treatment_id",
    "diagnoses.treatments.therapeutic_agents",
    "diagnoses.treatments.treatment_type",
    "diagnoses.treatments.treatment_or_therapy",
    "diagnoses.treatments.days_to_treatment_start",
    "diagnoses.treatments.days_to_treatment_end",
    "diagnoses.treatments.regimen_or_line_of_therapy",
    "exposures.exposure_id",
    "exposures.submitter_id",
    "exposures.alcohol_history",
    "exposures.alcohol_intensity",
    "exposures.alcohol_drinks_per_day",
    "exposures.cigarettes_per_day",
    "exposures.pack_years_smoked",
    "exposures.smoking_frequency",
    "exposures.tobacco_smoking_status",
    "exposures.tobacco_smoking_onset_year",
    "exposures.tobacco_smoking_quit_year",
    "exposures.type_of_tobacco_used",
    "exposures.exposure_duration_years",
    "exposures.asbestos_exposure_type",
    "exposures.environmental_tobacco_smoke_exposure",
    "exposures.secondhand_smoke_as_child",
    "samples.sample_id",
    "samples.submitter_id",
    "samples.sample_type",
    "samples.tissue_type",
    "samples.tumor_descriptor",
    "samples.specimen_type",
    "samples.biospecimen_anatomic_site",
    "samples.preservation_method",
    "samples.days_to_collection",
)

# Campos de /files para el manifiesto de expresión génica.
FILE_FIELDS: tuple[str, ...] = (
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
    "cases.samples.sample_id",
    "cases.samples.submitter_id",
    "cases.samples.sample_type",
)

EXPRESSION_COLUMNS: tuple[str, ...] = (
    "gene_id",
    "gene_name",
    "gene_type",
    "unstranded",
    "stranded_first",
    "stranded_second",
    "tpm_unstranded",
    "fpkm_unstranded",
    "fpkm_uq_unstranded",
)

EXPECTED_GENES_PER_FILE = 60_660


def expression_filters(project: str) -> dict:
    """Filtros exactos de la sección de decisiones del proyecto."""
    return {
        "op": "and",
        "content": [
            {"op": "=", "content": {"field": "cases.project.project_id", "value": project}},
            {"op": "=", "content": {"field": "data_category", "value": "Transcriptome Profiling"}},
            {"op": "=", "content": {"field": "data_type", "value": "Gene Expression Quantification"}},
            {"op": "=", "content": {"field": "analysis.workflow_type", "value": "STAR - Counts"}},
            {"op": "=", "content": {"field": "access", "value": "open"}},
        ],
    }


def case_filters(project: str) -> dict:
    return {
        "op": "=",
        "content": {"field": "project.project_id", "value": project},
    }
