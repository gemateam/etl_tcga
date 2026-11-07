"""Prueba de humo contra el GDC real y la base del pipeline.

Se ejecuta con ``make smoke`` después de descargar cinco archivos reales.
"""

from __future__ import annotations

import pytest

from tcga.db import connect
from tcga.ops.runs import get_gdc_status

pytestmark = [pytest.mark.smoke]


def test_gdc_status_is_reachable() -> None:
    status = get_gdc_status()

    assert status is not None
    assert status.get("data_release")


def test_smoke_pipeline_has_all_layers() -> None:
    with connect() as conn:
        complete = conn.execute(
            "SELECT count(*) AS n FROM ops.download_control WHERE status = 'complete'"
        ).fetchone()["n"]
        cases = conn.execute("SELECT count(*) AS n FROM silver.cases").fetchone()["n"]
        expressions = conn.execute("SELECT count(*) AS n FROM gold.gene_stats").fetchone()["n"]
        pivot = conn.execute("SELECT count(*) AS n FROM gold.cx_pivot").fetchone()["n"]
        failures = conn.execute(
            """
            SELECT count(*) AS n FROM (
                SELECT DISTINCT ON (step) status
                FROM ops.pipeline_runs
                ORDER BY step, started_at DESC
            ) latest
            WHERE status = 'failed'
            """
        ).fetchone()["n"]
        invalid_md5 = conn.execute(
            "SELECT count(*) AS n FROM ops.download_control WHERE status = 'complete' AND md5_ok IS NOT TRUE"
        ).fetchone()["n"]

    assert complete >= 5
    assert cases > 0
    assert expressions > 0
    assert pivot > 0
    assert failures == 0
    assert invalid_md5 == 0
