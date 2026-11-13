from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from tcga_flow.main import create_app


@pytest.fixture()
def app(settings):
    application = create_app(settings)
    application.state.state_service.latest_runs = lambda steps, project: {}
    application.state.state_service.outputs_present = lambda node: ([], False)
    return application


@pytest.fixture()
def client(app):
    with TestClient(app) as test_client:
        yield test_client


def test_health(client):
    payload = client.get("/health").json()
    assert payload["status"] == "ok"


def test_config_lists_workflows(client):
    payload = client.get("/api/flow/config").json()
    assert sorted([item["id"] for item in payload["workflows"]]) == ["gemamx_v1", "gemamx_v2", "gemamx_v2_1", "tcga_v3"]
    assert payload["layers"][0]["color"] == "#0f766e"
    assert payload["states"]["executed"] == "#15803d"


def test_catalog_has_tcga_nodes(client):
    payload = client.get("/api/flow/catalog").json()
    keys = {item["key"] for item in payload["nodes"]}
    assert "gold_marker_genes" in keys
    assert "gold_cx_pivot" in keys
    assert len(keys) == 47  # 18 TCGA + 18 GEMAMX-v2 granular + 8 GEMAMX-v1 + 3 GEMAMX-v2.1


def test_workflow_detail(client):
    payload = client.get("/api/flow/workflows/tcga_v3").json()
    assert len(payload["nodes"]) == 18
    assert len(payload["edges"]) == 20


def test_workflow_not_found(client):
    assert client.get("/api/flow/workflows/nope").status_code == 404


def test_state_endpoint(client):
    payload = client.get("/api/flow/workflows/tcga_v3/state").json()
    assert payload["execution"] == "idle"
    assert len(payload["nodes"]) == 18
    states = {node["id"]: node["state"] for node in payload["nodes"]}
    assert states["extract_cases"] == "configured"
    assert states["gold_cx_pivot"] == "idle"


def test_lineage(client):
    payload = client.get("/api/flow/workflows/tcga_v3/lineage/gold_cx_pivot").json()
    ids = [node["id"] for node in payload["nodes"]]
    assert "extract_files_manifest" in ids
    assert "gold_cx_pivot" in ids
    assert ids.index("extract_files_manifest") < ids.index("gold_cx_pivot")


def test_export_json_and_mermaid(client):
    exported = client.get("/api/flow/workflows/tcga_v3/export.json").json()
    assert exported["id"] == "tcga_v3"
    mermaid = client.get("/api/flow/workflows/tcga_v3/export.mermaid").text
    assert mermaid.startswith("flowchart LR")
    assert "extract_cases --> bronze_cases" in mermaid


def test_patch_node(client, settings):
    response = client.patch(
        "/api/flow/workflows/tcga_v3/nodes/gold_cx_pivot",
        json={"label": "Pivote (editado)", "notes": "nota de prueba"},
    )
    assert response.status_code == 200
    payload = response.json()
    node = next(item for item in payload["nodes"] if item["id"] == "gold_cx_pivot")
    assert node["label"] == "Pivote (editado)"
    assert node["notes"] == "nota de prueba"


def test_patch_invalid_params(client):
    response = client.patch(
        "/api/flow/workflows/tcga_v3/nodes/gold_cx_pivot",
        json={"params": {"top_genes": 0}},
    )
    assert response.status_code == 422


def test_execute_requires_executed_ancestors(client):
    response = client.post(
        "/api/flow/workflows/tcga_v3/execute",
        json={"nodes": ["gold_cx_pivot"]},
    )
    assert response.status_code == 409
    assert "ancestros" in response.json()["detail"]


def test_execute_all_and_reset(client, app):
    app.state.engine._run_node = lambda node_id, node: (0, {"ok": True, "rows_out": 1}, None)
    response = client.post("/api/flow/workflows/tcga_v3/execute", json={"all": True})
    assert response.status_code == 200
    app.state.engine.wait(timeout=10)
    reset = client.post("/api/flow/workflows/tcga_v3/reset", json=["gold_gene_dim"])
    assert reset.status_code == 200
    invalidated = set(reset.json()["invalidated"])
    assert {"gold_gene_dim", "gold_marker_genes"} <= invalidated


def test_execute_unknown_node(client):
    response = client.post(
        "/api/flow/workflows/tcga_v3/execute", json={"nodes": ["no_existe"]}
    )
    assert response.status_code == 404


def test_output_missing(client):
    response = client.get(
        "/api/flow/workflows/tcga_v3/nodes/extract_cases/output"
    )
    assert response.status_code == 404


def test_log_endpoint(client):
    payload = client.get("/api/flow/logs").json()
    assert payload["lines"] == []
