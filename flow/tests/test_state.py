from __future__ import annotations

from datetime import datetime, timedelta, timezone

from tcga_flow.schemas import NodeState
from tcga_flow.state import StateService


def _service(settings, runs=None):
    service = StateService(settings)
    service.latest_runs = lambda steps, project: runs or {}
    service.outputs_present = lambda node: ([], False)
    return service


def test_initial_states(settings, workflow):
    service = _service(settings)
    states = service.compute(workflow)
    # Los nodos sin entradas están configurados; el resto espera dependencias.
    assert states["extract_cases"].state is NodeState.CONFIGURED
    assert states["extract_files_manifest"].state is NodeState.CONFIGURED
    assert states["bronze_cases"].state is NodeState.IDLE
    assert states["gold_cx_pivot"].state is NodeState.IDLE


def test_success_run_marks_executed(settings, workflow):
    now = datetime.now(timezone.utc)
    runs = {
        "extract_cases": {
            "step": "extract_cases",
            "run_id": "r1",
            "status": "success",
            "rows_in": 0,
            "rows_out": 1098,
            "started_at": now - timedelta(seconds=5),
            "finished_at": now,
            "error": None,
        }
    }
    service = _service(settings, runs)
    states = service.compute(workflow)
    assert states["extract_cases"].state is NodeState.EXECUTED
    assert states["bronze_cases"].state is NodeState.CONFIGURED


def test_chain_of_success_marks_children_configured(settings, workflow):
    now = datetime.now(timezone.utc)

    def run(step, rows=1):
        return {
            "step": step,
            "run_id": step,
            "status": "success",
            "rows_in": rows,
            "rows_out": rows,
            "started_at": now - timedelta(seconds=5),
            "finished_at": now,
            "error": None,
        }

    runs = {
        "extract_cases": run("extract_cases"),
        "bronze_cases": run("bronze_cases"),
        "bronze_files": run("bronze_files"),
    }
    service = _service(settings, runs)
    states = service.compute(workflow)
    assert states["extract_cases"].state is NodeState.EXECUTED
    assert states["bronze_cases"].state is NodeState.EXECUTED
    assert states["silver_clinical"].state is NodeState.CONFIGURED


def test_failed_run_marks_error(settings, workflow):
    now = datetime.now(timezone.utc)
    runs = {
        "extract_cases": {
            "step": "extract_cases",
            "run_id": "r1",
            "status": "failed",
            "rows_in": 0,
            "rows_out": 0,
            "started_at": now - timedelta(seconds=5),
            "finished_at": now,
            "error": "sin red",
        }
    }
    service = _service(settings, runs)
    states = service.compute(workflow)
    assert states["extract_cases"].state is NodeState.ERROR
    assert "sin red" in (states["extract_cases"].detail or "")


def test_running_run_marks_running(settings, workflow):
    now = datetime.now(timezone.utc)
    runs = {
        "extract_cases": {
            "step": "extract_cases",
            "run_id": "r1",
            "status": "running",
            "rows_in": 0,
            "rows_out": 0,
            "started_at": now,
            "finished_at": None,
            "error": None,
        }
    }
    service = _service(settings, runs)
    states = service.compute(workflow)
    assert states["extract_cases"].state is NodeState.RUNNING


def test_reset_invalidates_node_and_descendants(settings, workflow):
    now = datetime.now(timezone.utc)
    runs = {
        "gold_gene_dim": {
            "step": "gold_gene_dim",
            "run_id": "r1",
            "status": "success",
            "rows_in": 1,
            "rows_out": 1,
            "started_at": now - timedelta(seconds=5),
            "finished_at": now,
            "error": None,
        }
    }
    service = _service(settings, runs)
    service.invalidate(workflow.id, ["gold_gene_dim"])
    states = service.compute(workflow)
    # Aunque el run sea correcto, el reset lo invalida.
    assert states["gold_gene_dim"].state is not NodeState.EXECUTED
    assert states["gold_marker_genes"].state is NodeState.IDLE


def test_outputs_present_marks_executed(settings, workflow):
    service = StateService(settings)
    service.latest_runs = lambda steps, project: {}
    service.outputs_present = lambda node: (["table"], True)
    states = service.compute(workflow)
    assert all(info.state is NodeState.EXECUTED for info in states.values())
