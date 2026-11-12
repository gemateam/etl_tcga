from __future__ import annotations

import pytest

from tcga_flow.engine import BusyError, Engine, EngineError, _parse_log_line
from tcga_flow.state import StateService


@pytest.fixture()
def engine(settings) -> Engine:
    return Engine(settings, StateService(settings))


def test_plan_until(engine, workflow):
    plan = engine.plan(workflow, until="gold_marker_genes")
    assert plan[0] == "extract_cases"
    assert plan[-1] == "gold_marker_genes"
    assert "gold_gene_dim" in plan


def test_reset_cascade(engine, workflow):
    invalidated = engine.reset(workflow, ["gold_gene_dim"])
    assert "gold_gene_dim" in invalidated
    assert "gold_marker_genes" in invalidated
    assert "gold_sample_marker_values" in invalidated
    assert "gold_cx_pivot" not in invalidated


def test_reset_all(engine, workflow):
    assert len(engine.reset(workflow, [])) == 18


def test_reset_unknown_node(engine, workflow):
    with pytest.raises(EngineError):
        engine.reset(workflow, ["no_existe"])


def test_busy_lock(engine, workflow):
    engine._lock.acquire()
    try:
        with pytest.raises(BusyError):
            engine.start(workflow, ["extract_cases"], "TCGA-BRCA")
    finally:
        engine._lock.release()


def test_cancel_without_execution(engine):
    with pytest.raises(EngineError):
        engine.cancel()


def test_execute_runs_plan_and_clears_invalidation(engine, workflow, monkeypatch):
    calls: list[str] = []

    def fake_run(node_id, node):
        calls.append(node_id)
        return 0, {"ok": True, "rows_out": 7}, None

    monkeypatch.setattr(engine, "_run_node", fake_run)
    engine.start(workflow, ["extract_cases", "bronze_cases"], "TCGA-BRCA")
    engine.wait(timeout=10)
    payload = engine.execution_payload()
    assert payload["execution"] == "idle"
    assert calls == ["extract_cases", "bronze_cases"]
    assert payload["done"] == ["extract_cases", "bronze_cases"]


def test_execute_stops_on_error(engine, workflow, monkeypatch):
    calls: list[str] = []

    def fake_run(node_id, node):
        calls.append(node_id)
        return 1, {"ok": False, "error": "boom"}, "boom"

    monkeypatch.setattr(engine, "_run_node", fake_run)
    engine.start(workflow, ["extract_cases", "bronze_cases"], "TCGA-BRCA")
    engine.wait(timeout=10)
    payload = engine.execution_payload()
    assert payload["execution"] == "idle"
    assert calls == ["extract_cases"]
    assert payload["last_error"] == "boom"


def test_parse_log_line():
    assert _parse_log_line('{"level": "INFO", "message": "hola"}') == ("INFO", "hola")
    assert _parse_log_line('{"level": "INFO", "message": "hola", "detail": "x"}') == (
        "INFO",
        "hola · x",
    )
    assert _parse_log_line("texto plano") == ("INFO", "texto plano")
