"""Fixtures compartidas de las pruebas del servicio `flow`."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from tcga.config import Settings

from tcga_flow.settings import FlowSettings

REPO_ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = REPO_ROOT / "flow" / "workflows"


@pytest.fixture()
def settings(tmp_path: Path) -> FlowSettings:
    workflows_dir = tmp_path / "workflows"
    workflows_dir.mkdir()
    for path in WORKFLOWS.glob("*.json"):
        shutil.copy(path, workflows_dir / path.name)
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    etl = Settings(
        data_dir=data_dir,
        gdc_api_url="https://api.gdc.cancer.gov",
        projects=("TCGA-BRCA",),
        database_url="postgresql://invalid:invalid@127.0.0.1:1/none",
        duckdb_memory_limit="512MB",
        duckdb_threads=2,
        download_workers=1,
        download_batch_bytes=1_000_000,
        http_timeout=1,
        http_retries=1,
        http_backoff=0.1,
        log_level="WARNING",
        sql_dir=REPO_ROOT / "sql",
    )
    return FlowSettings(
        etl=etl,
        workflows_dir=workflows_dir,
        frontend_dist=tmp_path / "dist",
        gemamx_dir=tmp_path / "gemamx",
        flow_port=8100,
        log_level="WARNING",
    )


@pytest.fixture()
def workflow(settings: FlowSettings):
    from tcga_flow.workflows import load_workflow

    return load_workflow(settings, "tcga_v3")
