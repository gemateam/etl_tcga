"""Pruebas del Contrato de Datos V3 (productor)."""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from tcga.export_contract_v3 import (
    CONTRACT_VERSION,
    build_clinical,
    build_cohort,
    build_mutations,
    validate_contract,
)

REAL_DATA = Path("D:/ProyectosIA/etl_tcga/tcga-medallion/data")
REAL_BUNDLE = Path("D:/ProyectosIA/etl_tcga/tcga-medallion/exports/tcga_brca_hispanic")


def _cases() -> pd.DataFrame:
    return pd.DataFrame([
        {
            "case_id": "c1", "submitter_id": "TCGA-AA-0001", "project_id": "TCGA-BRCA",
            "primary_site": "Breast", "disease_type": "Ductal and Lobular Neoplasms",
            "race": "white", "ethnicity": "hispanic or latino", "age_at_index": 50.0,
            "vital_status": "Alive", "primary_diagnosis": "Infiltrating duct carcinoma, NOS",
            "ajcc_pathologic_stage": "Stage IIA",
            "_gdc_data_release": "Data Release 99.0 - Test",
        },
        {
            "case_id": "c2", "submitter_id": "TCGA-BB-0002", "project_id": "TCGA-BRCA",
            "primary_site": "Breast", "disease_type": "Ductal and Lobular Neoplasms",
            "race": "black or african american", "ethnicity": "not hispanic or latino",
            "age_at_index": 60.0, "vital_status": "Dead",
            "primary_diagnosis": "Lobular carcinoma, NOS",
            "ajcc_pathologic_stage": None,
            "_gdc_data_release": "Data Release 99.0 - Test",
        },
    ])


def _samples() -> pd.DataFrame:
    return pd.DataFrame([
        {"case_id": "c1", "submitter_id": "TCGA-AA-0001-01A"},
        {"case_id": "c1", "submitter_id": "TCGA-AA-0001-10A"},
        {"case_id": "c2", "submitter_id": "TCGA-BB-0002-01A"},
    ])


def _mutations() -> pd.DataFrame:
    common = {
        "case_submitter_id": "TCGA-AA-0001",
        "case_id": "c1",
        "variant_classification": "Missense_Mutation",
        "filter_status": None,
        "chromosome": "chr3",
        "end_position": 179234297,
        "reference_allele": "A",
        "tumor_seq_allele2": "G",
        "t_depth": 100,
        "t_ref_count": 60,
        "t_alt_count": 40,
        "hgvsc": "c.3140A>G",
        "transcript_id": "ENST00000263967",
        "_gdc_data_release": "Data Release 99.0 - Test",
    }
    return pd.DataFrame([
        {**common, "file_id": "f1", "hugo_symbol": "PIK3CA",
         "hgvsp_short": "p.H1047R", "start_position": 179234297},
        # Misma variante en muestra normal: debe rechazarse.
        {**common, "file_id": "f2", "hugo_symbol": "PIK3CA",
         "hgvsp_short": "p.E542K", "start_position": 179218294},
        # Gen fuera del panel: se excluye en target_panel.
        {**common, "file_id": "f3", "hugo_symbol": "TTN",
         "hgvsp_short": "p.R1000Q", "start_position": 1000},
    ])


def _maf_samples() -> pd.DataFrame:
    return pd.DataFrame([
        {"file_id": "f1", "tumor_sample_barcode": "TCGA-AA-0001-01A"},
        {"file_id": "f2", "tumor_sample_barcode": "TCGA-AA-0001-10A"},
        {"file_id": "f3", "tumor_sample_barcode": "TCGA-AA-0001-01A"},
    ])


def _manifest(cohort, mutations, clinical) -> dict:
    return {
        "contract_version": CONTRACT_VERSION,
        "gdc_data_release": "Data Release 99.0 - Test",
        "mutation_scope": "target_panel",
        "target_genes": ["PIK3CA", "TP53", "AKT1", "CDH1", "PTEN", "ESR1",
                         "GATA3", "MAP3K1", "ERBB2", "BRCA1", "BRCA2"],
        "data_origin": "gdc_open_access",
        "curated": False,
        "counts": {
            "cohort_cases": len(cohort),
            "mutation_records": len(mutations),
            "clinical_records": len(clinical),
        },
    }


def test_cohort_solo_hispanos():
    cohort = build_cohort(_cases(), _samples())
    assert len(cohort) == 1
    assert cohort[0]["case_code"] == "TCGA-AA-0001"
    assert cohort[0]["ethnicity"] == "HISPANIC OR LATINO"
    assert cohort[0]["race"] == "WHITE"
    assert cohort[0]["n_samples"] == 2


def test_mutaciones_muestra_tumoral_y_panel():
    cohort = build_cohort(_cases(), _samples())
    mutations, rejected = build_mutations(
        _mutations(), _maf_samples(), {c["case_code"] for c in cohort},
        "Data Release 99.0 - Test",
    )
    assert len(mutations) == 1
    assert mutations[0]["hugo_symbol"] == "PIK3CA"
    assert mutations[0]["protein_change"] == "H1047R"
    assert mutations[0]["sample_id"] == "TCGA-AA-0001-01A"
    assert mutations[0]["chromosome"] == "3"
    assert len(rejected) == 1
    assert rejected[0]["reason"].startswith("muestra no tumoral")


def test_clinica_nulos_explicitos():
    cases = _cases()
    cases.loc[0, "race"] = "not reported"
    cases.loc[0, "primary_diagnosis"] = "Not Reported"
    cases.loc[0, "ajcc_pathologic_stage"] = None
    cohort = build_cohort(cases, _samples())
    clinical = build_clinical(cohort, cases, "Data Release 99.0 - Test")
    by_attr = {(r["patientId"], r["clinicalAttributeId"]): r for r in clinical}
    assert by_attr[("TCGA-AA-0001", "RACE")]["value"] is None
    assert by_attr[("TCGA-AA-0001", "RACE")]["raw_value"] == "not reported"
    assert by_attr[("TCGA-AA-0001", "HISTOLOGY")]["value"] is None
    assert by_attr[("TCGA-AA-0001", "HISTOLOGY")]["raw_value"] == "Not Reported"
    assert by_attr[("TCGA-AA-0001", "STAGE")]["value"] is None
    assert len(clinical) == 6


def test_validacion_detecta_muestra_normal():
    cohort = build_cohort(_cases(), _samples())
    mutations, _ = build_mutations(
        _mutations(), _maf_samples(), {c["case_code"] for c in cohort},
        "Data Release 99.0 - Test",
    )
    clinical = build_clinical(cohort, _cases(), "Data Release 99.0 - Test")
    mutations[0]["sample_id"] = "TCGA-AA-0001-10A"
    errors = validate_contract(cohort, mutations, clinical, _manifest(cohort, mutations, clinical))
    assert any("sample_id no tumoral" in e for e in errors)


@pytest.mark.skipif(not REAL_DATA.exists(), reason="medallón real no disponible")
def test_bundle_real_contiene_m293k_y_brca2():
    from tcga.export_contract_v3 import export_bundle
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        result = export_bundle(
            data_dir=REAL_DATA, outdir=Path(tmp), offline=True,
            require_science=True,
        )
        manifest = result["manifest"]
        mutations = pd.read_json(str(result["path"] / "somatic_mutations.json"))
        assert manifest["contract_version"] == CONTRACT_VERSION
        assert manifest["validation"]["passed"] is True
        assert manifest["counts"]["cohort_cases"] == 39
        assert manifest["counts"]["mutation_records"] == 23
        assert manifest["counts"]["mutation_patients"] == 13
        proteins = set(zip(mutations["hugo_symbol"], mutations["protein_change"]))
        assert ("GATA3", "M293K") in proteins
        assert ("GATA3", "M294K") not in proteins
        assert ("BRCA2", "E1158Q") in proteins
        assert ("BRCA2", "K1191N") in proteins
        assert all(str(s).endswith(("01A", "01B", "01C", "06A"))
                   for s in mutations["sample_id"])
