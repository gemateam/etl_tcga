from __future__ import annotations

import json

import pytest

from tcga_flow.preview import PreviewError, PreviewService


def _node(workflow, node_id):
    return next(node for node in workflow.nodes if node.id == node_id)


def test_preview_json(settings, workflow):
    target = settings.data_dir() / "landing" / "cases" / "TCGA-BRCA"
    target.mkdir(parents=True)
    (target / "page_1.json").write_text(
        json.dumps([{"case_id": "a", "state": "TX"}, {"case_id": "b", "state": "CA"}]),
        encoding="utf-8",
    )
    service = PreviewService(settings)
    payload = service.preview(_node(workflow, "extract_cases"), page=1, page_size=1)
    assert payload["total_rows"] == 2
    assert payload["rows"] == [{"case_id": "a", "state": "TX"}]
    assert {column["name"] for column in payload["columns"]} == {"case_id", "state"}


def test_preview_parquet(settings, workflow):
    from tcga.duck import duck_connect, sql_literal

    target = settings.data_dir() / "gold" / "gene_stats"
    target.mkdir(parents=True)
    out = target / "data.parquet"
    con = duck_connect(settings.etl)
    try:
        con.execute(
            f"COPY (SELECT 'ENSG1' AS gene_id, 1.5 AS mean UNION ALL "
            f"SELECT 'ENSG2', 2.5) TO {sql_literal(out.as_posix())} (FORMAT PARQUET)"
        )
    finally:
        con.close()

    service = PreviewService(settings)
    payload = service.preview(
        _node(workflow, "gold_gene_stats"), output_key="parquet", page=1, page_size=10
    )
    assert payload["total_rows"] == 2
    assert payload["columns"][0]["name"] == "gene_id"
    assert len(payload["rows"]) == 2


def test_preview_missing_output(settings, workflow):
    service = PreviewService(settings)
    with pytest.raises(PreviewError):
        service.preview(_node(workflow, "extract_cases"))


def test_preview_unknown_output(settings, workflow):
    service = PreviewService(settings)
    with pytest.raises(PreviewError):
        service.preview(_node(workflow, "gold_cx_pivot"), output_key="nope")


@pytest.mark.integration
def test_preview_table_integration(settings, workflow):
    service = PreviewService(settings)
    payload = service.preview(_node(workflow, "gold_gene_dim"))
    assert payload["kind"] == "table"
