from __future__ import annotations

import json
from types import SimpleNamespace

from app import analytics, db
from app import main as web_main
from app.main import app
from fastapi.testclient import TestClient

client = TestClient(app)


def _empty(monkeypatch) -> None:
    monkeypatch.setattr(db, "safe_query", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(db, "safe_scalar", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(db, "ping", lambda: True)
    monkeypatch.setattr(analytics, "expression_file_stats", lambda: {"files": 0, "rows": 0})


def test_health_ok(monkeypatch) -> None:
    monkeypatch.setattr(db, "ping", lambda: True)

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_health_unhealthy(monkeypatch) -> None:
    def fail() -> None:
        raise RuntimeError("sin base de datos")

    monkeypatch.setattr(db, "ping", fail)

    response = client.get("/health")

    assert response.status_code == 503
    assert response.json()["status"] == "unhealthy"


def test_empty_state_pages_render_without_database(monkeypatch) -> None:
    _empty(monkeypatch)

    for path in ("/", "/casos", "/genes", "/reportes"):
        response = client.get(path)
        assert response.status_code == 200, path
        assert "TCGA Medallion" in response.text

    response = client.get("/reportes")
    assert "Todavía no hay datos gold" in response.text


def test_status_page_starts_with_pipeline_visualization(monkeypatch) -> None:
    _empty(monkeypatch)

    response = client.get("/")

    assert response.status_code == 200
    assert response.text.index("Visualización del pipeline") < response.text.index("Estado del pipeline")
    assert "/static/vendor/plotly.min.js" in response.text
    assert "/static/pipeline-dashboard.js" in response.text


def test_pipeline_visualization_api_returns_dashboard_sections(monkeypatch) -> None:
    def query(statement: str, _params=None):
        if "FROM ops.pipeline_runs" in statement and "run_date" in statement:
            return [{"run_date": "2026-11-10", "total": 2, "success": 1, "failed": 1, "running": 0}]
        if "FROM ops.pipeline_runs" in statement:
            return [{
                "step": "bronze_mutations", "project_id": "TCGA-BRCA", "status": "failed",
                "rows_in": 1, "rows_out": 0, "started_at": "2026-11-10T10:00:00+00:00",
                "finished_at": "2026-11-10T10:01:00+00:00", "duration_seconds": 60,
                "error": "schema error", "layer": "bronze",
            }]
        if "FROM ops.dq_results" in statement:
            return [{"severity": "critical", "passed": False, "checks": 1}]
        if "FROM silver.cases_diagnoses" in statement:
            return [{"stage": "Stage II", "cases": 10}]
        if "FROM silver.cases c" in statement:
            return [{"age_group": "50-59", "cases": 10}]
        if "FROM silver.mutations" in statement and "top_genes" in statement:
            return [{"gene": "TP53", "case_id": "case-1", "submitter_id": "TCGA-XX-0001", "mutations": 1}]
        if "FROM silver.mutations" in statement:
            return [{"gene": "TP53", "cases": 1, "mutations": 1}]
        if "FROM gold.sample_marker_values" in statement:
            return [{"gene": "ESR1", "median_tpm": 12.5, "samples": 10}]
        return []

    monkeypatch.setattr(web_main, "_projects_available", lambda: ["TCGA-BRCA"])
    monkeypatch.setattr(db, "safe_query", query)

    response = client.get("/api/pipeline/visualization?project=TCGA-BRCA")

    assert response.status_code == 200
    payload = response.json()
    assert payload["history"][0]["step"] == "bronze_mutations"
    assert payload["history"][0]["status"] == "failed"
    assert payload["quality"][0]["passed"] is False
    assert payload["stages"][0]["stage"] == "Stage II"
    assert payload["top_mutations"][0]["gene"] == "TP53"


def test_cases_table_shows_ethnicity_column(monkeypatch) -> None:
    monkeypatch.setattr(
        web_main,
        "_case_rows",
        lambda *_args: ([{
            "case_id": "case-1", "submitter_id": "TCGA-XX-0001",
            "project_id": "TCGA-BRCA", "sex_at_birth": "female", "race": "white",
            "ethnicity": "HISPANIC OR LATINO", "vital_status": "Alive",
            "age_at_index": 50, "primary_diagnosis": "Carcinoma",
            "ajcc_pathologic_stage": "Stage IIA",
        }], 1, 1, 1),
    )

    response = client.get("/casos/tabla")

    assert response.status_code == 200
    assert "Etnicidad" in response.text
    assert "HISPANIC OR LATINO" in response.text
    assert "Caso priorizado en la cohorte hispana/latina" in response.text


def test_cases_query_prioritizes_hispanic_latino_ethnicity(monkeypatch) -> None:
    statements = []

    def query(statement: str, _params=None):
        statements.append(statement)
        if "count(*) AS n" in statement:
            return [{"n": 0}]
        return []

    monkeypatch.setattr(db, "safe_query", query)

    response = client.get("/casos")

    assert response.status_code == 200
    rows_query = next(statement for statement in statements if "SELECT c.case_id" in statement)
    assert "lower(coalesce(d.ethnicity, '')) = 'hispanic or latino' THEN 0" in rows_query


def test_missing_case_returns_404_with_clear_message(monkeypatch) -> None:
    _empty(monkeypatch)

    response = client.get("/casos/no-existe")

    assert response.status_code == 404
    assert "No hay información para mostrar" in response.text


def test_case_detail_renders_related_fields_and_mutation_count(monkeypatch) -> None:
    def query(statement: str, _params=None):
        if "FROM silver.cases c" in statement:
            return [{
                "case_id": "case-1", "submitter_id": "TCGA-XX-0001",
                "project_id": "TCGA-BRCA", "sex_at_birth": "female",
                "state": "CA", "days_to_birth": -18000,
            }]
        if "FROM silver.cases_diagnoses WHERE" in statement:
            return [{
                "diagnosis_id": "dx-1", "primary_diagnosis": "Carcinoma",
                "tumor_grade": "G2", "age_at_diagnosis": None,
                "treatments": [{
                    "treatment_id": "t-1", "treatment_type": "Pharmaceutical Therapy, NOS",
                    "therapeutic_agents": "Carboplatin", "treatment_or_therapy": "yes",
                    "days_to_treatment_start": 57,
                }],
            }]
        if "FROM silver.cases_exposures" in statement:
            return [{"exposure_id": "exp-1", "alcohol_drinks_per_day": 1}]
        if "FROM silver.samples" in statement:
            return [{"sample_id": "sample-1", "sample_type": "Primary Tumor", "preservation_method": "FFPE"}]
        if "FROM silver.files" in statement:
            return [{"file_id": "file-1", "file_name": "rna.tsv", "data_type": "Gene Expression Quantification"}]
        return []

    monkeypatch.setattr(db, "safe_query", query)
    monkeypatch.setattr(db, "safe_scalar", lambda *_args, **_kwargs: 12)

    response = client.get("/casos/case-1")

    assert response.status_code == 200
    assert "Año de nacimiento" in response.text
    assert "CA" in response.text
    assert "Tumor Grade" in response.text
    assert "Alcohol Drinks Per Day" in response.text
    assert "Preservation Method" in response.text
    assert "ADN</span> Variantes somáticas (12)" in response.text
    assert "ARN</span> Expresión génica" in response.text
    assert "Ácido desoxirribonucleico" in response.text
    assert "Ácido ribonucleico" in response.text
    assert "/casos/case-1/mutaciones?page=1" in response.text
    assert "Carboplatin" in response.text
    assert "Administrado: sí" in response.text
    assert "Inicio: día 57" in response.text
    assert "'treatment_id':" not in response.text


def test_case_detail_shows_supplemental_report_and_hispanic_percentiles(monkeypatch, tmp_path) -> None:
    report_dir = tmp_path / "case_reports" / "TCGA-XX-0001"
    report_dir.mkdir(parents=True)
    (report_dir / "report.json").write_text(json.dumps({
        "identity": {
            "case_id": "case-1", "submitter_id": "TCGA-XX-0001",
            "ethnicity": "HISPANIC OR LATINO", "hispanic_latino_priority": True,
        },
        "metadata": {"gdc_data_release": "Data Release 46.0"},
        "survival": {"vital_status": "Alive", "days_to_last_follow_up": 180},
        "percentiles_hispanic": {
            "ESR1": {"tpm": 56.7, "percentil": 72, "nivel": "alto", "n_cohorte": 400},
        },
    }), encoding="utf-8")
    monkeypatch.setattr(web_main, "settings", SimpleNamespace(data_dir=tmp_path))

    def query(statement: str, _params=None):
        if "FROM silver.cases c" in statement:
            return [{
                "case_id": "case-1", "submitter_id": "TCGA-XX-0001",
                "project_id": "TCGA-BRCA", "ethnicity": "HISPANIC OR LATINO",
            }]
        return []

    monkeypatch.setattr(db, "safe_query", query)
    monkeypatch.setattr(db, "safe_scalar", lambda *_args, **_kwargs: 0)

    response = client.get("/casos/case-1")

    assert response.status_code == 200
    assert "Prioridad hispana/latina" in response.text
    assert "Data Release 46.0" in response.text
    assert "Percentiles de expresión · prioridad hispana/latina" in response.text
    assert "ESR1" in response.text
    assert "Hispanos/latinos" in response.text


def test_case_mutations_are_paginated(monkeypatch) -> None:
    monkeypatch.setattr(db, "safe_scalar", lambda *_args, **_kwargs: 205)
    monkeypatch.setattr(
        db,
        "safe_query",
        lambda *_args, **_kwargs: [{"mutation_id": "m-101", "hugo_symbol": "TP53", "filter_status": "PASS"}],
    )

    response = client.get("/casos/case-1/mutaciones?page=2")

    assert response.status_code == 200
    assert "Página 2 de 3" in response.text
    assert "TP53" in response.text


def test_case_expression_table_supports_all_gene_pages(monkeypatch) -> None:
    monkeypatch.setattr(
        analytics,
        "expression_top_genes",
        lambda *_args, **_kwargs: [{
            "gene_id": "ENSG0001", "gene_name": "GENE1", "gene_type": "protein_coding",
            "tpm_unstranded": 3.0, "fpkm_unstranded": 2.0,
            "fpkm_uq_unstranded": 1.0, "unstranded": 4,
        }],
    )
    monkeypatch.setattr(analytics, "expression_gene_count", lambda *_args, **_kwargs: 120)
    monkeypatch.setattr(analytics, "expression_gene_types", lambda *_args, **_kwargs: ["protein_coding"])

    response = client.get(
        "/casos/case-1/expresion/tabla?file_id=file-1&limit=50&offset=50"
    )

    assert response.status_code == 200
    assert "51–51 de 120 genes" in response.text
    assert "FPKM-UQ" in response.text
    assert "Siguiente" in response.text


def test_case_pdf_includes_cached_supplemental_gdc_report(monkeypatch, tmp_path) -> None:
    report_dir = tmp_path / "case_reports" / "TCGA-5L-AAT1"
    report_dir.mkdir(parents=True)
    (report_dir / "report.json").write_text(json.dumps({
        "identity": {"case_id": "case-1", "submitter_id": "TCGA-5L-AAT1", "hispanic_latino_priority": True, "ethnicity": "HISPANIC OR LATINO"},
        "metadata": {"gdc_data_release": "Data Release 46.0", "generated_at_utc": "2026-11-07", "legacy_case_ids": ["16fc3677-0393-4ed1-adf3-c8355f056369"]},
        "survival": {"vital_status": "Alive", "days_to_last_follow_up": 180, "follow_up_records": 1},
        "clinical": {
            "samples": [{
                "sample_id": "sample-1", "submitter_id": "TCGA-5L-AAT1-01A",
                "sample_type": "Primary Tumor", "tissue_type": "Tumor",
                "portions": [{"analytes": [{"analyte_id": "analyte-1", "aliquots": [{"aliquot_id": "aliquot-1"}]}],
                              "slides": [{"slide_id": "slide-1", "percent_tumor_nuclei": 70, "percent_necrosis": 5}]}],
            }],
            "family_histories": [{"relationship_type": "Mother", "relationship_primary_diagnosis": "Breast carcinoma", "relative_with_cancer_history": "yes"}],
            "follow_ups": [{"days_to_follow_up": 180, "disease_response": "Alive"}],
        },
        "stage_tnm": [{"primary_diagnosis": "Lobular carcinoma", "stage": "Stage IV", "t": "T2", "n": "N1", "m": "M1*", "stage_note": "M1* inferido por Stage IV"}],
        "diagnosis_interpretation": [], "timeline": [],
        "receptors_clinical": {}, "receptors_expression": {"values_tpm": {}},
        "mutations": [{"gene": "PIK3CA", "dna_change": "chr3:g.100A>T"}],
        "copy_number": [], "expression_panel": [], "expression_panel_tpm": {},
        "percentiles_all_brca": {}, "percentiles_lobular": {},
        "percentiles_hispanic": {}, "percentiles_lobular_hispanic": {},
        "files_inventory": {"by_data_type": {"Masked Somatic Mutation": 1}},
        "file_links": {}, "annotations": [], "curated": {}, "warnings": [],
    }), encoding="utf-8")
    (tmp_path / "case_reports" / "_aliases.json").write_text(json.dumps({
        "16fc3677-0393-4ed1-adf3-c8355f056369": {
            "case_id": "case-1", "submitter_id": "TCGA-5L-AAT1",
            "report": "TCGA-5L-AAT1/report.json",
            "legacy_case_ids": ["16fc3677-0393-4ed1-adf3-c8355f056369"],
        },
    }), encoding="utf-8")
    monkeypatch.setattr(web_main, "settings", SimpleNamespace(data_dir=tmp_path))

    def query(statement: str, _params=None):
        if "SELECT case_id FROM silver.cases WHERE case_id = %s AND submitter_id = %s" in statement:
            return [{"case_id": "case-1"}]
        if "FROM silver.cases c" in statement:
            return [{"case_id": "case-1", "submitter_id": "TCGA-5L-AAT1", "project_id": "TCGA-BRCA"}]
        return None

    monkeypatch.setattr(db, "safe_query", query)
    monkeypatch.setattr(db, "safe_scalar", lambda *_args, **_kwargs: 0)

    legacy_url = "/casos/16fc3677-0393-4ed1-adf3-c8355f056369"
    detail_response = client.get(legacy_url)
    response = client.get(legacy_url + "/pdf")

    assert detail_response.status_code == 200
    assert "TCGA-5L-AAT1" in detail_response.text
    assert response.status_code == 200
    assert "Prioridad hispana/latina" in response.text
    assert "Data Release 46.0" in response.text
    assert "Seguimiento y supervivencia" in response.text
    assert "Biospecimen y calidad de tejido" in response.text
    assert "Antecedentes familiares" in response.text
    assert "M1* inferido por Stage IV" in response.text
    assert "PIK3CA" in response.text
    assert "TCGA-BRCA" in response.text


def test_gene_api_returns_empty_list_without_data(monkeypatch) -> None:
    _empty(monkeypatch)

    response = client.get("/api/genes?q=ESR1")

    assert response.status_code == 200
    assert response.json() == []
