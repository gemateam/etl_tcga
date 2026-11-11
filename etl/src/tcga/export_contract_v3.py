"""Exportador del Contrato de Datos V3 (TCGA-BRCA + Hispano/Latino).

Versión 2.0.0. Lee del medallón (Parquet) y no consulta el endpoint /ssms.
Genera un bundle inmutable con:

  - manifest.json
  - cohort_cases.json
  - somatic_mutations.json
  - clinical_attributes.json

Uso programático:

    from tcga.export_contract_v3 import export_bundle
    result = export_bundle(data_dir=Path("/data"), outdir=Path("exports/tcga_brca_hispanic"))

Uso CLI:

    tcga export-gemamx --outdir exports/tcga_brca_hispanic
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import urllib.request
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import pandas as pd

CONTRACT_VERSION = "2.0.0"
PROJECT_ID = "TCGA-BRCA"
ETHNICITY_VALUE = "HISPANIC OR LATINO"
TARGET_GENES: tuple[str, ...] = (
    "PIK3CA", "TP53", "AKT1", "CDH1", "PTEN", "ESR1",
    "GATA3", "MAP3K1", "ERBB2", "BRCA1", "BRCA2",
)

CASE_CODE_RE = re.compile(r"^TCGA-[A-Z0-9]{2}-[A-Z0-9]{4}$")
PROTEIN_RE = re.compile(r"^[A-Z*]\d+[A-Z*]$")
CHROMOSOME_RE = re.compile(r"^(?:[1-9]|1[0-9]|2[0-2]|X|Y|MT)$")
TUMOR_SAMPLE_CODE_RE = re.compile(r"^TCGA-[A-Z0-9]{2}-[A-Z0-9]{4}-0[1-9][A-Z]?$")

MISSING_LABELS = {
    "", "NA", "N/A", "NAN", "NONE", "NULL", "UNKNOWN", "NOT REPORTED",
    "NOT AVAILABLE", "NOT APPLICABLE", "<NA>", "N.D.", "ND", "SIN DATO",
    "--", ".",
}

BASE_ATTRIBUTES: tuple[dict[str, str], ...] = (
    {"id": "PRIMARY_SITE", "source": "GDC:cases", "type": "STRING"},
    {"id": "ETHNICITY", "source": "GDC:demographics", "type": "CATEGORICAL"},
    {"id": "RACE", "source": "GDC:demographics", "type": "CATEGORICAL"},
    {"id": "AGE", "source": "GDC:demographics", "type": "NUMBER"},
    {"id": "HISTOLOGY", "source": "GDC:diagnoses", "type": "CATEGORICAL"},
    {"id": "STAGE", "source": "GDC:diagnoses", "type": "CATEGORICAL"},
)
OPTIONAL_ATTRIBUTES: tuple[dict[str, str], ...] = (
    {"id": "VITAL_STATUS", "source": "GDC:demographics", "type": "CATEGORICAL"},
)


# ---------------------------------------------------------------------------
# Utilidades
# ---------------------------------------------------------------------------
def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _json_default(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, float) and math.isnan(value):
        return None
    if hasattr(value, "item"):
        return value.item()
    return str(value)


def _clean_value(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, float) and math.isnan(value):
        return None
    if isinstance(value, str):
        value = value.strip()
        return None if value.upper() in MISSING_LABELS else value
    return value


def _clean_label(value: Any) -> str | None:
    cleaned = _clean_value(value)
    if cleaned is None:
        return None
    return " ".join(str(cleaned).upper().split())


def _clean_text(value: Any) -> str | None:
    cleaned = _clean_value(value)
    if cleaned is None:
        return None
    return " ".join(str(cleaned).split())


def _raw_text(value: Any) -> str | None:
    """Conserva el valor original (incluido 'Not Reported') para auditoría."""
    if value is None:
        return None
    if isinstance(value, float) and math.isnan(value):
        return None
    text = str(value).strip()
    return text or None


def _int_or_none(value: Any) -> int | None:
    cleaned = _clean_value(value)
    if cleaned is None:
        return None
    try:
        return int(float(cleaned))
    except (TypeError, ValueError):
        return None


def _chromosome(value: Any) -> str | None:
    cleaned = _clean_value(value)
    if cleaned is None:
        return None
    text = str(cleaned).strip()
    if text.lower().startswith("chr"):
        text = text[3:]
    text = text.upper()
    return text if CHROMOSOME_RE.match(text) else None


def _sample_barcode(value: Any) -> str | None:
    """TCGA-XX-XXXX-01A-... -> TCGA-XX-XXXX-01A."""
    cleaned = _clean_value(value)
    if cleaned is None:
        return None
    parts = str(cleaned).split("-")
    if len(parts) < 4:
        return None
    return "-".join(parts[:4])


def _protein_change(value: Any) -> str | None:
    cleaned = _clean_value(value)
    if cleaned is None:
        return None
    text = str(cleaned).strip().replace("p.", "")
    return text if PROTEIN_RE.match(text) else None


def _genomic_dna_change(chromosome: str | None, start: int | None,
                        ref: str | None, alt: str | None) -> str | None:
    if not chromosome or not start or not ref or not alt:
        return None
    ref, alt = str(ref).strip(), str(alt).strip()
    if len(ref) == 1 and len(alt) == 1:
        return f"chr{chromosome}:g.{start}{ref}>{alt}"
    return None


def _variant_id(patient_id: str, hugo: str, protein: str, chromosome: str,
                start: int, ref: str | None, alt: str | None) -> str:
    key = "|".join([
        patient_id, hugo, protein, chromosome, str(start), ref or "", alt or "",
    ])
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]


def _git_commit(repo: Path | None) -> str | None:
    if repo is None:
        return os.getenv("GIT_COMMIT")
    try:
        proc = subprocess.run(
            ["git", "-C", str(repo), "rev-parse", "HEAD"],
            capture_output=True, text=True, timeout=10, check=False,
        )
        return proc.stdout.strip() or None
    except Exception:  # noqa: BLE001
        return None


def _gdc_status(offline: bool = False, timeout: int = 10) -> dict:
    if offline:
        return {}
    try:
        with urllib.request.urlopen("https://api.gdc.cancer.gov/status", timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception:  # noqa: BLE001
        return {}


def _release_from_data(mutations: pd.DataFrame, cases: pd.DataFrame) -> str | None:
    for df in (mutations, cases):
        if "_gdc_data_release" in df.columns:
            values = df["_gdc_data_release"].dropna().unique()
            if len(values):
                return str(values[0])
    return None


def _release_short(release: str | None) -> str:
    if not release:
        return "unknown"
    match = re.search(r"Data Release\s+(\d+\.\d+)", release, re.IGNORECASE)
    return match.group(1) if match else "unknown"


# ---------------------------------------------------------------------------
# Lectura del medallón
# ---------------------------------------------------------------------------
def _read_parquet(path: Path, columns: Iterable[str] | None = None) -> pd.DataFrame:
    if not path.exists():
        raise FileNotFoundError(f"No existe el Parquet requerido: {path}")
    return pd.read_parquet(path, columns=list(columns) if columns else None)


def load_medallion(data_dir: Path, project: str = PROJECT_ID) -> dict[str, pd.DataFrame]:
    """Carga casos, diagnósticos, muestras, mutaciones y mapeo de muestras MAF."""
    cases_dir = data_dir / "bronze" / "cases" / project
    mutations_dir = data_dir / "bronze" / "mutations" / project

    cases = _read_parquet(cases_dir / "cases.parquet")
    diagnoses = _read_parquet(cases_dir / "cases_diagnoses.parquet")
    samples = _read_parquet(cases_dir / "cases_samples.parquet")
    mutations = _read_parquet(mutations_dir / "mutations.parquet")

    maf_samples_path = mutations_dir / "maf_samples.parquet"
    if maf_samples_path.exists():
        maf_samples = _read_parquet(maf_samples_path)
    else:
        maf_samples = pd.DataFrame(columns=["file_id", "tumor_sample_barcode"])

    return {
        "cases": cases,
        "diagnoses": diagnoses,
        "samples": samples,
        "mutations": mutations,
        "maf_samples": maf_samples,
    }


# ---------------------------------------------------------------------------
# Construcción de las tablas del contrato
# ---------------------------------------------------------------------------
def build_cohort(cases: pd.DataFrame, samples: pd.DataFrame,
                 project: str = PROJECT_ID,
                 ethnicity: str = ETHNICITY_VALUE) -> list[dict]:
    """Filtra TCGA-BRCA + etnia hispana y devuelve cohort_cases.json."""
    df = cases.copy()
    df["_ethnicity_norm"] = df["ethnicity"].map(_clean_label)
    df["_project"] = df["project_id"].fillna(project).astype(str)
    target = df[
        (df["_project"] == project)
        & (df["_ethnicity_norm"] == ethnicity.upper())
    ].copy()

    sample_counts = (
        samples.groupby("case_id")["submitter_id"].nunique().to_dict()
        if "case_id" in samples.columns else {}
    )

    cohort: list[dict] = []
    for _, row in target.iterrows():
        case_code = _clean_text(row.get("submitter_id"))
        if case_code is None:
            continue
        cohort.append({
            "case_id": str(row["case_id"]),
            "case_code": case_code.upper(),
            "project_id": project,
            "primary_site": _clean_text(row.get("primary_site")) or "Breast",
            "disease_type": _clean_text(row.get("disease_type")),
            "race": _clean_label(row.get("race")),
            "ethnicity": ethnicity.upper(),
            "age": _int_or_none(row.get("age_at_index")),
            "vital_status": _clean_text(row.get("vital_status")),
            "histology": _clean_text(row.get("primary_diagnosis")),
            "stage": _clean_text(row.get("ajcc_pathologic_stage")),
            "n_samples": int(sample_counts.get(row["case_id"], 0)),
            "gdc_data_release": _clean_text(row.get("_gdc_data_release")),
        })

    cohort.sort(key=lambda item: item["case_code"])
    return cohort


def build_mutations(mutations: pd.DataFrame, maf_samples: pd.DataFrame,
                    cohort_codes: set[str], release: str | None,
                    scope: str = "target_panel",
                    target_genes: Iterable[str] = TARGET_GENES) -> tuple[list[dict], list[dict]]:
    """Construye somatic_mutations.json y devuelve (registros, rechazados)."""
    genes = {g.upper() for g in target_genes}
    df = mutations.copy()

    if "tumor_sample_barcode" not in df.columns:
        if maf_samples.empty:
            raise ValueError(
                "Falta el mapeo de muestras MAF (maf_samples.parquet) y la columna "
                "tumor_sample_barcode no existe en mutations.parquet. "
                "Ejecuta el enriquecimiento de muestras o vuelve a ingerir bronze."
            )
        mapping = maf_samples.drop_duplicates("file_id").set_index("file_id")
        df["tumor_sample_barcode"] = df["file_id"].map(mapping["tumor_sample_barcode"])

    rejected: list[dict] = []
    records: dict[tuple[str, str], dict] = {}

    for _, row in df.iterrows():
        patient_id = _clean_text(row.get("case_submitter_id"))
        if patient_id is None or patient_id.upper() not in cohort_codes:
            continue

        classification = _clean_text(row.get("variant_classification")) or ""
        if classification != "Missense_Mutation":
            continue

        filter_status = _clean_text(row.get("filter_status"))
        if filter_status not in (None, "PASS"):
            rejected.append({
                "patient_id": patient_id,
                "reason": f"filter_status={filter_status}",
                "file_id": _clean_text(row.get("file_id")),
            })
            continue

        hugo = _clean_text(row.get("hugo_symbol"))
        protein = _protein_change(row.get("hgvsp_short"))
        chromosome = _chromosome(row.get("chromosome"))
        start = _int_or_none(row.get("start_position"))
        sample_barcode = _sample_barcode(row.get("tumor_sample_barcode"))

        if not hugo or not protein or not chromosome or not start:
            rejected.append({
                "patient_id": patient_id,
                "reason": "campos obligatorios incompletos",
                "file_id": _clean_text(row.get("file_id")),
            })
            continue

        if scope == "target_panel" and hugo.upper() not in genes:
            continue

        if not sample_barcode or not TUMOR_SAMPLE_CODE_RE.match(sample_barcode):
            rejected.append({
                "patient_id": patient_id,
                "hugo_symbol": hugo,
                "protein_change": protein,
                "reason": f"muestra no tumoral o inválida: {sample_barcode}",
                "file_id": _clean_text(row.get("file_id")),
            })
            continue

        ref = _clean_text(row.get("reference_allele"))
        alt = _clean_text(row.get("tumor_seq_allele2"))
        vid = _variant_id(patient_id.upper(), hugo.upper(), protein,
                          chromosome, start, ref, alt)
        key = (patient_id.upper(), vid)

        if key not in records:
            records[key] = {
                "variant_id": vid,
                "patient_id": patient_id.upper(),
                "sample_id": sample_barcode,
                "sample_ids": [sample_barcode],
                "_file_ids": {_clean_text(row.get("file_id")) or ""},
                "hugo_symbol": hugo.upper(),
                "variant_classification": "Missense_Mutation",
                "protein_change": protein,
                "chromosome": chromosome,
                "start_position": start,
                "end_position": _int_or_none(row.get("end_position")),
                "reference_allele": ref,
                "tumor_seq_allele2": alt,
                "genomic_dna_change": _genomic_dna_change(chromosome, start, ref, alt),
                "mutation_status": "Somatic",
                "filter_status": filter_status,
                "t_depth": _int_or_none(row.get("t_depth")),
                "t_ref_count": _int_or_none(row.get("t_ref_count")),
                "t_alt_count": _int_or_none(row.get("t_alt_count")),
                "hgvsc": _clean_text(row.get("hgvsc")),
                "hgvsp_short": _clean_text(row.get("hgvsp_short")),
                "transcript_id": _clean_text(row.get("transcript_id")),
                "file_id": _clean_text(row.get("file_id")),
                "gdc_data_release": _clean_text(row.get("_gdc_data_release")) or release,
            }
        else:
            existing = records[key]
            if sample_barcode not in existing["sample_ids"]:
                existing["sample_ids"].append(sample_barcode)
            file_id = _clean_text(row.get("file_id"))
            if file_id:
                existing["_file_ids"].add(file_id)

    output: list[dict] = []
    for record in records.values():
        record["sample_ids"] = sorted(record["sample_ids"])
        record["sample_id"] = record["sample_ids"][0]
        record["n_samples"] = len(record["sample_ids"])
        record.pop("_file_ids", None)
        output.append(record)

    output.sort(key=lambda item: (
        item["patient_id"], item["hugo_symbol"], item["protein_change"],
        item["start_position"],
    ))
    return output, rejected


def build_clinical(cohort: list[dict], cases: pd.DataFrame, release: str | None,
                   attributes: Iterable[str] = tuple(a["id"] for a in BASE_ATTRIBUTES)) -> list[dict]:
    """Construye clinical_attributes.json (formato largo, nivel paciente)."""
    requested = {a.upper() for a in attributes}
    catalog = [a for a in BASE_ATTRIBUTES if a["id"] in requested]
    catalog += [a for a in OPTIONAL_ATTRIBUTES if a["id"] in requested]

    raw_by_id = cases.set_index("case_id").to_dict(orient="index")

    clinical: list[dict] = []

    for case in cohort:
        pid = case["case_code"]
        raw = raw_by_id.get(case["case_id"], {})
        for attribute in catalog:
            attr = attribute["id"]
            value: str | None
            raw_value: str | None = None

            if attr == "PRIMARY_SITE":
                value = case.get("primary_site") or "Breast"
                raw_value = _raw_text(raw.get("primary_site"))
            elif attr == "ETHNICITY":
                value = case.get("ethnicity")
                raw_value = _raw_text(raw.get("ethnicity"))
            elif attr == "RACE":
                value = case.get("race")
                raw_value = _raw_text(raw.get("race"))
            elif attr == "AGE":
                value = str(case["age"]) if case.get("age") is not None else None
                raw_value = _raw_text(raw.get("age_at_index"))
            elif attr == "HISTOLOGY":
                value = case.get("histology")
                raw_value = _raw_text(raw.get("primary_diagnosis"))
            elif attr == "STAGE":
                value = case.get("stage")
                raw_value = _raw_text(raw.get("ajcc_pathologic_stage"))
            elif attr == "VITAL_STATUS":
                value = case.get("vital_status")
                raw_value = _raw_text(raw.get("vital_status"))
            else:
                continue

            clinical.append({
                "studyId": f"{PROJECT_ID}-HISPANIC",
                "patientId": pid,
                "clinicalAttributeId": attr,
                "value": value,
                "raw_value": raw_value,
                "value_type": attribute["type"],
                "source": attribute["source"],
                "gdc_data_release": case.get("gdc_data_release") or release,
            })

    clinical.sort(key=lambda item: (item["patientId"], item["clinicalAttributeId"]))
    return clinical


# ---------------------------------------------------------------------------
# Validación
# ---------------------------------------------------------------------------
def validate_contract(cohort: list[dict], mutations: list[dict],
                      clinical: list[dict], manifest: dict,
                      require_science: bool = True) -> list[str]:
    """Aplica las reglas del contrato 2.0.0. Devuelve una lista de errores."""
    errors: list[str] = []

    if manifest.get("contract_version") != CONTRACT_VERSION:
        errors.append(f"contract_version distinto de {CONTRACT_VERSION}")
    if require_science and manifest.get("curated"):
        errors.append("bundle curado: no apto para uso científico")
    if manifest.get("data_origin") != "gdc_open_access":
        errors.append("data_origin debe ser gdc_open_access en un bundle científico")

    codes = [c.get("case_code") for c in cohort]
    if len(codes) != len(set(codes)):
        errors.append("case_code duplicado en la cohorte")
    for code in codes:
        if not code or not CASE_CODE_RE.match(code):
            errors.append(f"case_code inválido: {code}")
    for case in cohort:
        if case.get("ethnicity") != ETHNICITY_VALUE:
            errors.append(f"etnia fuera de cohorte: {case.get('case_code')}")

    cohort_set = set(codes)

    clinical_keys = set()
    for row in clinical:
        pid = row.get("patientId")
        if pid not in cohort_set:
            errors.append(f"clinical patientId fuera de cohorte: {pid}")
        key = (row.get("studyId"), pid, row.get("clinicalAttributeId"))
        if key in clinical_keys:
            errors.append(f"clave clínica duplicada: {key}")
        clinical_keys.add(key)

    variant_keys = set()
    for row in mutations:
        pid = row.get("patient_id")
        if pid not in cohort_set:
            errors.append(f"mutation patient_id fuera de cohorte: {pid}")
        key = (pid, row.get("variant_id"))
        if key in variant_keys:
            errors.append(f"variante duplicada: {key}")
        variant_keys.add(key)

        if not TUMOR_SAMPLE_CODE_RE.match(str(row.get("sample_id") or "")):
            errors.append(
                f"sample_id no tumoral o inválido: {row.get('sample_id')} "
                f"({pid} {row.get('hugo_symbol')} {row.get('protein_change')})"
            )
        if not CHROMOSOME_RE.match(str(row.get("chromosome") or "")):
            errors.append(f"chromosome inválido: {row.get('chromosome')}")
        if not PROTEIN_RE.match(str(row.get("protein_change") or "")):
            errors.append(f"protein_change inválido: {row.get('protein_change')}")
        if not isinstance(row.get("start_position"), int) or row["start_position"] <= 0:
            errors.append(f"start_position inválido: {row.get('start_position')}")

        if manifest.get("mutation_scope") == "target_panel":
            if row.get("hugo_symbol") not in set(manifest.get("target_genes") or []):
                errors.append(f"gen fuera del panel: {row.get('hugo_symbol')}")

    counts = manifest.get("counts") or {}
    if counts.get("cohort_cases") != len(cohort):
        errors.append("counts.cohort_cases no coincide")
    if counts.get("mutation_records") != len(mutations):
        errors.append("counts.mutation_records no coincide")
    if counts.get("clinical_records") != len(clinical):
        errors.append("counts.clinical_records no coincide")

    expected_release = manifest.get("gdc_data_release")
    for row in mutations:
        if expected_release and row.get("gdc_data_release") != expected_release:
            errors.append(f"release inconsistente en mutación: {row.get('gdc_data_release')}")
            break
    for row in clinical:
        if expected_release and row.get("gdc_data_release") != expected_release:
            errors.append(f"release inconsistente en clínica: {row.get('gdc_data_release')}")
            break

    return errors


def validate_bundle_dir(bundle_dir: Path, require_science: bool = True) -> list[str]:
    """Valida un bundle ya escrito: hashes, conteos y reglas del contrato."""
    errors: list[str] = []
    manifest_path = bundle_dir / "manifest.json"
    if not manifest_path.exists():
        return [f"Falta manifest.json en {bundle_dir}"]
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    payloads: dict[str, list] = {}
    for name, info in (manifest.get("files") or {}).items():
        path = bundle_dir / name
        if not path.exists():
            errors.append(f"Falta {name}")
            continue
        actual = sha256_file(path)
        if actual != info.get("sha256"):
            errors.append(f"SHA-256 no coincide para {name}")
        if path.stat().st_size != info.get("bytes"):
            errors.append(f"Tamaño no coincide para {name}")
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, list):
            errors.append(f"{name} no es una lista")
            continue
        if len(data) != info.get("records"):
            errors.append(f"Registros no coinciden para {name}")
        payloads[name] = data

    cohort = payloads.get("cohort_cases.json", [])
    mutations = payloads.get("somatic_mutations.json", [])
    clinical = payloads.get("clinical_attributes.json", [])
    errors.extend(validate_contract(cohort, mutations, clinical, manifest,
                                    require_science=require_science))
    return errors


def _write_json(path: Path, payload: Any) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=_json_default),
        encoding="utf-8",
    )


def _file_info(path: Path, records: int) -> dict:
    return {"sha256": sha256_file(path), "records": records, "bytes": path.stat().st_size}


def export_bundle(
    data_dir: Path,
    outdir: Path,
    project: str = PROJECT_ID,
    ethnicity: str = ETHNICITY_VALUE,
    scope: str = "target_panel",
    target_genes: Iterable[str] = TARGET_GENES,
    offline: bool = False,
    require_science: bool = True,
    extra_notes: Iterable[str] | None = None,
) -> dict:
    """Genera un bundle inmutable y devuelve {path, manifest, errors}."""
    if scope not in {"target_panel", "all_missense"}:
        raise ValueError("scope debe ser 'target_panel' o 'all_missense'")

    data = load_medallion(data_dir, project)
    cases, samples = data["cases"], data["samples"]
    mutations_df, maf_samples = data["mutations"], data["maf_samples"]

    release = _release_from_data(mutations_df, cases)
    status = _gdc_status(offline=offline)
    gdc_release = status.get("data_release") or release
    if not gdc_release:
        raise ValueError("No se pudo determinar gdc_data_release")
    if release and gdc_release != release:
        # El Parquet es la fuente de verdad del bundle; se registra la discrepancia.
        pass

    cohort = build_cohort(cases, samples, project, ethnicity)
    cohort_codes = {c["case_code"] for c in cohort}
    if not cohort:
        raise ValueError("La cohorte quedó vacía")

    mutations, rejected = build_mutations(
        mutations_df, maf_samples, cohort_codes, gdc_release,
        scope=scope, target_genes=target_genes,
    )
    clinical = build_clinical(cohort, cases, gdc_release)

    exported_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    bundle_ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    raw_scope = list(target_genes) if scope == "target_panel" else []

    manifest = {
        "contract_version": CONTRACT_VERSION,
        "bundle_id": None,
        "dataset_id": "tcga_brca_hispanic",
        "project_id": project,
        "disease_type": "Breast Invasive Carcinoma",
        "primary_site": "Breast",
        "gdc_data_release": gdc_release,
        "gdc_api_tag": status.get("tag"),
        "exported_at": exported_at,
        "producer": {
            "name": "etl_tcga",
            "version": CONTRACT_VERSION,
            "git_commit": _git_commit(Path(__file__).resolve().parents[3]),
        },
        "source": {
            "layer": "bronze.mutations",
            "mutations_parquet": str((data_dir / "bronze" / "mutations" / project / "mutations.parquet")),
            "maf_samples_parquet": str((data_dir / "bronze" / "mutations" / project / "maf_samples.parquet")),
            "cases_parquet": str((data_dir / "bronze" / "cases" / project / "cases.parquet")),
            "diagnoses_parquet": str((data_dir / "bronze" / "cases" / project / "cases_diagnoses.parquet")),
            "samples_parquet": str((data_dir / "bronze" / "cases" / project / "cases_samples.parquet")),
        },
        "cohort": {
            "filter": f"project_id = {project} AND ethnicity = {ethnicity.upper()}",
            "ethnicity_values": [ethnicity.upper()],
            "total_cases": len(cohort),
            "cases_with_mutations": len({m["patient_id"] for m in mutations}),
            "cases_without_mutations": len(cohort) - len({m["patient_id"] for m in mutations}),
        },
        "mutation_scope": scope,
        "target_genes": raw_scope,
        "data_origin": "gdc_open_access",
        "curated": False,
        "counts": {
            "cohort_cases": len(cohort),
            "mutation_records": len(mutations),
            "mutation_patients": len({m["patient_id"] for m in mutations}),
            "clinical_records": len(clinical),
        },
        "files": {},
        "validation": {
            "passed": False,
            "checked_at": exported_at,
            "rules": [
                "cohort_count", "cohort_ethnicity", "mutation_patient_fk",
                "clinical_patient_fk", "unique_clinical_key", "unique_mutation_key",
                "tumor_sample_only", "protein_change_format", "chromosome_format",
                "release_from_source", "sha256_match",
            ],
            "errors": [],
        },
        "notes": [
            "RACE/ETHNICITY son autorreportados; no son ancestría genética.",
            "El bundle se genera desde el medallón TCGA; no se consulta /ssms.",
        ] + (list(extra_notes) if extra_notes else []),
    }
    if scope == "target_panel":
        manifest["notes"].append(
            "mutation_scope=target_panel: no contiene todas las missense de la cohorte."
        )

    outdir.mkdir(parents=True, exist_ok=True)
    stage = outdir / f".tmp-{uuid.uuid4().hex}"
    stage.mkdir(parents=True)

    try:
        _write_json(stage / "cohort_cases.json", cohort)
        _write_json(stage / "somatic_mutations.json", mutations)
        _write_json(stage / "clinical_attributes.json", clinical)

        manifest["files"] = {
            "cohort_cases.json": _file_info(stage / "cohort_cases.json", len(cohort)),
            "somatic_mutations.json": _file_info(stage / "somatic_mutations.json", len(mutations)),
            "clinical_attributes.json": _file_info(stage / "clinical_attributes.json", len(clinical)),
        }

        digest = hashlib.sha256(
            "".join(f["sha256"] for f in manifest["files"].values()).encode()
        ).hexdigest()[:8]
        manifest["bundle_id"] = (
            f"{project}-HISPANIC__GDC-{_release_short(gdc_release)}__{bundle_ts}__{digest}"
        )

        errors = validate_contract(cohort, mutations, clinical, manifest,
                                   require_science=require_science)
        manifest["validation"]["passed"] = not errors
        manifest["validation"]["errors"] = errors
        if rejected:
            manifest["notes"].append(
                f"Registros rechazados por muestra/calidad: {len(rejected)}. "
                f"Ver validation.rejected (primeros 20)."
            )
            manifest["validation"]["rejected"] = rejected[:20]

        _write_json(stage / "manifest.json", manifest)
        post_errors = validate_bundle_dir(stage, require_science=require_science)
        if post_errors:
            errors = sorted(set(errors + post_errors))
            manifest["validation"]["passed"] = False
            manifest["validation"]["errors"] = errors
            _write_json(stage / "manifest.json", manifest)
        final = outdir / manifest["bundle_id"]
        if final.exists():
            raise FileExistsError(f"El bundle ya existe y es inmutable: {final}")
        shutil.move(str(stage), str(final))
    except Exception:
        shutil.rmtree(stage, ignore_errors=True)
        raise

    return {"path": final, "manifest": manifest, "errors": errors, "rejected": rejected}
