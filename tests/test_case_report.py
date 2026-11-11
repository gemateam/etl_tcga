from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from openpyxl import Workbook
from tcga.case_report import (
    _percentiles,
    _read_local_table,
    generate_case_report,
    generate_case_report_batch,
    is_hispanic_or_latino,
    resolve_case,
)
from tcga.config import Settings


def make_settings(tmp_path: Path) -> Settings:
    return Settings(
        data_dir=tmp_path / "data",
        gdc_api_url="https://api.test",
        projects=("TCGA-BRCA",),
        database_url="postgresql://unused",
        duckdb_memory_limit="1GB",
        duckdb_threads=1,
        download_workers=1,
        download_batch_bytes=1_000_000,
        http_timeout=1,
        http_retries=1,
        http_backoff=0,
        log_level="INFO",
        sql_dir=tmp_path,
    )


class FakeGDC:
    def __init__(self, _settings: Settings):
        self.downloaded: list[str] = []
        self.case_hits = [
            {
                "case_id": "case-non-hispanic", "submitter_id": "TCGA-DUP",
                "project": {"project_id": "TCGA-BRCA"},
                "demographic": {"ethnicity": "NOT HISPANIC OR LATINO"},
            },
            {
                "case_id": "case-hispanic", "submitter_id": "TCGA-DUP",
                "project": {"project_id": "TCGA-BRCA"},
                "demographic": {"ethnicity": "HISPANIC OR LATINO"},
            },
        ]
        self.case = {
            "case_id": "case-hispanic",
            "submitter_id": "TCGA-5L-AAT1",
            "project": {"project_id": "TCGA-BRCA"},
            "demographic": {"ethnicity": "HISPANIC OR LATINO", "vital_status": "Alive"},
            "diagnoses": [{
                "diagnosis_id": "dx-1", "primary_diagnosis": "Lobular carcinoma NOS",
                "morphology": "8520/3", "ajcc_pathologic_stage": "Stage IV",
                "diagnosis_is_primary_disease": True, "days_to_diagnosis": 0,
                "treatments": [{
                    "treatment_type": "Pharmaceutical Therapy, NOS",
                    "therapeutic_agents": "Carboplatin", "treatment_or_therapy": "yes",
                    "days_to_treatment_start": 57,
                }],
            }],
            "follow_ups": [{
                "days_to_follow_up": 180,
                "molecular_tests": [{
                    "antigen": "estrogen receptor", "laboratory_test": "IHC",
                    "test_result": "Positive", "days_to_test": 25,
                }],
            }],
            "family_histories": [{"relationship_type": "Mother", "relative_with_cancer_history": "yes"}],
            "samples": [{
                "sample_id": "sample-1", "sample_type": "Primary Tumor",
                "portions": [{"slides": [{"percent_tumor_nuclei": 70, "percent_necrosis": 5}]}],
            }],
        }
        self.files = [
            {"file_id": "maf-1", "file_name": "case.maf", "data_type": "Masked Somatic Mutation", "access": "open", "data_format": "MAF"},
            {"file_id": "cnv-1", "file_name": "cnv.tsv", "data_type": "Gene Level Copy Number", "access": "open", "data_format": "TSV"},
            {"file_id": "supp-1", "file_name": "clinical.xml", "data_type": "Clinical Supplement", "access": "open", "data_format": "XML"},
            {"file_id": "expr-1", "file_name": "expression.tsv", "data_type": "Gene Expression Quantification", "access": "open", "data_format": "TSV", "analysis": {"workflow_type": "STAR - Counts"}},
            {"file_id": "controlled-1", "file_name": "wgs.bam", "data_type": "Aligned Reads", "access": "controlled", "data_format": "BAM"},
        ]
        self.payloads = {
            "maf-1": "Hugo_Symbol\tChromosome\tStart_Position\tEnd_Position\tReference_Allele\tTumor_Seq_Allele2\tVariant_Classification\tHGVSp_Short\tFILTER\nPIK3CA\t3\t100\t101\tA\tT\tMissense_Mutation\tp.E545K\tPASS\n",
            "cnv-1": "gene_name\tchromosome\tstart\tend\tcopy_number\tmin_copy_number\tmax_copy_number\nERBB2\t17\t1\t2\t6\t5\t7\n",
            "supp-1": "<clinical><breast_carcinoma_estrogen_receptor_status>Positive</breast_carcinoma_estrogen_receptor_status><her2_immunohistochemistry_level_result>Negative</her2_immunohistochemistry_level_result></clinical>",
            "expr-1": "gene_name\tgene_id\tgene_type\ttpm_unstranded\tfpkm_unstranded\tfpkm_uq_unstranded\tunstranded\nCDH1\tENSG1\tprotein_coding\t49.9\t10\t12\t20\nERBB2\tENSG2\tprotein_coding\t219.2\t30\t32\t40\n",
        }

    def status(self) -> dict[str, str]:
        return {"data_release": "Data Release 46.0"}

    def get_json(self, path: str, params=None) -> dict[str, Any]:
        assert path == "cases/case-hispanic"
        return {"data": self.case}

    def search_pages(self, endpoint, payload, page_size=500, max_records=None):
        if endpoint == "cases":
            hits = self.case_hits
        elif endpoint == "files":
            filters = json.dumps(payload.get("filters", {}))
            hits = self.files if '"field": "cases.case_id"' in filters else [self.files[3]]
        elif endpoint == "annotations":
            hits = [{"annotation_id": "a1", "category": "Quality", "classification": "Observation", "status": "active"}]
        elif endpoint == "ssm_occurrences":
            hits = [{"ssm": {
                "ssm_id": "ssm-1", "genomic_dna_change": "chr3:g.100A>T",
                "mutation_subtype": "SNP", "consequence": [{
                    "transcript": {"gene": {"symbol": "PIK3CA"}, "aa_change": "p.E545K", "consequence_type": "missense", "is_canonical": True},
                }],
            }}]
        else:
            hits = []
        if max_records is not None:
            hits = hits[:max_records]
        if hits:
            yield 1, {"data": {"hits": hits, "pagination": {"total": len(hits)}}}

    def download_batch(self, file_ids, destination: Path) -> int:
        file_id = file_ids[0]
        if file_id == "controlled-1":
            raise AssertionError("No se deben descargar archivos controlados")
        self.downloaded.append(file_id)
        payload = self.payloads[file_id].encode()
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(payload)
        return len(payload)


def test_hispanic_priority_resolves_ambiguous_submitter_id(tmp_path: Path) -> None:
    client = FakeGDC(make_settings(tmp_path))

    resolved = resolve_case(client, "TCGA-DUP")

    assert resolved == "case-hispanic"
    assert is_hispanic_or_latino("Hispanic or Latino")
    assert not is_hispanic_or_latino("NOT HISPANIC OR LATINO")


def test_batch_generates_only_hispanic_cases_and_continues(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    client = FakeGDC(settings)
    report_dir = tmp_path / "batch-reports"
    case_dir = report_dir / "TCGA-5L-AAT1"
    case_dir.mkdir(parents=True)
    legacy_id = "16fc3677-0393-4ed1-adf3-c8355f056369"
    (case_dir / "report.json").write_text(json.dumps({
        "identity": {"case_id": "case-hispanic", "submitter_id": "TCGA-5L-AAT1"},
        "metadata": {"legacy_case_ids": []},
    }), encoding="utf-8")
    (report_dir / "_aliases.json").write_text(json.dumps({
        legacy_id: {
            "case_id": "case-hispanic", "submitter_id": "TCGA-5L-AAT1",
            "report": "TCGA-5L-AAT1/report.json", "legacy_case_ids": [legacy_id],
        },
    }), encoding="utf-8")

    result = generate_case_report_batch(
        settings=settings,
        out_dir=report_dir,
        percentiles=False,
        client_factory=lambda _settings: client,
    )

    assert result["cases_found"] == 1
    assert result["reports_processed"] == 1
    assert result["failures"] == []
    report_path = tmp_path / "batch-reports" / "TCGA-5L-AAT1" / "report.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["identity"]["hispanic_latino_priority"] is True
    assert legacy_id in report["metadata"]["legacy_case_ids"]


def test_report_includes_layers_and_never_downloads_controlled_files(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    client = FakeGDC(settings)
    cdr = tmp_path / "cdr.csv"
    cdr.write_text("bcr_patient_barcode,OS\nTCGA-5L-AAT1,1\n", encoding="utf-8")

    report = generate_case_report(
        "case-hispanic", settings=settings, out_dir=tmp_path / "reports", cdr=cdr,
        client_factory=lambda _settings: client,
        legacy_case_ids=["16fc3677-0393-4ed1-adf3-c8355f056369"],
    )

    assert report["identity"]["hispanic_latino_priority"] is True
    assert report["survival"]["days_to_last_follow_up"] == 180
    assert report["stage_tnm"][0]["stage_note"].startswith("M1* se infiere por estadio IV")
    assert report["stage_tnm"][0]["m"] == "M1*"
    assert report["mutations"][0]["gene"] == "PIK3CA"
    assert report["copy_number"][0]["gene_name"] == "ERBB2"
    assert report["receptors_clinical"]["ER"]["clinical_supplement"]
    assert report["receptors_clinical"]["HER2"]["clinical_supplement"]
    assert report["expression_panel_tpm"]["CDH1"] == 49.9
    assert report["percentiles_all_brca"] == {}
    assert report["curated"]["cdr"]["OS"] == "1"
    assert report["available_layers"]["clinical"] is True
    assert report["available_layers"]["mutations"] is True
    assert report["available_layers"]["receptors_clinical"] is True
    assert any("Percentiles no calculados" in warning for warning in report["warnings"])
    assert any("pam50: no se proporcionó" in warning for warning in report["warnings"])
    controlled = report["file_links"]["controlled_inventory_only"]
    assert [item["file_id"] for item in controlled] == ["controlled-1"]
    assert "controlled-1" not in client.downloaded
    assert Path(report["report_path"]).exists()
    persisted = json.loads(Path(report["report_path"]).read_text(encoding="utf-8"))
    assert persisted["identity"]["ethnicity"] == "HISPANIC OR LATINO"
    aliases = json.loads((tmp_path / "reports" / "_aliases.json").read_text(encoding="utf-8"))
    assert aliases["16fc3677-0393-4ed1-adf3-c8355f056369"]["case_id"] == "case-hispanic"

    before = len(client.downloaded)
    cached = generate_case_report(
        "case-hispanic", settings=settings, out_dir=tmp_path / "reports", cdr=cdr,
        client_factory=lambda _settings: client,
        legacy_case_ids=["16fc3677-0393-4ed1-adf3-c8355f056369"],
    )
    assert len(client.downloaded) == before
    assert cached["report_path"] == report["report_path"]


def test_percentiles_report_tpm_level_and_cohort_size() -> None:
    result = _percentiles(
        {"ESR1": {"tpm": 56.7}},
        [{"ESR1": 0.1}, {"ESR1": 56.7}, {"ESR1": 219.2}],
    )

    assert result["ESR1"] == {"tpm": 56.7, "percentil": 33.3, "nivel": "Medio", "n_cohorte": 3}


def test_percentiles_generate_hispanic_and_lobular_cohorts_without_network(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    client = FakeGDC(settings)

    report = generate_case_report(
        "case-hispanic", settings=settings, out_dir=tmp_path / "percentiles",
        percentiles=True, max_cohort_files=1, client_factory=lambda _settings: client,
    )

    assert report["percentiles_all_brca"]["CDH1"]["n_cohorte"] == 1
    assert report["percentiles_lobular_hispanic"]["ERBB2"]["n_cohorte"] == 1
    assert report["cohort_counts"]["percentiles_hispanic"]["listed"] == 1
    assert any("no representan el universo completo" in warning for warning in report["warnings"])


def test_local_xlsx_table_joins_by_submitter_id(tmp_path: Path) -> None:
    path = tmp_path / "cdr.xlsx"
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(["bcr_patient_barcode", "OS", "OS.time"])
    sheet.append(["TCGA-5L-AAT1", 1, 1200])
    workbook.save(path)

    result = _read_local_table(path, "bcr_patient_barcode", "TCGA-5L-AAT1")

    assert result == {"bcr_patient_barcode": "TCGA-5L-AAT1", "OS": 1, "OS.time": 1200}