from __future__ import annotations

from fastapi.testclient import TestClient

from app import analytics, db
from app.main import app

client = TestClient(app)


def _empty(monkeypatch) -> None:
    monkeypatch.setattr(db, "safe_query", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(db, "safe_scalar", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(db, "ping", lambda: True)
    monkeypatch.setattr(analytics, "expression_file_stats", lambda: {"files": 0, "rows": 0})


def test_health_ok(monkeypatch) -> None:
    monkeypatch.setattr(db, "ping", lambda: True)

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_health_unhealthy(monkeypatch) -> None:
    def fail() -> None:
        raise RuntimeError("sin base de datos")

    monkeypatch.setattr(db, "ping", fail)

    response = client.get("/health")

    assert response.status_code == 503
    assert response.json()["status"] == "unhealthy"


def test_empty_state_pages_render_without_database(monkeypatch) -> None:
    _empty(monkeypatch)

    for path in ("/", "/casos", "/genes", "/reportes"):
        response = client.get(path)
        assert response.status_code == 200, path
        assert "TCGA Medallion" in response.text

    response = client.get("/reportes")
    assert "Todavía no hay datos gold" in response.text


def test_missing_case_returns_404_with_clear_message(monkeypatch) -> None:
    _empty(monkeypatch)

    response = client.get("/casos/no-existe")

    assert response.status_code == 404
    assert "No hay información para mostrar" in response.text


def test_gene_api_returns_empty_list_without_data(monkeypatch) -> None:
    _empty(monkeypatch)

    response = client.get("/api/genes?q=ESR1")

    assert response.status_code == 200
    assert response.json() == []
