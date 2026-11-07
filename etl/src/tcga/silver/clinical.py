"""S02: tablas clínicas normalizadas en el esquema silver."""

from __future__ import annotations

from typing import Any, Sequence

from ..common import clean_text, safe_float, safe_int
from ..config import Settings, get_settings
from ..db import connect, copy_rows
from ..duck import duck_connect, sql_literal
from ..logging_utils import get_logger
from ..ops.runs import pipeline_step, record_dq, recorded_release

log = get_logger(__name__)

CASES_COLS = (
    "case_id",
    "submitter_id",
    "project_id",
    "project_name",
    "primary_site",
    "disease_type",
    "consent_type",
    "_gdc_data_release",
)

DEMO_COLS = (
    "case_id",
    "demographic_id",
    "sex_at_birth",
    "race",
    "ethnicity",
    "vital_status",
    "days_to_death",
    "days_to_birth",
    "age_at_index",
    "year_of_birth",
    "year_of_death",
    "cause_of_death",
    "marital_status",
    "education_level",
    "country_of_residence_at_enrollment",
    "state",
    "_gdc_data_release",
)

DIAG_COLS = (
    "diagnosis_id",
    "case_id",
    "submitter_id",
    "primary_diagnosis",
    "tissue_or_organ_of_origin",
    "classification_of_tumor",
    "tumor_grade",
    "ajcc_pathologic_stage",
    "ajcc_pathologic_t",
    "ajcc_pathologic_n",
    "ajcc_pathologic_m",
    "ajcc_clinical_stage",
    "age_at_diagnosis",
    "days_to_diagnosis",
    "days_to_last_follow_up",
    "last_known_disease_status",
    "morphology",
    "laterality",
    "site_of_resection_or_biopsy",
    "year_of_diagnosis",
    "days_to_recurrence",
    "progression_or_recurrence",
    "diagnosis_is_primary_disease",
    "treatments",
    "_gdc_data_release",
)

EXPO_COLS = (
    "exposure_id",
    "case_id",
    "submitter_id",
    "alcohol_history",
    "alcohol_intensity",
    "alcohol_drinks_per_day",
    "cigarettes_per_day",
    "pack_years_smoked",
    "smoking_frequency",
    "tobacco_smoking_status",
    "tobacco_smoking_onset_year",
    "tobacco_smoking_quit_year",
    "type_of_tobacco_used",
    "exposure_duration_years",
    "asbestos_exposure_type",
    "environmental_tobacco_smoke_exposure",
    "secondhand_smoke_as_child",
    "_gdc_data_release",
)

SAMPLE_COLS = (
    "sample_id",
    "case_id",
    "submitter_id",
    "sample_type",
    "tissue_type",
    "tumor_descriptor",
    "specimen_type",
    "biospecimen_anatomic_site",
    "preservation_method",
    "days_to_collection",
    "_gdc_data_release",
)

FILES_COLS = (
    "file_id",
    "case_id",
    "sample_id",
    "sample_type",
    "project_id",
    "file_name",
    "file_size",
    "md5sum",
    "access",
    "data_category",
    "data_type",
    "data_format",
    "experimental_strategy",
    "workflow_type",
    "_gdc_data_release",
)

TEXT_FIELDS = {
    "case_id",
    "submitter_id",
    "project_id",
    "project_name",
    "primary_site",
    "disease_type",
    "consent_type",
    "demographic_id",
    "sex_at_birth",
    "race",
    "ethnicity",
    "vital_status",
    "cause_of_death",
    "marital_status",
    "education_level",
    "country_of_residence_at_enrollment",
    "state",
    "diagnosis_id",
    "primary_diagnosis",
    "tissue_or_organ_of_origin",
    "classification_of_tumor",
    "tumor_grade",
    "ajcc_pathologic_stage",
    "ajcc_pathologic_t",
    "ajcc_pathologic_n",
    "ajcc_pathologic_m",
    "ajcc_clinical_stage",
    "last_known_disease_status",
    "morphology",
    "laterality",
    "site_of_resection_or_biopsy",
    "progression_or_recurrence",
    "exposure_id",
    "alcohol_history",
    "alcohol_intensity",
    "smoking_frequency",
    "tobacco_smoking_status",
    "type_of_tobacco_used",
    "asbestos_exposure_type",
    "environmental_tobacco_smoke_exposure",
    "secondhand_smoke_as_child",
    "sample_id",
    "sample_type",
    "tissue_type",
    "tumor_descriptor",
    "specimen_type",
    "biospecimen_anatomic_site",
    "preservation_method",
    "file_id",
    "file_name",
    "md5sum",
    "access",
    "data_category",
    "data_type",
    "data_format",
    "experimental_strategy",
    "workflow_type",
    "_gdc_data_release",
}

INT_FIELDS = {
    "days_to_death",
    "days_to_birth",
    "age_at_index",
    "year_of_birth",
    "year_of_death",
    "age_at_diagnosis",
    "days_to_diagnosis",
    "days_to_last_follow_up",
    "year_of_diagnosis",
    "days_to_recurrence",
    "tobacco_smoking_onset_year",
    "tobacco_smoking_quit_year",
    "days_to_collection",
    "file_size",
}

FLOAT_FIELDS = {
    "alcohol_drinks_per_day",
    "cigarettes_per_day",
    "pack_years_smoked",
    "exposure_duration_years",
}


def _normalize(value: Any, column: str) -> Any:
    if column in TEXT_FIELDS:
        return clean_text(value)
    if column in INT_FIELDS:
        return safe_int(value)
    if column in FLOAT_FIELDS:
        return safe_float(value)
    if column == "diagnosis_is_primary_disease":
        return value if isinstance(value, bool) else None
    if column == "treatments":
        return value or "[]"
    return value


def _tuples(rows: Sequence[dict[str, Any]], columns: Sequence[str]) -> list[tuple]:
    return [
        tuple(_normalize(row.get(col), col) for col in columns)
        for row in rows
    ]


def _load(conn: Any, table: str, columns: Sequence[str], rows: Sequence[dict[str, Any]]) -> int:
    if not rows:
        return 0
    return copy_rows(conn, table, columns, _tuples(rows, columns))


def silver_clinical(projects: list[str], settings: Settings | None = None) -> dict[str, int]:
    settings = settings or get_settings()
    result: dict[str, int] = {}
    cases_glob = (settings.bronze_root() / "cases" / "**" / "cases.parquet").as_posix()
    diag_glob = (settings.bronze_root() / "cases" / "**" / "cases_diagnoses.parquet").as_posix()
    expo_glob = (settings.bronze_root() / "cases" / "**" / "cases_exposures.parquet").as_posix()
    sample_glob = (settings.bronze_root() / "cases" / "**" / "cases_samples.parquet").as_posix()
    files_glob = (settings.bronze_root() / "files" / "**" / "files.parquet").as_posix()
    release = recorded_release()

    for project in projects:
        with pipeline_step("silver_clinical", project) as run:
            con = duck_connect(settings)
            try:
                cases = con.execute(
                    f"SELECT * FROM read_parquet({sql_literal(cases_glob)}, union_by_name=true) WHERE project_id = ?",
                    [project],
                ).fetchall()
                case_columns = [d[0] for d in con.description]
                cases = [dict(zip(case_columns, row)) for row in cases]

                def read(glob: str) -> list[dict[str, Any]]:
                    cursor = con.execute(
                        f"SELECT * FROM read_parquet({sql_literal(glob)}, union_by_name=true) WHERE project_id = ?",
                        [project],
                    )
                    cols = [d[0] for d in cursor.description]
                    return [dict(zip(cols, row)) for row in cursor.fetchall()]

                diagnoses = read(diag_glob)
                exposures = read(expo_glob)
                samples = read(sample_glob)
                files = read(files_glob)
            finally:
                con.close()

            if not cases:
                raise FileNotFoundError(
                    f"No hay casos bronze para {project}; ejecuta bronze-cases."
                )

            # Las llaves primarias no admiten NULL: se descartan filas sin llave.
            samples = [row for row in samples if clean_text(row.get("sample_id"))]
            diagnoses = [row for row in diagnoses if clean_text(row.get("diagnosis_id"))]
            exposures = [row for row in exposures if clean_text(row.get("exposure_id"))]
            files = [row for row in files if clean_text(row.get("file_id"))]

            for row in cases:
                row.setdefault("_gdc_data_release", release)

            # Demografía: una fila por caso.
            demo_rows = []
            for row in cases:
                if row.get("demographic_id"):
                    demo_rows.append(row)

            with connect() as conn:
                conn.execute("DELETE FROM silver.files WHERE project_id = %s", (project,))
                conn.execute("DELETE FROM silver.cases WHERE project_id = %s", (project,))
                n_cases = _load(conn, "silver.cases", CASES_COLS, cases)
                n_demo = _load(conn, "silver.cases_demographics", DEMO_COLS, demo_rows)
                n_diag = _load(conn, "silver.cases_diagnoses", DIAG_COLS, diagnoses)
                n_expo = _load(conn, "silver.cases_exposures", EXPO_COLS, exposures)
                n_samples = _load(conn, "silver.samples", SAMPLE_COLS, samples)
                n_files = _load(conn, "silver.files", FILES_COLS, files)

                orphans = conn.execute(
                    """
                    SELECT
                        (SELECT count(*) FROM silver.cases_diagnoses d
                          LEFT JOIN silver.cases c USING (case_id) WHERE c.case_id IS NULL) +
                        (SELECT count(*) FROM silver.cases_exposures e
                          LEFT JOIN silver.cases c USING (case_id) WHERE c.case_id IS NULL) +
                        (SELECT count(*) FROM silver.samples s
                          LEFT JOIN silver.cases c USING (case_id) WHERE c.case_id IS NULL)
                    AS n
                    """
                ).fetchone()["n"]
                missing_demo = conn.execute(
                    """
                    SELECT count(*) AS n FROM silver.cases c
                    LEFT JOIN silver.cases_demographics d USING (case_id)
                    WHERE c.project_id = %s AND d.case_id IS NULL
                    """,
                    (project,),
                ).fetchone()["n"]
                files_without_case = conn.execute(
                    """
                    SELECT count(*) AS n FROM silver.files
                    WHERE project_id = %s AND case_id IS NULL
                    """,
                    (project,),
                ).fetchone()["n"]

            run.count(rows_in=len(cases), rows_out=n_cases)
            record_dq(
                run.run_id,
                run.step,
                project,
                "clinical_referential_integrity",
                "critical",
                orphans == 0,
                f"huérfanos={orphans}",
            )
            record_dq(
                run.run_id,
                run.step,
                project,
                "clinical_cases_not_empty",
                "critical",
                n_cases > 0,
                f"casos={n_cases}",
            )
            record_dq(
                run.run_id,
                run.step,
                project,
                "clinical_cases_without_demographics",
                "warning",
                missing_demo == 0,
                f"casos_sin_demografia={missing_demo}",
            )
            record_dq(
                run.run_id,
                run.step,
                project,
                "clinical_files_without_case",
                "warning",
                files_without_case == 0,
                f"archivos_sin_caso={files_without_case}",
            )
            result[project] = n_cases
            log.info(
                "Silver clínico",
                extra={
                    "event": "silver_clinical",
                    "rows": n_cases,
                    "detail": (
                        f"demografia={n_demo} diagnósticos={n_diag} "
                        f"exposiciones={n_expo} muestras={n_samples} archivos={n_files}"
                    ),
                },
            )
    return result
