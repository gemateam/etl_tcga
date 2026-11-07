"""E04: aplanado de casos y archivos JSON a Parquet."""

from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone
from typing import Any

import pyarrow as pa

from ..common import read_json
from ..config import Settings, get_settings
from ..db import connect
from ..duck import write_parquet
from ..logging_utils import get_logger
from ..ops.runs import pipeline_step, record_dq, recorded_release

log = get_logger(__name__)

META = [
    pa.field("_ingested_at", pa.timestamp("us", tz="UTC")),
    pa.field("_source_file", pa.string()),
    pa.field("_gdc_data_release", pa.string()),
]


def _meta() -> dict[str, Any]:
    return {
        "_ingested_at": datetime.now(timezone.utc),
        "_gdc_data_release": None,
    }


CASES_SCHEMA = pa.schema(
    [
        pa.field("case_id", pa.string()),
        pa.field("submitter_id", pa.string()),
        pa.field("project_id", pa.string()),
        pa.field("project_name", pa.string()),
        pa.field("primary_site", pa.string()),
        pa.field("disease_type", pa.string()),
        pa.field("consent_type", pa.string()),
        pa.field("demographic_id", pa.string()),
        pa.field("sex_at_birth", pa.string()),
        pa.field("race", pa.string()),
        pa.field("ethnicity", pa.string()),
        pa.field("vital_status", pa.string()),
        pa.field("days_to_death", pa.int32()),
        pa.field("days_to_birth", pa.int32()),
        pa.field("age_at_index", pa.int32()),
        pa.field("year_of_birth", pa.int32()),
        pa.field("year_of_death", pa.int32()),
        pa.field("cause_of_death", pa.string()),
        pa.field("marital_status", pa.string()),
        pa.field("education_level", pa.string()),
        pa.field("country_of_residence_at_enrollment", pa.string()),
        pa.field("state", pa.string()),
        pa.field("n_diagnoses", pa.int32()),
        pa.field("n_exposures", pa.int32()),
        pa.field("n_samples", pa.int32()),
        pa.field("primary_diagnosis", pa.string()),
        pa.field("ajcc_pathologic_stage", pa.string()),
    ]
    + META
)

DIAGNOSES_SCHEMA = pa.schema(
    [
        pa.field("diagnosis_id", pa.string()),
        pa.field("case_id", pa.string()),
        pa.field("submitter_id", pa.string()),
        pa.field("project_id", pa.string()),
        pa.field("primary_diagnosis", pa.string()),
        pa.field("tissue_or_organ_of_origin", pa.string()),
        pa.field("classification_of_tumor", pa.string()),
        pa.field("tumor_grade", pa.string()),
        pa.field("ajcc_pathologic_stage", pa.string()),
        pa.field("ajcc_pathologic_t", pa.string()),
        pa.field("ajcc_pathologic_n", pa.string()),
        pa.field("ajcc_pathologic_m", pa.string()),
        pa.field("ajcc_clinical_stage", pa.string()),
        pa.field("age_at_diagnosis", pa.int32()),
        pa.field("days_to_diagnosis", pa.int32()),
        pa.field("days_to_last_follow_up", pa.int32()),
        pa.field("last_known_disease_status", pa.string()),
        pa.field("morphology", pa.string()),
        pa.field("laterality", pa.string()),
        pa.field("site_of_resection_or_biopsy", pa.string()),
        pa.field("year_of_diagnosis", pa.int32()),
        pa.field("days_to_recurrence", pa.int32()),
        pa.field("progression_or_recurrence", pa.string()),
        pa.field("diagnosis_is_primary_disease", pa.bool_()),
        pa.field("treatments", pa.string()),
    ]
    + META
)

EXPOSURES_SCHEMA = pa.schema(
    [
        pa.field("exposure_id", pa.string()),
        pa.field("case_id", pa.string()),
        pa.field("submitter_id", pa.string()),
        pa.field("project_id", pa.string()),
        pa.field("alcohol_history", pa.string()),
        pa.field("alcohol_intensity", pa.string()),
        pa.field("alcohol_drinks_per_day", pa.float64()),
        pa.field("cigarettes_per_day", pa.float64()),
        pa.field("pack_years_smoked", pa.float64()),
        pa.field("smoking_frequency", pa.string()),
        pa.field("tobacco_smoking_status", pa.string()),
        pa.field("tobacco_smoking_onset_year", pa.int32()),
        pa.field("tobacco_smoking_quit_year", pa.int32()),
        pa.field("type_of_tobacco_used", pa.string()),
        pa.field("exposure_duration_years", pa.float64()),
        pa.field("asbestos_exposure_type", pa.string()),
        pa.field("environmental_tobacco_smoke_exposure", pa.string()),
        pa.field("secondhand_smoke_as_child", pa.string()),
    ]
    + META
)

SAMPLES_SCHEMA = pa.schema(
    [
        pa.field("sample_id", pa.string()),
        pa.field("case_id", pa.string()),
        pa.field("submitter_id", pa.string()),
        pa.field("project_id", pa.string()),
        pa.field("sample_type", pa.string()),
        pa.field("tissue_type", pa.string()),
        pa.field("tumor_descriptor", pa.string()),
        pa.field("specimen_type", pa.string()),
        pa.field("biospecimen_anatomic_site", pa.string()),
        pa.field("preservation_method", pa.string()),
        pa.field("days_to_collection", pa.int32()),
    ]
    + META
)

FILES_SCHEMA = pa.schema(
    [
        pa.field("file_id", pa.string()),
        pa.field("file_name", pa.string()),
        pa.field("file_size", pa.int64()),
        pa.field("md5sum", pa.string()),
        pa.field("access", pa.string()),
        pa.field("data_category", pa.string()),
        pa.field("data_type", pa.string()),
        pa.field("data_format", pa.string()),
        pa.field("experimental_strategy", pa.string()),
        pa.field("workflow_type", pa.string()),
        pa.field("case_id", pa.string()),
        pa.field("project_id", pa.string()),
        pa.field("sample_id", pa.string()),
        pa.field("sample_type", pa.string()),
        pa.field("case_ids", pa.string()),
        pa.field("sample_ids", pa.string()),
    ]
    + META
)


def _int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _first(value: Any) -> dict[str, Any]:
    if isinstance(value, list):
        return value[0] if value else {}
    return value or {}


def _clean(value: Any) -> Any:
    if isinstance(value, list):
        return ",".join(str(v) for v in value if v is not None) or None
    return value


def bronze_cases(projects: list[str], settings: Settings | None = None) -> dict[str, int]:
    settings = settings or get_settings()
    result: dict[str, int] = {}
    for project in projects:
        with pipeline_step("bronze_cases", project) as run:
            import json as _json

            page_files = sorted(settings.landing_cases_dir(project).glob("page_*.json"))
            if not page_files:
                raise FileNotFoundError(
                    f"No hay páginas de casos en {settings.landing_cases_dir(project)}"
                )
            recorded = recorded_release()
            case_rows: list[dict[str, Any]] = []
            diag_rows: list[dict[str, Any]] = []
            expo_rows: list[dict[str, Any]] = []
            sample_rows: list[dict[str, Any]] = []
            raw_rows: list[tuple] = []
            seen: set[str] = set()
            release = None

            for page_file in page_files:
                payload = read_json(page_file)
                release = release or recorded
                for hit in payload.get("data", {}).get("hits", []):
                    case_id = hit.get("case_id")
                    if not case_id:
                        continue
                    seen.add(case_id)
                    meta = _meta()
                    meta["_source_file"] = page_file.name
                    meta["_gdc_data_release"] = release
                    demo = _first(hit.get("demographic"))
                    diagnoses = hit.get("diagnoses") or []
                    exposures = hit.get("exposures") or []
                    samples = hit.get("samples") or []
                    project_hit = _first(hit.get("project"))
                    project_id = project_hit.get("project_id") or project
                    first_dx = diagnoses[0] if diagnoses else {}
                    case_rows.append(
                        {
                            "case_id": case_id,
                            "submitter_id": hit.get("submitter_id"),
                            "project_id": project_id,
                            "project_name": project_hit.get("name"),
                            "primary_site": _clean(hit.get("primary_site")),
                            "disease_type": _clean(hit.get("disease_type")),
                            "consent_type": hit.get("consent_type"),
                            "demographic_id": demo.get("demographic_id"),
                            "sex_at_birth": demo.get("sex_at_birth"),
                            "race": demo.get("race"),
                            "ethnicity": demo.get("ethnicity"),
                            "vital_status": demo.get("vital_status"),
                            "days_to_death": _int(demo.get("days_to_death")),
                            "days_to_birth": _int(demo.get("days_to_birth")),
                            "age_at_index": _int(demo.get("age_at_index")),
                            "year_of_birth": _int(demo.get("year_of_birth")),
                            "year_of_death": _int(demo.get("year_of_death")),
                            "cause_of_death": demo.get("cause_of_death"),
                            "marital_status": demo.get("marital_status"),
                            "education_level": demo.get("education_level"),
                            "country_of_residence_at_enrollment": demo.get(
                                "country_of_residence_at_enrollment"
                            ),
                            "state": demo.get("state"),
                            "n_diagnoses": len(diagnoses),
                            "n_exposures": len(exposures),
                            "n_samples": len(samples),
                            "primary_diagnosis": first_dx.get("primary_diagnosis"),
                            "ajcc_pathologic_stage": first_dx.get("ajcc_pathologic_stage"),
                            **meta,
                        }
                    )
                    raw_rows.append(
                        (
                            case_id,
                            project_id,
                            hit.get("submitter_id"),
                            json.dumps(hit, ensure_ascii=False),
                            page_file.name,
                            release,
                        )
                    )
                    for index, dx in enumerate(diagnoses):
                        diag_rows.append(
                            {
                                "diagnosis_id": dx.get("diagnosis_id") or f"{case_id}-dx{index}",
                                "case_id": case_id,
                                "submitter_id": dx.get("submitter_id"),
                                "project_id": project_id,
                                "primary_diagnosis": dx.get("primary_diagnosis"),
                                "tissue_or_organ_of_origin": dx.get("tissue_or_organ_of_origin"),
                                "classification_of_tumor": dx.get("classification_of_tumor"),
                                "tumor_grade": dx.get("tumor_grade"),
                                "ajcc_pathologic_stage": dx.get("ajcc_pathologic_stage"),
                                "ajcc_pathologic_t": dx.get("ajcc_pathologic_t"),
                                "ajcc_pathologic_n": dx.get("ajcc_pathologic_n"),
                                "ajcc_pathologic_m": dx.get("ajcc_pathologic_m"),
                                "ajcc_clinical_stage": dx.get("ajcc_clinical_stage"),
                                "age_at_diagnosis": _int(dx.get("age_at_diagnosis")),
                                "days_to_diagnosis": _int(dx.get("days_to_diagnosis")),
                                "days_to_last_follow_up": _int(dx.get("days_to_last_follow_up")),
                                "last_known_disease_status": dx.get("last_known_disease_status"),
                                "morphology": dx.get("morphology"),
                                "laterality": dx.get("laterality"),
                                "site_of_resection_or_biopsy": dx.get("site_of_resection_or_biopsy"),
                                "year_of_diagnosis": _int(dx.get("year_of_diagnosis")),
                                "days_to_recurrence": _int(dx.get("days_to_recurrence")),
                                "progression_or_recurrence": dx.get("progression_or_recurrence"),
                                "diagnosis_is_primary_disease": dx.get("diagnosis_is_primary_disease"),
                                "treatments": _json.dumps(dx.get("treatments") or []),
                                **meta,
                            }
                        )
                    for index, ex in enumerate(exposures):
                        expo_rows.append(
                            {
                                "exposure_id": ex.get("exposure_id") or f"{case_id}-ex{index}",
                                "case_id": case_id,
                                "submitter_id": ex.get("submitter_id"),
                                "project_id": project_id,
                                "alcohol_history": ex.get("alcohol_history"),
                                "alcohol_intensity": ex.get("alcohol_intensity"),
                                "alcohol_drinks_per_day": ex.get("alcohol_drinks_per_day"),
                                "cigarettes_per_day": ex.get("cigarettes_per_day"),
                                "pack_years_smoked": ex.get("pack_years_smoked"),
                                "smoking_frequency": ex.get("smoking_frequency"),
                                "tobacco_smoking_status": ex.get("tobacco_smoking_status"),
                                "tobacco_smoking_onset_year": _int(ex.get("tobacco_smoking_onset_year")),
                                "tobacco_smoking_quit_year": _int(ex.get("tobacco_smoking_quit_year")),
                                "type_of_tobacco_used": ex.get("type_of_tobacco_used"),
                                "exposure_duration_years": ex.get("exposure_duration_years"),
                                "asbestos_exposure_type": ex.get("asbestos_exposure_type"),
                                "environmental_tobacco_smoke_exposure": ex.get(
                                    "environmental_tobacco_smoke_exposure"
                                ),
                                "secondhand_smoke_as_child": ex.get("secondhand_smoke_as_child"),
                                **meta,
                            }
                        )
                    for sample in samples:
                        sample_rows.append(
                            {
                                "sample_id": sample.get("sample_id"),
                                "case_id": case_id,
                                "submitter_id": sample.get("submitter_id"),
                                "project_id": project_id,
                                "sample_type": sample.get("sample_type"),
                                "tissue_type": sample.get("tissue_type"),
                                "tumor_descriptor": sample.get("tumor_descriptor"),
                                "specimen_type": sample.get("specimen_type"),
                                "biospecimen_anatomic_site": sample.get("biospecimen_anatomic_site"),
                                "preservation_method": sample.get("preservation_method"),
                                "days_to_collection": _int(sample.get("days_to_collection")),
                                **meta,
                            }
                        )

            out = settings.bronze_root() / "cases" / project
            shutil.rmtree(out, ignore_errors=True)
            write_parquet(out / "cases.parquet", case_rows, CASES_SCHEMA)
            write_parquet(out / "cases_diagnoses.parquet", diag_rows, DIAGNOSES_SCHEMA)
            write_parquet(out / "cases_exposures.parquet", expo_rows, EXPOSURES_SCHEMA)
            write_parquet(out / "cases_samples.parquet", sample_rows, SAMPLES_SCHEMA)

            with connect() as conn:
                conn.execute("DELETE FROM bronze.cases_raw WHERE project_id = %s", (project,))
                with conn.cursor() as cur:
                    with cur.copy(
                        "COPY bronze.cases_raw (case_id, project_id, submitter_id, payload, "
                        "_source_file, _gdc_data_release) FROM STDIN"
                    ) as copy:
                        for row in raw_rows:
                            copy.write_row(row)
            run.count(rows_in=len(case_rows), rows_out=len(case_rows))
            record_dq(
                run.run_id,
                run.step,
                project,
                "cases_bronze_unique",
                "critical",
                len(case_rows) == len(seen),
                f"filas={len(case_rows)} únicos={len(seen)}",
            )
            record_dq(
                run.run_id,
                run.step,
                project,
                "cases_bronze_not_empty",
                "critical",
                len(case_rows) > 0,
                f"filas={len(case_rows)}",
            )
            result[project] = len(case_rows)
            log.info(
                "Bronze de casos",
                extra={
                    "event": "bronze_cases",
                    "rows": len(case_rows),
                    "detail": f"diagnósticos={len(diag_rows)} exposiciones={len(expo_rows)} muestras={len(sample_rows)}",
                },
            )
    return result


def bronze_files(projects: list[str], settings: Settings | None = None) -> dict[str, int]:
    settings = settings or get_settings()
    result: dict[str, int] = {}
    for project in projects:
        from ..landing.files import read_manifest

        manifest = read_manifest(project, settings)
        with pipeline_step("bronze_files", project) as run:
            rows: list[dict[str, Any]] = []
            seen: set[str] = set()
            for hit in manifest.get("hits", []):
                file_id = hit.get("file_id")
                if not file_id:
                    continue
                seen.add(file_id)
                cases = hit.get("cases") or []
                case = cases[0] if cases else {}
                samples = (case.get("samples") or []) if case else []
                sample = samples[0] if samples else {}
                case_ids = sorted({c.get("case_id") for c in cases if c.get("case_id")})
                sample_ids = sorted(
                    {
                        s.get("sample_id")
                        for c in cases
                        for s in (c.get("samples") or [])
                        if s.get("sample_id")
                    }
                )
                meta = _meta()
                meta["_source_file"] = "manifest.json"
                meta["_gdc_data_release"] = manifest.get("data_release")
                rows.append(
                    {
                        "file_id": file_id,
                        "file_name": hit.get("file_name"),
                        "file_size": hit.get("file_size"),
                        "md5sum": hit.get("md5sum"),
                        "access": hit.get("access"),
                        "data_category": hit.get("data_category"),
                        "data_type": hit.get("data_type"),
                        "data_format": hit.get("data_format"),
                        "experimental_strategy": hit.get("experimental_strategy"),
                        "workflow_type": (hit.get("analysis") or {}).get("workflow_type"),
                        "case_id": case.get("case_id"),
                        "project_id": (
                            (case.get("project") or {}).get("project_id") or project
                        ),
                        "sample_id": sample.get("sample_id"),
                        "sample_type": sample.get("sample_type"),
                        "case_ids": json.dumps(case_ids),
                        "sample_ids": json.dumps(sample_ids),
                        **meta,
                    }
                )
            out = settings.bronze_root() / "files" / project
            shutil.rmtree(out, ignore_errors=True)
            write_parquet(out / "files.parquet", rows, FILES_SCHEMA)
            run.count(rows_in=len(manifest.get("hits", [])), rows_out=len(rows))
            record_dq(
                run.run_id,
                run.step,
                project,
                "files_bronze_unique",
                "critical",
                len(rows) == len(seen),
                f"filas={len(rows)} únicos={len(seen)}",
            )
            record_dq(
                run.run_id,
                run.step,
                project,
                "files_bronze_matches_manifest",
                "warning",
                len(rows) == len(manifest.get("hits", [])),
                f"filas={len(rows)} manifiesto={len(manifest.get('hits', []))}",
            )
            result[project] = len(rows)
            log.info(
                "Bronze de archivos",
                extra={"event": "bronze_files", "rows": len(rows)},
            )
    return result
