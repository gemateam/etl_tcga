"""Reporte complementario por caso TCGA desde datos abiertos del GDC."""

from __future__ import annotations

import argparse
import csv
import gzip
import io
import json
import logging
import math
import re
import tarfile
import xml.etree.ElementTree as ET
import zipfile
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable

from openpyxl import load_workbook

from .config import Settings, get_settings
from .gdc.client import GDCClient, GDCError, is_tar_gz

log = logging.getLogger("tcga.case_report")

CASE_EXPAND = [
    "annotations",
    "demographic",
    "diagnoses",
    "diagnoses.pathology_details",
    "diagnoses.treatments",
    "exposures",
    "family_histories",
    "follow_ups",
    "follow_ups.molecular_tests",
    "follow_ups.other_clinical_attributes",
    "samples",
    "samples.portions",
    "samples.portions.analytes",
    "samples.portions.analytes.aliquots",
    "samples.portions.slides",
]
CASE_FIELDS = [
    "case_id", "submitter_id", "project.project_id", "project.name", "primary_site",
    "disease_type", "consent_type", "days_to_lost_to_followup", "lost_to_followup",
    "demographic.demographic_id", "demographic.sex_at_birth", "demographic.race",
    "demographic.ethnicity", "demographic.vital_status", "demographic.days_to_death",
    "demographic.days_to_birth", "demographic.age_at_index", "demographic.year_of_birth",
    "demographic.year_of_death", "demographic.cause_of_death", "demographic.marital_status",
    "demographic.education_level", "demographic.country_of_residence_at_enrollment",
    "demographic.state", "diagnoses.diagnosis_id", "diagnoses.submitter_id",
    "diagnoses.primary_diagnosis", "diagnoses.tissue_or_organ_of_origin",
    "diagnoses.classification_of_tumor", "diagnoses.tumor_grade",
    "diagnoses.ajcc_pathologic_stage", "diagnoses.ajcc_pathologic_t",
    "diagnoses.ajcc_pathologic_n", "diagnoses.ajcc_pathologic_m",
    "diagnoses.ajcc_clinical_stage", "diagnoses.age_at_diagnosis",
    "diagnoses.days_to_diagnosis", "diagnoses.days_to_last_follow_up",
    "diagnoses.days_to_last_known_disease_status", "diagnoses.last_known_disease_status",
    "diagnoses.morphology", "diagnoses.laterality", "diagnoses.site_of_resection_or_biopsy",
    "diagnoses.year_of_diagnosis", "diagnoses.days_to_recurrence",
    "diagnoses.progression_or_recurrence", "diagnoses.diagnosis_is_primary_disease",
    "diagnoses.icd_10_code", "diagnoses.prior_malignancy", "diagnoses.synchronous_malignancy",
    "diagnoses.pathology_details.greatest_tumor_dimension",
    "diagnoses.pathology_details.lymph_nodes_positive",
    "diagnoses.pathology_details.lymph_nodes_removed",
    "diagnoses.pathology_details.margin_status", "diagnoses.pathology_details.necrosis_percent",
    "diagnoses.pathology_details.percent_tumor_nuclei",
    "diagnoses.pathology_details.tumor_largest_dimension_diameter",
    "diagnoses.treatments.treatment_id", "diagnoses.treatments.therapeutic_agents",
    "diagnoses.treatments.treatment_type", "diagnoses.treatments.treatment_or_therapy",
    "diagnoses.treatments.days_to_treatment_start", "diagnoses.treatments.days_to_treatment_end",
    "diagnoses.treatments.regimen_or_line_of_therapy",
    "diagnoses.treatments.treatment_intent_type", "diagnoses.treatments.treatment_outcome",
    "diagnoses.treatments.treatment_effect", "diagnoses.treatments.treatment_effect_indicator",
    "diagnoses.treatments.number_of_cycles", "diagnoses.treatments.number_of_fractions",
    "diagnoses.treatments.prescribed_dose", "diagnoses.treatments.prescribed_dose_units",
    "diagnoses.treatments.treatment_type_administered",
    "diagnoses.treatments.treatment_anatomic_sites",
    "diagnoses.treatments.reason_treatment_not_given",
    "exposures.exposure_id", "exposures.submitter_id", "exposures.alcohol_history",
    "exposures.alcohol_intensity", "exposures.alcohol_drinks_per_day",
    "exposures.cigarettes_per_day", "exposures.pack_years_smoked",
    "exposures.smoking_frequency", "exposures.tobacco_smoking_status",
    "exposures.tobacco_smoking_onset_year", "exposures.tobacco_smoking_quit_year",
    "exposures.type_of_tobacco_used", "exposures.exposure_duration_years",
    "exposures.asbestos_exposure_type", "exposures.environmental_tobacco_smoke_exposure",
    "exposures.secondhand_smoke_as_child", "family_histories.family_history_id",
    "family_histories.relationship_type", "family_histories.relationship_primary_diagnosis",
    "family_histories.relationship_age_at_diagnosis", "family_histories.relationship_sex_at_birth",
    "family_histories.relative_with_cancer_history", "family_histories.relative_deceased",
    "family_histories.relative_smoker", "follow_ups.follow_up_id", "follow_ups.days_to_follow_up",
    "follow_ups.year_of_follow_up", "follow_ups.days_to_progression",
    "follow_ups.days_to_recurrence", "follow_ups.progression_or_recurrence",
    "follow_ups.progression_or_recurrence_anatomic_site",
    "follow_ups.progression_or_recurrence_type", "follow_ups.disease_response",
    "follow_ups.first_event", "follow_ups.molecular_tests.molecular_test_id",
    "follow_ups.molecular_tests.antigen", "follow_ups.molecular_tests.gene_symbol",
    "follow_ups.molecular_tests.laboratory_test",
    "follow_ups.molecular_tests.molecular_analysis_method",
    "follow_ups.molecular_tests.test_result", "follow_ups.molecular_tests.test_value",
    "follow_ups.molecular_tests.test_units", "follow_ups.molecular_tests.days_to_test",
    "follow_ups.molecular_tests.staining_intensity_value",
    "follow_ups.molecular_tests.variant_type", "follow_ups.molecular_tests.aa_change",
    "samples.sample_id", "samples.submitter_id", "samples.sample_type", "samples.tissue_type",
    "samples.tumor_descriptor", "samples.specimen_type", "samples.biospecimen_anatomic_site",
    "samples.biospecimen_laterality", "samples.preservation_method",
    "samples.days_to_collection", "samples.days_to_sample_procurement",
    "samples.portions.portion_id", "samples.portions.portion_number", "samples.portions.is_ffpe",
    "samples.portions.weight", "samples.portions.analytes.analyte_id",
    "samples.portions.analytes.analyte_type", "samples.portions.analytes.experimental_protocol_type",
    "samples.portions.analytes.rna_integrity_number", "samples.portions.analytes.dna_integrity_number",
    "samples.portions.analytes.a260_a280_ratio", "samples.portions.analytes.concentration",
    "samples.portions.analytes.aliquots.aliquot_id", "samples.portions.analytes.aliquots.amount",
    "samples.portions.analytes.aliquots.concentration",
    "samples.portions.slides.slide_id", "samples.portions.slides.submitter_id",
    "samples.portions.slides.section_location", "samples.portions.slides.percent_tumor_nuclei",
    "samples.portions.slides.percent_tumor_cells", "samples.portions.slides.percent_necrosis",
    "samples.portions.slides.percent_normal_cells",
]
CURRENT_PANEL = ["AURKA", "BRCA1", "CDH1", "ERBB2", "ESR1", "MKI67", "PGR", "TP53"]
PAM50_GENES = [
    "ACTR3B", "ANLN", "BAG1", "BCL2", "BIRC5", "BLVRA", "CCNB1", "CCNE1", "CDC20",
    "CDC6", "NUF2", "CDH3", "CENPF", "CEP55", "CXXC5", "EGFR", "ERBB2", "ESR1",
    "EXO1", "FGFR4", "FOXA1", "FOXC1", "GPR160", "GRB7", "KIF2C", "KRT14", "KRT17",
    "KRT5", "MAPT", "MDM2", "MELK", "MIA", "MKI67", "MLPH", "MMP11", "MYBL2", "MYC",
    "NAT1", "ORC6L", "PGR", "PHGDH", "PTTG1", "RRM2", "SFRP1", "SLC39A6", "TMEM45B",
    "TYMS", "UBE2C", "UBE2T",
]
EXTRA_PANEL = ["GATA3", "CCND1", "CD274", "PIK3CA", "MAP3K1", "PTEN", "AKT1"]
GENE_PANEL = sorted(set(CURRENT_PANEL + PAM50_GENES + EXTRA_PANEL))
DRIVER_GENES = [
    "PIK3CA", "TP53", "CDH1", "GATA3", "MAP3K1", "PTEN", "AKT1", "ERBB2", "CCND1",
    "FOXA1", "RUNX1", "KMT2C",
]
HISPANIC_LABELS = {"hispanic or latino", "hispanic", "latino", "latina"}
RECEPTOR_XML_TAGS = {
    "breast_carcinoma_estrogen_receptor_status": "ER",
    "er_level_cell_percentage_category": "ER",
    "breast_carcinoma_progesterone_receptor_status": "PR",
    "progesterone_receptor_level_cell_percent_category": "PR",
    "her2_immunohistochemistry_level_result": "HER2",
    "lab_proc_her2_neu_immunohistochemistry_receptor_status": "HER2",
    "pos_finding_her2_erbb2_other_measurement_scale_text": "HER2",
    "her2_erbb_method_calculation_method_text": "HER2",
}
UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)


def _eq(field: str, value: Any) -> dict[str, Any]:
    return {"op": "=", "content": {"field": field, "value": value}}


def _in(field: str, values: Iterable[Any]) -> dict[str, Any]:
    return {"op": "in", "content": {"field": field, "value": list(values)}}


def _and(*filters: dict[str, Any]) -> dict[str, Any]:
    return {"op": "and", "content": list(filters)}


def _case_demographic(case: dict[str, Any]) -> dict[str, Any]:
    value = case.get("demographic") or {}
    return value[0] if isinstance(value, list) and value else value if isinstance(value, dict) else {}


def is_hispanic_or_latino(value: Any) -> bool:
    return str(value or "").strip().casefold() in HISPANIC_LABELS


def _release_slug(value: str | None) -> str:
    if not value:
        return "unknown"
    slug = re.sub(r"[^A-Za-z0-9._-]+", "_", value).strip("._-")
    return slug or "unknown"


def _safe_name(value: str) -> str:
    return Path(value or "file").name.replace("\\", "_").replace("/", "_")


def _number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    temp.replace(path)


def _update_alias_index(
    out_dir: Path,
    report_path: Path,
    case_id: str,
    submitter_id: str,
    aliases: list[str],
) -> None:
    if not aliases:
        return
    index_path = out_dir / "_aliases.json"
    try:
        index = json.loads(index_path.read_text(encoding="utf-8")) if index_path.exists() else {}
    except (OSError, json.JSONDecodeError):
        index = {}
    relative_report = report_path.relative_to(out_dir).as_posix()
    for alias in aliases:
        index[alias] = {
            "case_id": case_id,
            "submitter_id": submitter_id,
            "report": relative_report,
            "legacy_case_ids": aliases,
        }
    _atomic_json(index_path, index)


def _case_legacy_aliases(out_dir: Path, case_id: str) -> list[str]:
    index_path = out_dir / "_aliases.json"
    if not index_path.exists():
        return []
    try:
        index = json.loads(index_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []

    aliases: list[str] = []
    report_root = out_dir.resolve()
    for alias, entry in index.items():
        if not UUID_RE.fullmatch(str(alias)) or not isinstance(entry, dict):
            continue
        if entry.get("case_id") != case_id:
            continue
        relative_report = Path(str(entry.get("report", "")))
        if relative_report.is_absolute() or ".." in relative_report.parts:
            continue
        report_path = (out_dir / relative_report).resolve()
        if report_root not in report_path.parents:
            continue
        try:
            report = json.loads(report_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        identity = report.get("identity") or {}
        if (
            identity.get("case_id") == case_id
            and identity.get("submitter_id") == entry.get("submitter_id")
        ):
            aliases.append(str(alias))
    return sorted(set(aliases))


def _collect_search(
    client: GDCClient,
    endpoint: str,
    filters: dict[str, Any],
    fields: list[str],
    page_size: int = 500,
    max_records: int | None = None,
) -> list[dict[str, Any]]:
    payload = {"filters": filters, "fields": ",".join(fields), "format": "JSON"}
    hits: list[dict[str, Any]] = []
    for _, page in client.search_pages(endpoint, payload, page_size, max_records):
        hits.extend(page.get("data", {}).get("hits", []))
    return hits


def resolve_case(
    client: GDCClient,
    identifier: str,
    prioritize_hispanic: bool = True,
    project: str = "TCGA-BRCA",
) -> str:
    if UUID_RE.fullmatch(identifier):
        return identifier
    hits = _collect_search(
        client,
        "cases",
        _eq("submitter_id", identifier),
        ["case_id", "submitter_id", "project.project_id", "demographic.ethnicity"],
        page_size=100,
    )
    if not hits:
        raise ValueError(f"No se encontró el caso {identifier} en el GDC.")
    if prioritize_hispanic:
        hits.sort(
            key=lambda hit: (
                not is_hispanic_or_latino(_case_demographic(hit).get("ethnicity")),
                (hit.get("project") or {}).get("project_id") != project,
                hit.get("case_id", ""),
            )
        )
    return hits[0]["case_id"]


def _get_case(client: GDCClient, case_id: str) -> dict[str, Any]:
    result = client.get_json(
        f"cases/{case_id}",
        params={"expand": ",".join(CASE_EXPAND), "fields": ",".join(CASE_FIELDS)},
    )
    data = result.get("data")
    if not isinstance(data, dict):
        raise GDCError("La respuesta de /cases/{case_id} no contiene data.")
    return data


def _fetch_files(client: GDCClient, case_id: str) -> list[dict[str, Any]]:
    fields = [
        "file_id", "file_name", "file_size", "md5sum", "access", "data_category", "data_type",
        "data_format", "experimental_strategy", "analysis.workflow_type", "cases.case_id",
        "cases.submitter_id", "cases.samples.sample_id", "cases.samples.submitter_id",
        "cases.samples.sample_type",
    ]
    return _collect_search(client, "files", _eq("cases.case_id", case_id), fields)


def _fetch_annotations(client: GDCClient, case_id: str) -> list[dict[str, Any]]:
    fields = [
        "annotation_id", "case_id", "category", "classification", "status", "notes",
        "created_datetime", "entity_type", "entity_submitter_id",
    ]
    return _collect_search(client, "annotations", _eq("case_id", case_id), fields)


def _file_inventory(files: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, int]]:
    inventory: list[dict[str, Any]] = []
    counts: dict[str, int] = {}
    for item in files:
        data_type = item.get("data_type") or "Sin tipo"
        counts[data_type] = counts.get(data_type, 0) + 1
        cases = item.get("cases") or []
        samples = [sample for case in cases for sample in case.get("samples", [])]
        inventory.append({
            "file_id": item.get("file_id"),
            "file_name": item.get("file_name"),
            "data_category": item.get("data_category"),
            "data_type": data_type,
            "data_format": item.get("data_format"),
            "experimental_strategy": item.get("experimental_strategy"),
            "workflow_type": (item.get("analysis") or {}).get("workflow_type"),
            "access": item.get("access"),
            "file_size": item.get("file_size"),
            "md5sum": item.get("md5sum"),
            "samples": [
                {
                    "sample_id": sample.get("sample_id"),
                    "submitter_id": sample.get("submitter_id"),
                    "sample_type": sample.get("sample_type"),
                }
                for sample in samples
            ],
            "portal_url": f"https://portal.gdc.cancer.gov/files/{item['file_id']}"
            if item.get("file_id") else None,
        })
    inventory.sort(key=lambda item: (item["data_type"], item.get("file_name") or ""))
    return inventory, counts


def _open_file(client: GDCClient, item: dict[str, Any], raw_dir: Path) -> Path:
    if str(item.get("access", "")).casefold() != "open":
        raise PermissionError("Se omitió una descarga que no es de acceso abierto.")
    file_id = item.get("file_id")
    if not file_id:
        raise ValueError("El archivo no contiene file_id.")
    dest = raw_dir / f"{file_id}_{_safe_name(item.get('file_name', 'file'))}"
    if not dest.exists() or dest.stat().st_size == 0:
        client.download_batch([file_id], dest)
    return dest


def _text_from_path(path: Path) -> str:
    if is_tar_gz(path):
        with tarfile.open(path, "r:gz") as archive:
            for member in archive.getmembers():
                if not member.isfile():
                    continue
                stream = archive.extractfile(member)
                if stream is not None:
                    return stream.read().decode("utf-8", errors="replace")
        return ""
    data = path.read_bytes()
    if data.startswith(b"\x1f\x8b"):
        data = gzip.decompress(data)
    return data.decode("utf-8", errors="replace")


def _csv_rows(text: str, delimiter: str = "\t") -> Iterable[dict[str, str]]:
    lines = (line for line in text.splitlines() if line.strip() and not line.startswith("#"))
    yield from csv.DictReader(lines, delimiter=delimiter)


def _parse_maf(text: str, case_id: str, submitter_id: str) -> list[dict[str, Any]]:
    rows = []
    for row in _csv_rows(text):
        gene = (row.get("Hugo_Symbol") or "").strip()
        if gene not in DRIVER_GENES:
            continue
        rows.append({
            "gene": gene,
            "case_id": case_id,
            "submitter_id": submitter_id,
            "dna_change": f"{row.get('Chromosome', '')}:{row.get('Start_Position', '')} "
            f"{row.get('Reference_Allele', '')}>{row.get('Tumor_Seq_Allele2', '')}",
            "subtype": row.get("Variant_Classification"),
            "variant_type": row.get("Variant_Type"),
            "aa_change": row.get("HGVSp_Short"),
            "consequence": row.get("Consequence"),
            "filter": row.get("FILTER"),
            "source": "MAF abierto del GDC",
        })
    return rows


def _fetch_ssm_mutations(
    client: GDCClient, case_id: str, submitter_id: str, warnings: list[str]
) -> list[dict[str, Any]]:
    open_maf = DRIVER_GENES
    filters = _and(
        _eq("case.case_id", case_id),
        _in("ssm.consequence.transcript.gene.symbol", open_maf),
    )
    fields = [
        "case.case_id", "ssm.ssm_id", "ssm.genomic_dna_change", "ssm.mutation_subtype",
        "ssm.consequence.transcript.gene.symbol", "ssm.consequence.transcript.aa_change",
        "ssm.consequence.transcript.consequence_type", "ssm.consequence.transcript.is_canonical",
    ]
    try:
        hits = _collect_search(client, "ssm_occurrences", filters, fields)
    except Exception as exc:  # noqa: BLE001
        warnings.append(f"Mutaciones por ssm_occurrences no disponibles: {type(exc).__name__}.")
        return []
    rows: dict[tuple[str, str], dict[str, Any]] = {}
    for hit in hits:
        ssm = hit.get("ssm") or {}
        for consequence in ssm.get("consequence") or []:
            transcript = consequence.get("transcript") or {}
            gene = (transcript.get("gene") or {}).get("symbol")
            if gene not in DRIVER_GENES:
                continue
            canonical = transcript.get("is_canonical")
            if canonical not in (None, True, 1, "1", "true", "True"):
                continue
            key = (str(ssm.get("ssm_id") or ""), str(gene))
            rows[key] = {
                "gene": gene,
                "case_id": case_id,
                "submitter_id": submitter_id,
                "dna_change": ssm.get("genomic_dna_change"),
                "subtype": ssm.get("mutation_subtype"),
                "aa_change": transcript.get("aa_change"),
                "consequence": transcript.get("consequence_type"),
                "source": "GDC ssm_occurrences; contrastar con MAF abierto",
            }
    return list(rows.values())


def _parse_cnv(path: Path, genes: list[str]) -> list[dict[str, Any]]:
    wanted = set(genes)
    rows = []
    for row in _csv_rows(_text_from_path(path)):
        gene = row.get("gene_name") or row.get("gene") or row.get("Gene")
        if gene in wanted:
            rows.append({key: value for key, value in row.items() if value not in (None, "")})
    return rows


def _extract_xml_payloads(path: Path) -> list[tuple[str, bytes]]:
    if is_tar_gz(path):
        result = []
        with tarfile.open(path, "r:gz") as archive:
            for member in archive.getmembers():
                if member.isfile() and member.name.lower().endswith((".xml", ".xml.gz")):
                    stream = archive.extractfile(member)
                    if stream:
                        payload = stream.read()
                        if member.name.lower().endswith(".gz"):
                            payload = gzip.decompress(payload)
                        result.append((member.name, payload))
        return result
    payload = path.read_bytes()
    if payload.startswith(b"\x1f\x8b"):
        payload = gzip.decompress(payload)
    if zipfile.is_zipfile(io.BytesIO(payload)):
        result = []
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            for name in archive.namelist():
                if name.lower().endswith(".xml"):
                    result.append((name, archive.read(name)))
        return result
    return [(path.name, payload)] if b"<" in payload[:200] else []


def _receptor_group(tag: str) -> str | None:
    normalized = re.sub(r"[^a-z0-9]+", "_", tag.casefold()).strip("_")
    return RECEPTOR_XML_TAGS.get(normalized)


def _parse_receptors_xml(payloads: list[tuple[str, bytes]]) -> dict[str, list[dict[str, str]]]:
    results: dict[str, list[dict[str, str]]] = {"ER": [], "PR": [], "HER2": []}
    for name, payload in payloads:
        try:
            for _, element in ET.iterparse(io.BytesIO(payload), events=("end",)):
                value = (element.text or "").strip()
                group = _receptor_group(element.tag.rsplit("}", 1)[-1])
                if group and value:
                    entry = {"field": element.tag.rsplit("}", 1)[-1], "value": value, "file": name}
                    if entry not in results[group]:
                        results[group].append(entry)
                element.clear()
        except ET.ParseError:
            continue
    return results


def _receptors_from_molecular_tests(case: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    results: dict[str, list[dict[str, Any]]] = {"ER": [], "PR": [], "HER2": []}
    for follow_up in case.get("follow_ups") or []:
        for test in follow_up.get("molecular_tests") or []:
            label = " ".join(str(test.get(key) or "") for key in ("antigen", "gene_symbol", "laboratory_test", "molecular_analysis_method")).casefold()
            group = "HER2" if any(term in label for term in ("her2", "her-2", "erbb2")) else None
            if group is None and ("progesterone receptor" in label or "pr receptor" in label):
                group = "PR"
            if group is None and ("estrogen receptor" in label or "oestrogen receptor" in label):
                group = "ER"
            if group:
                results[group].append({
                    "source": "follow_ups.molecular_tests",
                    "test": test.get("laboratory_test") or test.get("antigen") or test.get("gene_symbol"),
                    "result": test.get("test_result"),
                    "value": test.get("test_value"),
                    "units": test.get("test_units"),
                    "method": test.get("molecular_analysis_method"),
                    "days_to_test": test.get("days_to_test"),
                })
    return results


def _expression_values(text: str) -> dict[str, dict[str, float | None]]:
    values: dict[str, dict[str, float | None]] = {}
    for row in _csv_rows(text):
        gene = (row.get("gene_name") or "").strip()
        if gene not in GENE_PANEL:
            continue
        values[gene] = {
            "tpm": _number(row.get("tpm_unstranded")),
            "fpkm": _number(row.get("fpkm_unstranded")),
            "fpkm_uq": _number(row.get("fpkm_uq_unstranded")),
            "counts": _number(row.get("unstranded")),
        }
    return values


def _expression_details(values: dict[str, dict[str, float | None]]) -> list[dict[str, Any]]:
    rows = []
    for gene in GENE_PANEL:
        value = values.get(gene, {})
        tpm = value.get("tpm")
        flags = []
        if gene.startswith("MT-"):
            flags.append("mitocondrial")
        if gene.startswith("IG"):
            flags.append("inmunoglobulina")
        rows.append({
            "gene": gene,
            "tpm": tpm,
            "log2_tpm_plus_1": round(math.log2(tpm + 1), 4) if tpm is not None else None,
            "fpkm": value.get("fpkm"),
            "fpkm_uq": value.get("fpkm_uq"),
            "counts": value.get("counts"),
            "informative": not flags,
            "flags": flags,
        })
    return rows


def _survival(case: dict[str, Any]) -> dict[str, Any]:
    demographic = _case_demographic(case)
    follow_ups = case.get("follow_ups") or []
    diagnoses = case.get("diagnoses") or []
    times: list[int] = []
    for row in follow_ups:
        value = row.get("days_to_follow_up")
        if isinstance(value, (int, float)):
            times.append(int(value))
    for row in diagnoses:
        value = row.get("days_to_last_follow_up")
        if isinstance(value, (int, float)):
            times.append(int(value))
    death = demographic.get("days_to_death")
    if isinstance(death, (int, float)):
        times.append(int(death))
    return {
        "vital_status": demographic.get("vital_status"),
        "days_to_death": death,
        "days_to_last_follow_up": max(times) if times else None,
        "lost_to_followup": case.get("lost_to_followup"),
        "follow_up_records": len(follow_ups),
        "interpretation": "Seguimiento derivado de campos GDC; no se imputan fechas ni eventos faltantes.",
    }


def _stage_summary(case: dict[str, Any]) -> list[dict[str, Any]]:
    output = []
    for diagnosis in case.get("diagnoses") or []:
        stage = diagnosis.get("ajcc_pathologic_stage") or diagnosis.get("ajcc_clinical_stage")
        m = diagnosis.get("ajcc_pathologic_m") or diagnosis.get("ajcc_clinical_m")
        note = None
        if stage and "iv" in str(stage).casefold() and not m:
            m = "M1*"
            note = "M1* se infiere por estadio IV; GDC no registró explícitamente la categoría M."
        output.append({
            "diagnosis_id": diagnosis.get("diagnosis_id"),
            "primary_diagnosis": diagnosis.get("primary_diagnosis"),
            "classification_of_tumor": diagnosis.get("classification_of_tumor"),
            "morphology": diagnosis.get("morphology"),
            "laterality": diagnosis.get("laterality"),
            "stage": stage,
            "t": diagnosis.get("ajcc_pathologic_t") or diagnosis.get("ajcc_clinical_t"),
            "n": diagnosis.get("ajcc_pathologic_n") or diagnosis.get("ajcc_clinical_n"),
            "m": m,
            "stage_note": note,
        })
    return output


def _timeline(case: dict[str, Any]) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for diagnosis in case.get("diagnoses") or []:
        events.append({
            "day": diagnosis.get("days_to_diagnosis"),
            "kind": "diagnóstico",
            "label": diagnosis.get("primary_diagnosis") or "Diagnóstico no especificado",
            "status": diagnosis.get("progression_or_recurrence") or diagnosis.get("classification_of_tumor"),
            "source": "GDC diagnoses",
        })
        for treatment in diagnosis.get("treatments") or []:
            events.append({
                "day": treatment.get("days_to_treatment_start"),
                "end_day": treatment.get("days_to_treatment_end"),
                "kind": "tratamiento",
                "label": treatment.get("therapeutic_agents") or treatment.get("treatment_type") or "Tratamiento no especificado",
                "administered": treatment.get("treatment_or_therapy"),
                "intent": treatment.get("treatment_intent_type"),
                "outcome": treatment.get("treatment_outcome"),
                "source": "GDC diagnoses.treatments",
            })
    for follow_up in case.get("follow_ups") or []:
        day = follow_up.get("days_to_follow_up")
        if day is None:
            day = follow_up.get("days_to_progression") or follow_up.get("days_to_recurrence")
        events.append({
            "day": day,
            "kind": "seguimiento",
            "label": follow_up.get("disease_response") or "Seguimiento",
            "status": follow_up.get("progression_or_recurrence"),
            "source": "GDC follow_ups",
        })
    events.sort(key=lambda row: (row.get("day") is None, row.get("day") or 0, row["kind"]))
    return events


def _diagnosis_interpretation(case: dict[str, Any]) -> list[dict[str, Any]]:
    diagnoses = case.get("diagnoses") or []
    result = []
    for diagnosis in diagnoses:
        explicit = str(diagnosis.get("progression_or_recurrence") or "").casefold()
        classification = str(diagnosis.get("classification_of_tumor") or "").casefold()
        if "recurr" in explicit or "recurr" in classification or "metasta" in classification:
            status = "Recurrencia o metástasis reportada por GDC"
        elif diagnosis.get("diagnosis_is_primary_disease") is True:
            status = "Diagnóstico primario marcado por GDC"
        elif diagnosis.get("diagnosis_is_primary_disease") is False:
            status = "Diagnóstico secundario; relación clínica por confirmar"
        else:
            status = "Por confirmar: GDC no permite distinguir segundo primario de recurrencia"
        result.append({
            "diagnosis_id": diagnosis.get("diagnosis_id"),
            "primary_diagnosis": diagnosis.get("primary_diagnosis"),
            "laterality": diagnosis.get("laterality"),
            "morphology": diagnosis.get("morphology"),
            "classification": status,
            "lesion_sample_link": "No se infiere vínculo diagnóstico-muestra si GDC no lo proporciona.",
        })
    return result


def _clinical_receptors(case: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    results: dict[str, list[dict[str, Any]]] = {"ER": [], "PR": [], "HER2": []}
    for follow_up in case.get("follow_ups") or []:
        for test in follow_up.get("molecular_tests") or []:
            label = " ".join(str(test.get(k) or "") for k in ("antigen", "gene_symbol", "laboratory_test", "molecular_analysis_method")).casefold()
            receptor = "HER2" if any(x in label for x in ("her2", "her-2", "erbb2")) else None
            if receptor is None and any(x in label for x in ("progesterone receptor", "progesterone receptor")):
                receptor = "PR"
            if receptor is None and any(x in label for x in ("estrogen receptor", "oestrogen receptor")):
                receptor = "ER"
            if receptor:
                results[receptor].append({
                    "source": "GDC follow_ups.molecular_tests",
                    "test": test.get("laboratory_test") or test.get("antigen") or test.get("gene_symbol"),
                    "result": test.get("test_result"),
                    "value": test.get("test_value"),
                    "units": test.get("test_units"),
                    "method": test.get("molecular_analysis_method"),
                    "days_to_test": test.get("days_to_test"),
                })
    return results


def _read_local_table(path: Path, id_col: str, submitter_id: str) -> dict[str, Any] | None:
    if not path.exists():
        raise FileNotFoundError(f"No existe la tabla local: {path}")
    if path.suffix.casefold() in {".xlsx", ".xlsm"}:
        workbook = load_workbook(path, read_only=True, data_only=True)
        try:
            sheet = workbook.active
            values = sheet.iter_rows(values_only=True)
            headers = [str(value).strip() if value is not None else "" for value in next(values, ())]
            if id_col not in headers:
                raise ValueError(f"La tabla {path.name} no contiene la columna {id_col}.")
            index = headers.index(id_col)
            for row in values:
                if index < len(row) and str(row[index] or "").strip() == submitter_id:
                    return {header: row[i] for i, header in enumerate(headers) if header and i < len(row)}
            return None
        finally:
            workbook.close()
    with path.open("r", encoding="utf-8-sig", newline="") as stream:
        sample = stream.read(4096)
        stream.seek(0)
        delimiter = "\t" if path.suffix.casefold() in {".tsv", ".txt"} else csv.Sniffer().sniff(sample, delimiters=",\t;").delimiter
        reader = csv.DictReader(stream, delimiter=delimiter)
        if id_col not in (reader.fieldnames or []):
            raise ValueError(f"La tabla {path.name} no contiene la columna {id_col}.")
        return next((dict(row) for row in reader if (row.get(id_col) or "").strip() == submitter_id), None)


def _curated_tables(
    submitter_id: str,
    table_paths: dict[str, Path | None],
    id_col: str,
    warnings: list[str],
) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for name, path in table_paths.items():
        result[name] = None
        if path is None:
            warnings.append(f"{name}: no se proporcionó tabla curada local; capa no disponible.")
            continue
        try:
            result[name] = _read_local_table(path, id_col, submitter_id)
            if result[name] is None:
                warnings.append(f"{name}: no se encontró {submitter_id} en {path.name}.")
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"{name}: no se pudo leer la tabla local ({type(exc).__name__}).")
    return result


def _cohort_filters(project: str, morphology: str | None = None, ethnicity: str | None = None) -> dict[str, Any]:
    parts = [
        _eq("cases.project.project_id", project),
        _eq("data_type", "Gene Expression Quantification"),
        _eq("analysis.workflow_type", "STAR - Counts"),
        _eq("cases.samples.sample_type", "Primary Tumor"),
        _eq("access", "open"),
    ]
    if morphology:
        parts.append(_eq("cases.diagnoses.morphology", morphology))
    if ethnicity:
        parts.append(_eq("cases.demographic.ethnicity", ethnicity))
    return _and(*parts)


def _cohort_file_panels(
    settings: Settings,
    release: str | None,
    project: str,
    morphology: str | None,
    ethnicity: str | None,
    max_files: int,
    workers: int,
    warnings: list[str],
    client_factory: Callable[[Settings], GDCClient],
) -> tuple[list[dict[str, float]], dict[str, int]]:
    fields = [
        "file_id", "file_name", "access", "cases.case_id", "cases.samples.sample_type",
    ]
    try:
        listing = _collect_search(
            client_factory(settings), "files", _cohort_filters(project, morphology, ethnicity),
            fields, page_size=500, max_records=max_files or None,
        )
    except Exception as exc:  # noqa: BLE001
        warnings.append(f"Cohorte {morphology or 'BRCA'} {ethnicity or ''}: listado no disponible ({type(exc).__name__}).")
        return [], {"listed": 0, "processed": 0}

    files = [item for item in listing if item.get("access") == "open" and item.get("file_id")]
    cache_dir = settings.data_dir / "case_report_cache" / _release_slug(release)
    cache_dir.mkdir(parents=True, exist_ok=True)

    def load_panel(item: dict[str, Any]) -> dict[str, float] | None:
        cache_file = cache_dir / f"{_safe_name(item['file_id'])}.json"
        if cache_file.exists():
            try:
                return json.loads(cache_file.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                cache_file.unlink(missing_ok=True)
        name = _safe_name(item.get("file_name", "expression.tsv"))
        temp_file = cache_dir / f"{_safe_name(item['file_id'])}_{name}"
        try:
            _open_file(client_factory(settings), item, cache_dir)
            temp_file = cache_dir / f"{item['file_id']}_{name}"
            values = _expression_values(_text_from_path(temp_file))
            panel = {gene: row["tpm"] for gene, row in values.items() if row.get("tpm") is not None}
            _atomic_json(cache_file, panel)
            temp_file.unlink(missing_ok=True)
            return panel
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"No se pudo procesar archivo de cohorte {item.get('file_id')}: {type(exc).__name__}.")
            temp_file.unlink(missing_ok=True)
            return None

    panels: list[dict[str, float]] = []
    with ThreadPoolExecutor(max_workers=max(1, min(int(workers), 4))) as executor:
        for panel in executor.map(load_panel, files):
            if panel:
                panels.append(panel)
    return panels, {"listed": len(files), "processed": len(panels)}


def _percentiles(
    values: dict[str, dict[str, float | None]],
    panels: list[dict[str, float]],
) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for gene, current in values.items():
        tpm = current.get("tpm")
        cohort_values = [_number(row.get(gene)) for row in panels]
        cohort_values = [value for value in cohort_values if value is not None]
        if tpm is None or not cohort_values:
            continue
        percentile = sum(value < tpm for value in cohort_values) / len(cohort_values) * 100
        result[gene] = {
            "tpm": round(tpm, 2),
            "percentil": round(percentile, 1),
            "nivel": "Alto" if percentile >= 75 else "Bajo" if percentile < 25 else "Medio",
            "n_cohorte": len(cohort_values),
        }
    return result


def _data_type_file(
    files: list[dict[str, Any]],
    *,
    exact: str | None = None,
    contains: str | None = None,
    workflow_contains: str | None = None,
) -> dict[str, Any] | None:
    for item in files:
        if item.get("access") != "open":
            continue
        data_type = str(item.get("data_type") or "")
        if exact and data_type != exact:
            continue
        if contains and contains.casefold() not in f"{data_type} {item.get('data_format', '')}".casefold():
            continue
        if workflow_contains:
            workflow = (item.get("analysis") or {}).get("workflow_type") or ""
            if workflow_contains.casefold() not in workflow.casefold():
                continue
        return item
    return None


def generate_case_report(
    identifier: str,
    *,
    settings: Settings | None = None,
    out_dir: Path | None = None,
    percentiles: bool = False,
    max_cohort_files: int = 0,
    workers: int = 4,
    cdr: Path | None = None,
    pam50: Path | None = None,
    panc: Path | None = None,
    id_col: str = "bcr_patient_barcode",
    prioritize_hispanic: bool = True,
    refresh: bool = False,
    client_factory: Callable[[Settings], GDCClient] | None = None,
    project: str = "TCGA-BRCA",
    legacy_case_ids: list[str] | None = None,
) -> dict[str, Any]:
    settings = settings or get_settings()
    factory = client_factory or GDCClient
    client = factory(settings)
    warnings: list[str] = []
    try:
        status = client.status()
    except Exception as exc:  # noqa: BLE001
        status = {}
        warnings.append(f"No se pudo consultar la versión del GDC ({type(exc).__name__}).")
    release = status.get("data_release") or status.get("data_release_version")

    case_id = resolve_case(
        client, identifier, prioritize_hispanic=prioritize_hispanic, project=project
    )
    case = _get_case(client, case_id)
    submitter_id = case.get("submitter_id") or identifier
    actual_project = (case.get("project") or {}).get("project_id") or project
    aliases = sorted(set(legacy_case_ids or []))
    invalid_aliases = [alias for alias in aliases if not UUID_RE.fullmatch(alias)]
    if invalid_aliases:
        raise ValueError("Los aliases de UUID deben tener formato UUID completo.")
    demographic = _case_demographic(case)
    ethnicity = demographic.get("ethnicity")
    hispanic_priority = is_hispanic_or_latino(ethnicity)
    if not hispanic_priority:
        warnings.append(f"Etnicidad reportada por GDC: {ethnicity or 'sin dato'}; el caso se mantiene en el reporte, pero no se clasifica como hispano/latino.")
    if actual_project != project:
        warnings.append(f"El caso pertenece a {actual_project}; se solicitó {project}.")

    out_dir = Path(out_dir or settings.data_dir / "case_reports")
    report_dir = out_dir / _safe_name(submitter_id)
    report_path = report_dir / "report.json"
    options = {
        "project": project,
        "id_col": id_col,
        "percentiles": bool(percentiles),
        "max_cohort_files": int(max_cohort_files),
        "prioritize_hispanic": bool(prioritize_hispanic),
        "legacy_case_ids": aliases,
        "curated_tables": {
            name: {"path": str(path), "mtime": path.stat().st_mtime if path and path.exists() else None}
            for name, path in (("cdr", cdr), ("pam50", pam50), ("pancanatlas", panc))
        },
    }
    if report_path.exists() and not refresh:
        try:
            cached = json.loads(report_path.read_text(encoding="utf-8"))
            if (
                cached.get("metadata", {}).get("gdc_data_release") == release
                and cached.get("metadata", {}).get("options") == options
                and cached.get("identity", {}).get("case_id") == case_id
            ):
                _update_alias_index(
                    out_dir, report_path, case_id, submitter_id, aliases
                )
                return cached
        except (OSError, json.JSONDecodeError):
            pass

    raw_dir = report_dir / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    try:
        files = _fetch_files(client, case_id)
    except Exception as exc:  # noqa: BLE001
        files = []
        warnings.append(f"Inventario de archivos no disponible ({type(exc).__name__}).")
    inventory, inventory_counts = _file_inventory(files)

    try:
        annotations = _fetch_annotations(client, case_id)
    except Exception as exc:  # noqa: BLE001
        annotations = []
        warnings.append(f"Anotaciones no disponibles ({type(exc).__name__}).")

    open_maf = _data_type_file(files, exact="Masked Somatic Mutation")
    mutations: list[dict[str, Any]] = []
    mutation_source = None
    if open_maf:
        mutations = _fetch_ssm_mutations(client, case_id, submitter_id, warnings)
        if mutations:
            mutation_source = "GDC ssm_occurrences; MAF abierto disponible"
    if not mutations and open_maf:
        try:
            maf_path = _open_file(client, open_maf, raw_dir)
            mutations = _parse_maf(_text_from_path(maf_path), case_id, submitter_id)
            mutation_source = "MAF abierto del GDC"
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"Mutaciones MAF no disponibles ({type(exc).__name__}).")
    elif not open_maf:
        warnings.append("No hay archivo Masked Somatic Mutation de acceso abierto; no se consultan mutaciones fuera de acceso abierto.")

    copy_number: list[dict[str, Any]] = []
    cnv_file = _data_type_file(files, exact="Gene Level Copy Number")
    if cnv_file:
        try:
            cnv_path = _open_file(client, cnv_file, raw_dir)
            copy_number = _parse_cnv(cnv_path, DRIVER_GENES)
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"Número de copias no disponible ({type(exc).__name__}).")
    else:
        warnings.append("No hay archivo Gene Level Copy Number de acceso abierto.")

    receptors = _clinical_receptors(case)
    supplement_files = [item for item in files if item.get("access") == "open" and item.get("data_type") == "Clinical Supplement"]
    supplement_receptors: dict[str, list[dict[str, str]]] = {"ER": [], "PR": [], "HER2": []}
    for item in supplement_files:
        try:
            supplement_path = _open_file(client, item, raw_dir)
            parsed = _parse_receptors_xml(_extract_xml_payloads(supplement_path))
            for group, values in parsed.items():
                supplement_receptors[group].extend(values)
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"Clinical Supplement no disponible ({type(exc).__name__}).")
    receptors = {
        group: {"molecular_tests": receptors[group], "clinical_supplement": supplement_receptors[group]}
        for group in ("ER", "PR", "HER2")
    }
    if not any(row for group in receptors.values() for row in group["molecular_tests"] + group["clinical_supplement"]):
        warnings.append("No se encontraron resultados clínicos ER/PR/HER2 en molecular_tests ni en suplementos XML abiertos.")

    expression_file = _data_type_file(
        files, exact="Gene Expression Quantification", workflow_contains="STAR - Counts"
    ) or _data_type_file(files, exact="Gene Expression Quantification")
    expression_values: dict[str, dict[str, float | None]] = {}
    if expression_file:
        try:
            expression_path = _open_file(client, expression_file, raw_dir)
            expression_values = _expression_values(_text_from_path(expression_path))
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"Expresión del caso no disponible ({type(exc).__name__}).")
    else:
        warnings.append("No hay archivo de expresión de acceso abierto para el caso.")
    expression_tpm = {gene: values.get("tpm") for gene, values in expression_values.items()}
    receptor_expression = {gene: expression_tpm.get(gene) for gene in ("ESR1", "PGR", "ERBB2")}

    percentile_results: dict[str, Any] = {
        "percentiles_all_brca": {},
        "percentiles_lobular": {},
        "percentiles_hispanic": {},
        "percentiles_lobular_hispanic": {},
    }
    cohorts: dict[str, dict[str, int]] = {}
    if percentiles and expression_values:
        if max_cohort_files > 0:
            warnings.append(
                f"Percentiles limitados a {max_cohort_files} archivos por cohorte; no representan el universo completo."
            )
        cohort_specs = {
            "percentiles_all_brca": (None, None),
            "percentiles_lobular": ("8520/3", None),
            "percentiles_hispanic": (None, "HISPANIC OR LATINO"),
            "percentiles_lobular_hispanic": ("8520/3", "HISPANIC OR LATINO"),
        }
        for key, (morphology, cohort_ethnicity) in cohort_specs.items():
            cohort_panels, counts = _cohort_file_panels(
                settings, release, project, morphology, cohort_ethnicity,
                max_cohort_files, workers, warnings, factory,
            )
            cohorts[key] = counts
            percentile_results[key] = _percentiles(expression_values, cohort_panels)
    elif percentiles:
        warnings.append("Percentiles omitidos: el caso no tiene un panel de expresión cargado.")
    else:
        warnings.append("Percentiles no calculados; use --percentiles para procesar las cohortes abiertas.")

    curated = _curated_tables(
        submitter_id,
        {"cdr": cdr, "pam50": pam50, "pancanatlas": panc},
        id_col,
        warnings,
    )
    file_links = {
        "pathology_reports": [
            item["portal_url"] for item in inventory
            if item.get("data_type") == "Pathology Report" and item.get("portal_url")
        ],
        "slide_images": [
            item["portal_url"] for item in inventory
            if item.get("data_type") == "Slide Image" and item.get("portal_url")
        ],
        "controlled_inventory_only": [
            item for item in inventory if str(item.get("access", "")).casefold() != "open"
        ],
    }
    report = {
        "metadata": {
            "generated_at_utc": datetime.now(timezone.utc).isoformat(),
            "gdc_data_release": release,
            "case_id": case_id,
            "submitter_id": submitter_id,
            "legacy_case_ids": aliases,
            "project": actual_project,
            "cache_key": f"{case_id}:{release or 'unknown'}",
            "options": options,
            "use": "Solo uso en investigación. Datos abiertos y seudonimizados; no reidentificar ni redistribuir datos individuales.",
            "citation": "The Cancer Genome Atlas Research Network. Nature 490, 61–70 (2012). doi:10.1038/nature11412",
        },
        "identity": {
            "case_id": case_id,
            "submitter_id": submitter_id,
            "project": actual_project,
            "ethnicity": ethnicity,
            "hispanic_latino_priority": hispanic_priority,
            "priority_label": "Prioridad hispana/latina" if hispanic_priority else "Etnicidad no reportada como hispana/latina",
        },
        "clinical": case,
        "survival": _survival(case),
        "stage_tnm": _stage_summary(case),
        "diagnosis_interpretation": _diagnosis_interpretation(case),
        "timeline": _timeline(case),
        "files_inventory": {"by_data_type": inventory_counts, "files": inventory},
        "file_links": file_links,
        "annotations": annotations,
        "mutations": mutations,
        "mutations_source": mutation_source,
        "copy_number": copy_number,
        "receptors_clinical": receptors,
        "receptors_expression": {
            "values_tpm": receptor_expression,
            "warning": "La expresión de ARN no confirma positividad clínica por IHQ/FISH.",
        },
        "expression_panel_tpm": expression_tpm,
        "expression_panel": _expression_details(expression_values),
        "percentiles_all_brca": percentile_results["percentiles_all_brca"],
        "percentiles_lobular": percentile_results["percentiles_lobular"],
        "percentiles_hispanic": percentile_results["percentiles_hispanic"],
        "percentiles_lobular_hispanic": percentile_results["percentiles_lobular_hispanic"],
        "cohort_counts": cohorts,
        "curated": curated,
        "warnings": warnings,
    }
    report["available_layers"] = {
        "clinical": bool(case),
        "files_inventory": bool(inventory),
        "annotations": bool(annotations),
        "mutations": bool(mutations),
        "copy_number": bool(copy_number),
        "receptors_clinical": any(
            group["molecular_tests"] or group["clinical_supplement"]
            for group in receptors.values()
        ),
        "expression_panel_tpm": bool(expression_values),
        "percentiles": any(percentile_results[key] for key in percentile_results),
        "curated": any(curated.values()),
    }
    report["report_path"] = str(report_path)
    _atomic_json(report_path, report)
    _update_alias_index(out_dir, report_path, case_id, submitter_id, aliases)
    return report


def generate_case_report_batch(
    *,
    settings: Settings | None = None,
    project: str = "TCGA-BRCA",
    out_dir: Path | None = None,
    percentiles: bool = True,
    max_cohort_files: int = 0,
    workers: int = 1,
    max_cases: int = 0,
    cdr: Path | None = None,
    pam50: Path | None = None,
    panc: Path | None = None,
    id_col: str = "bcr_patient_barcode",
    refresh: bool = False,
    client_factory: Callable[[Settings], GDCClient] | None = None,
) -> dict[str, Any]:
    """Genera reportes individuales para los casos hispanos/latinos de un proyecto."""
    if max_cases < 0:
        raise ValueError("max_cases debe ser mayor o igual a cero")
    settings = settings or get_settings()
    factory = client_factory or GDCClient
    client = factory(settings)
    fields = ["case_id", "submitter_id", "project.project_id", "demographic.ethnicity"]
    filters = _and(
        _eq("project.project_id", project),
        _eq("demographic.ethnicity", "HISPANIC OR LATINO"),
    )
    hits = _collect_search(
        client,
        "cases",
        filters,
        fields,
        page_size=500,
        max_records=max_cases or None,
    )

    selected: dict[str, dict[str, Any]] = {}
    for hit in hits:
        case_id = str(hit.get("case_id") or "")
        hit_project = (hit.get("project") or {}).get("project_id")
        ethnicity = _case_demographic(hit).get("ethnicity")
        if (
            not case_id
            or not is_hispanic_or_latino(ethnicity)
            or (hit_project and hit_project != project)
        ):
            continue
        selected[case_id] = hit

    cases = sorted(
        selected.values(),
        key=lambda hit: (str(hit.get("submitter_id") or ""), str(hit["case_id"])),
    )
    report_dir = Path(out_dir) if out_dir else settings.data_dir / "case_reports"
    failures: list[dict[str, str]] = []
    processed = 0
    log.info("Casos hispanos/latinos encontrados en %s: %s", project, len(cases))

    for index, hit in enumerate(cases, start=1):
        case_id = str(hit["case_id"])
        submitter_id = str(hit.get("submitter_id") or case_id)
        log.info("Procesando reporte %s/%s: %s", index, len(cases), submitter_id)
        try:
            legacy_aliases = _case_legacy_aliases(report_dir, case_id)
            generate_case_report(
                case_id,
                settings=settings,
                out_dir=report_dir,
                percentiles=percentiles,
                max_cohort_files=max_cohort_files,
                workers=workers,
                cdr=cdr,
                pam50=pam50,
                panc=panc,
                id_col=id_col,
                refresh=refresh,
                project=project,
                client_factory=factory,
                legacy_case_ids=legacy_aliases,
            )
        except Exception as exc:  # noqa: BLE001
            failures.append({
                "case_id": case_id,
                "submitter_id": submitter_id,
                "error": type(exc).__name__,
            })
            log.error("Falló el reporte de %s (%s)", submitter_id, type(exc).__name__)
            continue
        processed += 1

    return {
        "project": project,
        "cases_found": len(cases),
        "reports_processed": processed,
        "failures": failures,
        "out_dir": str(report_dir),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("case", help="submitter_id o UUID del caso")
    parser.add_argument("--out", type=Path, default=Path("out"))
    parser.add_argument("--project", default="TCGA-BRCA")
    parser.add_argument("--legacy-case-id", action="append", default=[], help="UUID anterior que apunta al mismo submitter_id.")
    parser.add_argument("--percentiles", action="store_true")
    parser.add_argument("--max-cohort-files", type=int, default=0)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--cdr", type=Path)
    parser.add_argument("--pam50", type=Path)
    parser.add_argument("--panc", type=Path)
    parser.add_argument("--id-col", default="bcr_patient_barcode")
    parser.add_argument("--no-hispanic-priority", action="store_true")
    parser.add_argument("--refresh", action="store_true")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(message)s",
    )
    try:
        report = generate_case_report(
            args.case,
            settings=get_settings(),
            out_dir=args.out,
            percentiles=args.percentiles,
            max_cohort_files=args.max_cohort_files,
            workers=args.workers,
            cdr=args.cdr,
            pam50=args.pam50,
            panc=args.panc,
            id_col=args.id_col,
            prioritize_hispanic=not args.no_hispanic_priority,
            refresh=args.refresh,
            project=args.project,
            legacy_case_ids=args.legacy_case_id,
        )
    except Exception as exc:  # noqa: BLE001
        log.error("No se pudo generar el reporte: %s", type(exc).__name__)
        return 1
    log.info("Reporte escrito en %s", report["report_path"])
    return 0