"""Tests para el pipeline GEMAMX v2 (Romero-Córdoba 2021 HM).

Criterios de aceptación numéricos del prompt:
- MOESM8 HM maf: 28 114 filas, 134 muestras
- MOESM9 Driver mutation in HM: 127 drivers con coordenadas genómicas
- Top variantes: 49, rank 1 = PIK3CA H1047R freq=15
- Mutation stats: total_mutations=28114, total_samples=134
- FASTA: 47 mutantes válidos, 2 cuarentena (GATA3 L328H, M294K)
"""

from __future__ import annotations

import json
from pathlib import Path

import pyarrow.parquet as pq
import pytest


GEMAMX_DIR = Path("D:/RespaldoLaptopHP/ProyectosIA/GEMAMX")
MOESM8 = GEMAMX_DIR / "data" / "romero" / "41467_2021_22478_MOESM8_ESM.xlsx"
MOESM9 = GEMAMX_DIR / "data" / "romero" / "41467_2021_22478_MOESM9_ESM.xlsx"

skip_no_gemamx = pytest.mark.skipif(
    not MOESM8.exists() or not MOESM9.exists(),
    reason="GEMAMX MOESM files not available",
)


@pytest.fixture
def data_dir(tmp_path: Path) -> Path:
    return tmp_path / "data"


@skip_no_gemamx
class TestGemamxV2Bronze:
    def test_read_moesm8_maf(self, data_dir: Path) -> None:
        from tcga_flow.gemamx.v2 import read_moesm8_maf

        res = read_moesm8_maf(GEMAMX_DIR, data_dir)
        assert res["total_rows"] == 28_114
        pq_file = data_dir / "gemamx" / "v2" / "bronze" / "romero_maf.parquet"
        assert pq_file.exists()
        table = pq.read_table(pq_file)
        assert table.num_rows == 28_114
        assert "hugo_symbol" in table.column_names
        assert "_sha256" in table.column_names

    def test_read_moesm9_drivers(self, data_dir: Path) -> None:
        from tcga_flow.gemamx.v2 import read_moesm9_drivers

        res = read_moesm9_drivers(GEMAMX_DIR, data_dir)
        assert res["total_rows"] == 127
        pq_file = data_dir / "gemamx" / "v2" / "bronze" / "romero_drivers.parquet"
        assert pq_file.exists()
        table = pq.read_table(pq_file)
        assert table.num_rows == 127


@skip_no_gemamx
class TestGemamxV2Silver:
    def _seed_bronze(self, data_dir: Path) -> None:
        from tcga_flow.gemamx.v2 import read_moesm8_maf, read_moesm9_drivers

        read_moesm8_maf(GEMAMX_DIR, data_dir)
        read_moesm9_drivers(GEMAMX_DIR, data_dir)

    def test_build_driver_lookup(self, data_dir: Path) -> None:
        from tcga_flow.gemamx.v2 import build_driver_lookup

        self._seed_bronze(data_dir)
        res = build_driver_lookup(data_dir)
        assert res["total_drivers"] == 127
        assert (data_dir / "gemamx" / "v2" / "silver" / "driver_lookup.parquet").exists()

    def test_normalize_romero_mutations(self, data_dir: Path) -> None:
        from tcga_flow.gemamx.v2 import build_driver_lookup, normalize_romero_mutations

        self._seed_bronze(data_dir)
        build_driver_lookup(data_dir)
        res = normalize_romero_mutations(data_dir)
        assert res["total_rows"] == 28_114
        assert res["drivers_matched"] == 167


@skip_no_gemamx
class TestGemamxV2Gold:
    def _seed_silver(self, data_dir: Path) -> None:
        from tcga_flow.gemamx.v2 import (
            build_driver_lookup,
            normalize_romero_mutations,
            read_moesm8_maf,
            read_moesm9_drivers,
        )

        read_moesm8_maf(GEMAMX_DIR, data_dir)
        read_moesm9_drivers(GEMAMX_DIR, data_dir)
        build_driver_lookup(data_dir)
        normalize_romero_mutations(data_dir)

    def test_compute_top_variants(self, data_dir: Path) -> None:
        from tcga_flow.gemamx.v2 import compute_top_variants

        self._seed_silver(data_dir)
        res = compute_top_variants(data_dir)

        assert res["top_variants_count"] == 49
        assert res["rank_1"]["hugo_symbol"] == "PIK3CA"
        assert res["rank_1"]["protein_change"] == "H1047R"
        assert res["rank_1"]["frequency"] == 15

        # Verificar contra gold de referencia del GEMAMXST
        gold_ref = GEMAMX_DIR / "GEMAMXST" / "v2" / "data" / "top_variants.json"
        if gold_ref.exists():
            gold = json.loads(gold_ref.read_text(encoding="utf-8"))
            computed = json.loads(
                (data_dir / "gemamx" / "v2" / "gold" / "top_variants.json").read_text(
                    encoding="utf-8"
                )
            )
            for i, (g, c) in enumerate(zip(gold, computed)):
                assert g["hugo_symbol"] == c["hugo_symbol"], f"Rank {i+1} gene mismatch"
                assert g["protein_change"] == c["protein_change"], f"Rank {i+1} pc mismatch"
                assert g["frequency"] == c["frequency"], f"Rank {i+1} freq mismatch"

    def test_compute_mutation_stats(self, data_dir: Path) -> None:
        from tcga_flow.gemamx.v2 import compute_mutation_stats

        self._seed_silver(data_dir)
        stats = compute_mutation_stats(data_dir)

        assert stats["total_mutations"] == 28_114
        assert stats["total_samples"] == 134
        assert stats["dataset"] == "Romero-Cordoba 2021 HM"

        # Verificar contra gold de referencia
        gold_ref = GEMAMX_DIR / "GEMAMXST" / "v2" / "data" / "mutation_stats.json"
        if gold_ref.exists():
            gold = json.loads(gold_ref.read_text(encoding="utf-8"))
            assert stats == gold

    def test_generate_fasta_variants(self, data_dir: Path) -> None:
        from tcga_flow.gemamx.v2 import compute_top_variants, generate_fasta_variants

        self._seed_silver(data_dir)
        compute_top_variants(data_dir)
        res = generate_fasta_variants(GEMAMX_DIR, data_dir)

        assert res["wt_files"] == 7  # 7 genes con WT
        assert res["mut_files"] == 47  # 47 mutantes válidos
        assert res["quarantined"] == 2  # GATA3 L328H y M294K
        assert res["total_fasta_files"] == 54  # 7 WT + 47 MUT

        # Verificar que PIK3CA_H1047R.fasta existe y tiene la mutación correcta
        fasta_dir = data_dir / "gemamx" / "v2" / "gold" / "fasta"
        pik3ca_mut = fasta_dir / "PIK3CA_H1047R.fasta"
        assert pik3ca_mut.exists()
        lines = pik3ca_mut.read_text(encoding="utf-8").splitlines()
        seq = "".join(lines[1:])
        assert seq[1046] == "R"  # posición 1047 (0-indexed = 1046) debe ser R

        # WT debe tener H en la misma posición
        pik3ca_wt = fasta_dir / "PIK3CA_WT.fasta"
        wt_lines = pik3ca_wt.read_text(encoding="utf-8").splitlines()
        wt_seq = "".join(wt_lines[1:])
        assert wt_seq[1046] == "H"


@skip_no_gemamx
class TestGemamxV2Workflow:
    """Test de integración del workflow JSON."""

    def test_workflow_loads_and_validates(self) -> None:
        from tcga_flow.schemas import WorkflowDefinition
        from tcga_flow.workflows import validate_workflow

        wf_path = Path(__file__).parent.parent / "workflows" / "gemamx_v2.json"
        raw = json.loads(wf_path.read_text(encoding="utf-8"))
        wf = WorkflowDefinition.model_validate(raw)
        validate_workflow(wf)

        assert wf.id == "gemamx_v2"
        assert len(wf.nodes) == 18
        assert len(wf.edges) == 17

