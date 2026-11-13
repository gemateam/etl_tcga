"""Tests para el pipeline GEMAMX v2.1 (Romero-Córdoba 2021 HM con enriquecimiento clínico M5)."""

from __future__ import annotations

import json
from pathlib import Path

import pyarrow.parquet as pq
import pytest

GEMAMX_DIR = Path("D:/RespaldoLaptopHP/ProyectosIA/GEMAMX")
MOESM4 = GEMAMX_DIR / "data" / "romero" / "41467_2021_22478_MOESM4_ESM.xlsx"
MOESM8 = GEMAMX_DIR / "data" / "romero" / "41467_2021_22478_MOESM8_ESM.xlsx"
MOESM9 = GEMAMX_DIR / "data" / "romero" / "41467_2021_22478_MOESM9_ESM.xlsx"

skip_no_gemamx = pytest.mark.skipif(
    not MOESM4.exists() or not MOESM8.exists() or not MOESM9.exists(),
    reason="Archivos MOESM de GEMAMX no disponibles",
)


@pytest.fixture
def data_dir(tmp_path: Path) -> Path:
    return tmp_path / "data"


@skip_no_gemamx
class TestGemamxV21Clinical:
    def test_read_moesm4_clinical(self, data_dir: Path) -> None:
        from tcga_flow.gemamx.v2_1 import read_moesm4_clinical

        res = read_moesm4_clinical(GEMAMX_DIR, data_dir)
        assert res["total_samples"] == 204
        pq_file = data_dir / "gemamx" / "v2_1" / "bronze" / "romero_clinical_raw.parquet"
        assert pq_file.exists()

    def test_normalize_clinical_metadata(self, data_dir: Path) -> None:
        from tcga_flow.gemamx.v2_1 import normalize_clinical_metadata, read_moesm4_clinical

        read_moesm4_clinical(GEMAMX_DIR, data_dir)
        res = normalize_clinical_metadata(data_dir)
        assert res["normalized_samples"] == 204
        pq_file = data_dir / "gemamx" / "v2_1" / "silver" / "clinical_metadata.parquet"
        assert pq_file.exists()

    def test_compute_variant_clinical_summary(self, data_dir: Path) -> None:
        from tcga_flow.gemamx.v2 import (
            build_driver_lookup,
            compute_top_variants,
            normalize_romero_mutations,
            read_moesm8_maf,
            read_moesm9_drivers,
        )
        from tcga_flow.gemamx.v2_1 import (
            compute_variant_clinical_summary,
            normalize_clinical_metadata,
            read_moesm4_clinical,
        )

        # 1. Pipeline de mutaciones (v2)
        read_moesm8_maf(GEMAMX_DIR, data_dir)
        read_moesm9_drivers(GEMAMX_DIR, data_dir)
        build_driver_lookup(data_dir)
        normalize_romero_mutations(data_dir)
        compute_top_variants(data_dir)

        # 2. Pipeline clínico (v2.1)
        read_moesm4_clinical(GEMAMX_DIR, data_dir)
        normalize_clinical_metadata(data_dir)

        # 3. Resumen clínico conjunto
        res = compute_variant_clinical_summary(data_dir)
        assert res["variants_summarized"] == 49

        # Validar consistencia de PIK3CA H1047R
        json_file = Path(res["json_file"])
        summaries = json.loads(json_file.read_text(encoding="utf-8"))
        h1047r = summaries[0]

        assert h1047r["hugo_symbol"] == "PIK3CA"
        assert h1047r["protein_change"] == "H1047R"
        assert h1047r["sample_count"] == 15
        assert h1047r["matched_clinical_count"] == 15
        assert h1047r["age"]["mean"] == pytest.approx(56.71, abs=0.1)
        assert h1047r["highlights"]["top_ihc_subtype"] == "Luminal A"
        assert h1047r["highlights"]["top_pam50"] == "LumA"


class TestGemamxV21Workflow:
    def test_workflow_definition_loads(self) -> None:
        from tcga_flow.schemas import WorkflowDefinition
        from tcga_flow.workflows import validate_workflow

        wf_path = Path(__file__).parent.parent / "workflows" / "gemamx_v2_1.json"
        raw = json.loads(wf_path.read_text(encoding="utf-8"))
        wf = WorkflowDefinition.model_validate(raw)
        validate_workflow(wf)

        assert wf.id == "gemamx_v2_1"
        assert len(wf.nodes) == 20
        assert len(wf.edges) == 19

