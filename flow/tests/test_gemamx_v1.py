"""Tests para el pipeline GEMAMX v1 (TCGA-BRCA cBioPortal)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest


GEMAMX_DIR = Path("D:/RespaldoLaptopHP/ProyectosIA/GEMAMX")


@pytest.fixture
def data_dir(tmp_path: Path) -> Path:
    return tmp_path / "data"


class TestGemamxV1Bronze:
    def test_fetch_mutations(self, data_dir: Path) -> None:
        from tcga_flow.gemamx.v1 import fetch_mutations

        res = fetch_mutations(GEMAMX_DIR, data_dir)
        assert res["total_rows"] > 1000
        assert "cBioPortal" in res["source"]
        pq_file = data_dir / "gemamx" / "v1" / "bronze" / "cbio_mutations.parquet"
        assert pq_file.exists()

    def test_fetch_genes(self, data_dir: Path) -> None:
        from tcga_flow.gemamx.v1 import fetch_genes

        res = fetch_genes(data_dir)
        assert res["total_genes"] == 15


class TestGemamxV1Silver:
    def _seed_bronze(self, data_dir: Path) -> None:
        from tcga_flow.gemamx.v1 import fetch_genes, fetch_mutations, fetch_uniprot_fasta

        fetch_mutations(GEMAMX_DIR, data_dir)
        fetch_genes(data_dir)
        fetch_uniprot_fasta(GEMAMX_DIR, data_dir)

    def test_normalize_v1_mutations(self, data_dir: Path) -> None:
        from tcga_flow.gemamx.v1 import normalize_v1_mutations

        self._seed_bronze(data_dir)
        res = normalize_v1_mutations(data_dir)
        assert res["total_rows"] > 1000


class TestGemamxV1Gold:
    def _seed_silver(self, data_dir: Path) -> None:
        from tcga_flow.gemamx.v1 import (
            fetch_genes,
            fetch_mutations,
            fetch_uniprot_fasta,
            normalize_v1_mutations,
            wt_sequences,
        )

        fetch_mutations(GEMAMX_DIR, data_dir)
        fetch_genes(data_dir)
        fetch_uniprot_fasta(GEMAMX_DIR, data_dir)
        normalize_v1_mutations(data_dir)
        wt_sequences(data_dir)

    def test_compute_v1_top_variants(self, data_dir: Path) -> None:
        from tcga_flow.gemamx.v1 import compute_v1_top_variants

        self._seed_silver(data_dir)
        res = compute_v1_top_variants(data_dir)

        assert res["top_variants_count"] <= 20
        assert res["rank_1"]["hugo_symbol"] == "PIK3CA"
        assert res["rank_1"]["protein_change"] == "H1047R"

    def test_compute_v1_mutation_stats(self, data_dir: Path) -> None:
        from tcga_flow.gemamx.v1 import compute_v1_mutation_stats

        self._seed_silver(data_dir)
        stats = compute_v1_mutation_stats(data_dir)
        assert stats["total_mutations"] > 1000
        assert stats["total_samples"] > 500

    def test_generate_v1_fasta_variants(self, data_dir: Path) -> None:
        from tcga_flow.gemamx.v1 import compute_v1_top_variants, generate_v1_fasta_variants

        self._seed_silver(data_dir)
        compute_v1_top_variants(data_dir)
        res = generate_v1_fasta_variants(data_dir)

        assert res["valid_variants"] >= 0
        assert res["quarantined"] >= 0


class TestGemamxV1Workflow:
    """Test del workflow JSON sin datos."""

    def test_workflow_loads_and_validates(self) -> None:
        from tcga_flow.schemas import WorkflowDefinition
        from tcga_flow.workflows import validate_workflow

        wf_path = Path(__file__).parent.parent / "workflows" / "gemamx_v1.json"
        raw = json.loads(wf_path.read_text(encoding="utf-8"))
        wf = WorkflowDefinition.model_validate(raw)
        validate_workflow(wf)

        assert wf.id == "gemamx_v1"
        assert len(wf.nodes) == 5
        assert len(wf.edges) == 3

