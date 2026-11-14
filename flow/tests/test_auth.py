"""Autenticación HTTP Basic opcional del lienzo."""

from __future__ import annotations

from fastapi.testclient import TestClient

from tcga_flow.main import create_app


def _client(settings) -> TestClient:
    application = create_app(settings)
    application.state.state_service.latest_runs = lambda steps, project: {}
    application.state.state_service.outputs_present = lambda node: ([], False)
    return TestClient(application)


def test_open_without_credentials(settings, monkeypatch):
    monkeypatch.delenv("FLOW_AUTH_USER", raising=False)
    monkeypatch.delenv("FLOW_AUTH_PASSWORD", raising=False)
    client = _client(settings)
    assert client.get("/health").status_code == 200
    assert client.get("/api/flow/config").status_code == 200


def test_requires_basic_auth(settings, monkeypatch):
    monkeypatch.setenv("FLOW_AUTH_USER", "operador")
    monkeypatch.setenv("FLOW_AUTH_PASSWORD", "secreto")
    client = _client(settings)

    assert client.get("/health").status_code == 200
    assert client.get("/api/flow/config").status_code == 401

    ok = client.get("/api/flow/config", auth=("operador", "secreto"))
    assert ok.status_code == 200
    assert ok.json()["workflows"]

    assert client.get("/api/flow/config", auth=("operador", "mal")).status_code == 401


def test_partial_credentials_leave_service_open(settings, monkeypatch):
    monkeypatch.setenv("FLOW_AUTH_USER", "operador")
    monkeypatch.delenv("FLOW_AUTH_PASSWORD", raising=False)
    client = _client(settings)
    assert client.get("/api/flow/config").status_code == 200
