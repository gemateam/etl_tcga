"""Integración real sobre PostgreSQL con un landing mínimo de ejemplo.

Se ejecuta solo cuando ``TCGA_INTEGRATION=1``. Crea una base de datos
``tcga_test`` usando las credenciales de administrador de PostgreSQL que
ya están en el entorno del contenedor y la elimina al terminar.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
from pathlib import Path

import psycopg
import pytest
from psycopg.rows import dict_row

from tcga.bronze.cases import bronze_cases, bronze_files
from tcga.bronze.expressions import bronze_expressions
from tcga.config import Settings
from tcga.db import init_schema
from tcga.gold.cx_pivot import gold_cx_pivot
from tcga.gold.extras import gene_stats, sample_summary
from tcga.gold.top_diagnosis import gold_top_20_diagnosis
from tcga.silver.case_expressions import silver_case_expressions
from tcga.silver.clinical import silver_clinical

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("TCGA_INTEGRATION") != "1",
        reason="define TCGA_INTEGRATION=1 y levanta PostgreSQL para ejecutarla",
    ),
]

FIXTURE = Path(__file__).parent / "fixtures" / "expression_sample.tsv"
FILE_IDS = (
    "11111111-1111-1111-1111-111111111111",
    "22222222-2222-2222-2222-222222222222",
)
CASE_IDS = (
    "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
    "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
)
SAMPLE_IDS = (
    "cccccccc-cccc-cccc-cccc-cccccccccccc",
    "dddddddd-dddd-dddd-dddd-dddddddddddd",
)


def _pg_parts() -> tuple[str, str, str, str, str, str]:
    return (
        os.getenv("POSTGRES_HOST", "postgres"),
        os.getenv("POSTGRES_PORT", "5432"),
        os.getenv("POSTGRES_USER", "tcga_admin"),
        os.getenv("POSTGRES_PASSWORD", ""),
        os.getenv("ETL_DB_USER", "etl_rw"),
        os.getenv("ETL_DB_PASSWORD", ""),
    )


def _database_url(user: str, password: str, host: str, port: str, database: str) -> str:
    return f"postgresql://{user}:{password}@{host}:{port}/{database}"


def _prepare_database() -> str:
    host, port, admin_user, admin_password, etl_user, etl_password = _pg_parts()
    admin_url = _database_url(admin_user, admin_password, host, port, "postgres")
    with psycopg.connect(admin_url, autocommit=True) as conn:
        conn.execute("DROP DATABASE IF EXISTS tcga_test WITH (FORCE)")
        conn.execute(f'CREATE DATABASE tcga_test OWNER "{etl_user}"')
    return _database_url(etl_user, etl_password, host, port, "tcga_test")


def _drop_database() -> None:
    host, port, admin_user, admin_password, _, _ = _pg_parts()
    admin_url = _database_url(admin_user, admin_password, host, port, "postgres")
    try:
        with psycopg.connect(admin_url, autocommit=True) as conn:
            conn.execute("DROP DATABASE IF EXISTS tcga_test WITH (FORCE)")
    except psycopg.Error:
        pass


def _case_hit(index: int) -> dict:
    return {
        "case_id": CASE_IDS[index],
        "submitter_id": f"TCGA-{index:02d}-0001",
        "primary_site": "Breast",
        "disease_type": "Ductal and Lobular Neoplasms",
        "consent_type": "Informed Consent",
        "project": {
            "project_id": "TCGA-BRCA",
            "name": "Breast Invasive Carcinoma",
        },
        "demographic": {
            "demographic_id": f"demo-{index}",
            "sex_at_birth": "Female",
            "race": "white",
            "ethnicity": "not hispanic or latino",
            "vital_status": "Alive" if index == 0 else "Dead",
            "days_to_death": None if index == 0 else 900,
            "days_to_birth": -15000,
            "age_at_index": 55 if index == 0 else 61,
            "year_of_birth": 1960 if index == 0 else 1955,
            "year_of_death": None if index == 0 else 2018,
        },
        "diagnoses": [
            {
                "diagnosis_id": f"dx-{index}",
                "submitter_id": f"TCGA-{index:02d}-0001-DX",
                "primary_diagnosis": "Infiltrating duct carcinoma, NOS",
                "ajcc_pathologic_stage": "Stage IIA",
                "age_at_diagnosis": 55,
                "days_to_last_follow_up": 1200,
                "diagnosis_is_primary_disease": True,
                "treatments": [],
            }
        ],
        "exposures": [
            {
                "exposure_id": f"ex-{index}",
                "alcohol_history": "No",
                "cigarettes_per_day": 0,
            }
        ],
        "samples": [
            {
                "sample_id": SAMPLE_IDS[index],
                "submitter_id": f"TCGA-{index:02d}-0001-01A",
                "sample_type": "Primary Tumor",
                "tissue_type": "Tumor",
            }
        ],
    }


def _manifest_hit(index: int, path: Path) -> dict:
    payload = path.read_bytes()
    return {
        "file_id": FILE_IDS[index],
        "file_name": path.name,
        "file_size": len(payload),
        "md5sum": hashlib.md5(payload).hexdigest(),
        "access": "open",
        "data_category": "Transcriptome Profiling",
        "data_type": "Gene Expression Quantification",
        "data_format": "TSV",
        "experimental_strategy": "RNA-Seq",
        "analysis": {"workflow_type": "STAR - Counts"},
        "cases": [
            {
                "case_id": CASE_IDS[index],
                "project": {"project_id": "TCGA-BRCA"},
                "samples": [
                    {
                        "sample_id": SAMPLE_IDS[index],
                        "submitter_id": f"TCGA-{index:02d}-0001-01A",
                        "sample_type": "Primary Tumor",
                    }
                ],
            }
        ],
    }


def _write_landing(root: Path) -> Settings:
    landing_cases = root / "landing" / "cases" / "TCGA-BRCA"
    landing_files = root / "landing" / "files" / "TCGA-BRCA"
    landing_expr = root / "landing" / "expressions" / "TCGA-BRCA"
    landing_cases.mkdir(parents=True)
    landing_files.mkdir(parents=True)

    page = {"data": {"hits": [_case_hit(0), _case_hit(1)], "pagination": {"total": 2}}}
    (landing_cases / "page_1.json").write_text(json.dumps(page), encoding="utf-8")

    expression_paths = []
    for index, file_id in enumerate(FILE_IDS):
        target_dir = landing_expr / file_id
        target_dir.mkdir(parents=True)
        target = target_dir / f"expr_{index}.tsv"
        content = FIXTURE.read_text(encoding="utf-8")
        if index == 1:
            content = content.replace("17.2434", "170.0000").replace("0.0944", "9.4400")
        target.write_text(content, encoding="utf-8")
        expression_paths.append(target)

    manifest = {
        "project_id": "TCGA-BRCA",
        "data_release": "test-release",
        "hits": [_manifest_hit(i, path) for i, path in enumerate(expression_paths)],
    }
    (landing_files / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    return Settings(
        data_dir=root,
        gdc_api_url="https://api.test",
        projects=("TCGA-BRCA",),
        database_url=os.environ["DATABASE_URL"],
        duckdb_memory_limit="1GB",
        duckdb_threads=1,
        download_workers=1,
        download_batch_bytes=1_000_000,
        http_timeout=1,
        http_retries=1,
        http_backoff=0,
        log_level="INFO",
    )


def test_pipeline_end_to_end(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    test_url = _prepare_database()
    monkeypatch.setenv("DATABASE_URL", test_url)
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("GDC_API_URL", "https://api.test")
    monkeypatch.setattr("tcga.ops.runs.get_gdc_status", lambda: None)

    try:
        init_schema()
        settings = _write_landing(tmp_path)
        projects = ["TCGA-BRCA"]

        bronze_cases(projects, settings)
        bronze_files(projects, settings)
        bronze_expressions(projects, settings)
        silver_clinical(projects, settings)
        silver_case_expressions(projects, settings)
        gold_top_20_diagnosis(projects, settings)
        sample_summary(projects, settings)
        gene_stats(projects, settings)
        gold_cx_pivot(projects, top_genes=3, settings=settings)

        with psycopg.connect(test_url, row_factory=dict_row) as conn:
            counts = {
                "bronze_cases_raw": conn.execute(
                    "SELECT count(*) AS n FROM bronze.cases_raw"
                ).fetchone()["n"],
                "silver_cases": conn.execute("SELECT count(*) AS n FROM silver.cases").fetchone()["n"],
                "silver_diagnoses": conn.execute(
                    "SELECT count(*) AS n FROM silver.cases_diagnoses"
                ).fetchone()["n"],
                "silver_samples": conn.execute(
                    "SELECT count(*) AS n FROM silver.samples"
                ).fetchone()["n"],
                "silver_files": conn.execute("SELECT count(*) AS n FROM silver.files").fetchone()["n"],
                "gold_top": conn.execute(
                    "SELECT count(*) AS n FROM gold.top_20_diagnosis"
                ).fetchone()["n"],
                "gold_stats": conn.execute("SELECT count(*) AS n FROM gold.gene_stats").fetchone()["n"],
                "gold_pivot": conn.execute("SELECT count(*) AS n FROM gold.cx_pivot").fetchone()["n"],
                "critical_failures": conn.execute(
                    "SELECT count(*) AS n FROM ops.dq_results WHERE severity = 'critical' AND NOT passed"
                ).fetchone()["n"],
            }

        assert counts["bronze_cases_raw"] == 2
        assert counts["silver_cases"] == 2
        assert counts["silver_diagnoses"] == 2
        assert counts["silver_samples"] == 2
        assert counts["silver_files"] == 2
        assert counts["gold_top"] >= 1
        assert counts["gold_stats"] > 0
        assert counts["gold_pivot"] == 2
        assert counts["critical_failures"] == 0
    finally:
        shutil.rmtree(tmp_path, ignore_errors=True)
        _drop_database()
