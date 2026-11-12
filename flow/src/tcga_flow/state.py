"""Cálculo del estado de los nodos a partir de `ops.pipeline_runs` y las salidas."""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .schemas import NodeDefinition, NodeState, OutputKind, WorkflowDefinition
from .settings import FlowSettings
from .workflows import topological_order


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class NodeRuntimeInfo:
    state: NodeState
    detail: str
    run: dict[str, Any] | None = None
    outputs: list[str] = field(default_factory=list)


class StateService:
    """Estado combinado de ejecuciones y ficheros, con invalidación en memoria.

    El Reset no borra datos: solo invalida el nodo y sus descendientes en esta
    sesión. Al reiniciar el servicio, el estado se recalcula desde
    `ops.pipeline_runs` y la existencia de las salidas.
    """

    def __init__(self, settings: FlowSettings) -> None:
        self.settings = settings
        self._lock = threading.RLock()
        self._invalidated: dict[str, dict[str, float]] = {}
        self._table_cache: dict[str, tuple[float, bool]] = {}

    # ------------------------------------------------------------- invalidación
    def invalidate(self, workflow_id: str, node_ids: list[str]) -> None:
        now = time.time()
        with self._lock:
            bucket = self._invalidated.setdefault(workflow_id, {})
            for node_id in node_ids:
                bucket[node_id] = now

    def is_invalidated(self, workflow_id: str, node_id: str) -> bool:
        with self._lock:
            return node_id in self._invalidated.get(workflow_id, {})

    def clear_invalidation(self, workflow_id: str, node_id: str) -> None:
        with self._lock:
            self._invalidated.get(workflow_id, {}).pop(node_id, None)

    def invalidation_epoch(self, workflow_id: str, node_id: str) -> float | None:
        with self._lock:
            return self._invalidated.get(workflow_id, {}).get(node_id)

    # --------------------------------------------------------------- ejecuciones
    def _connect(self):
        from tcga.db import connect

        return connect()

    def latest_runs(self, steps: list[str], project_id: str | None) -> dict[str, dict[str, Any]]:
        if not steps:
            return {}
        try:
            with self._connect() as conn:
                if project_id:
                    rows = conn.execute(
                        """
                        SELECT DISTINCT ON (step)
                               step, run_id, project_id, status, rows_in, rows_out,
                               started_at, finished_at, error, params
                        FROM ops.pipeline_runs
                        WHERE step = ANY(%s)
                          AND (project_id = %s OR project_id IS NULL)
                        ORDER BY step, started_at DESC
                        """,
                        (steps, project_id),
                    ).fetchall()
                else:
                    rows = conn.execute(
                        """
                        SELECT DISTINCT ON (step)
                               step, run_id, project_id, status, rows_in, rows_out,
                               started_at, finished_at, error, params
                        FROM ops.pipeline_runs
                        WHERE step = ANY(%s)
                        ORDER BY step, started_at DESC
                        """,
                        (steps,),
                    ).fetchall()
        except Exception:  # noqa: BLE001 - el lienzo debe abrir aunque falle la BD
            return {}
        return {row["step"]: dict(row) for row in rows}

    def latest_run_ids(self, steps: list[str], project_id: str | None) -> dict[str, str]:
        return {
            step: run["run_id"]
            for step, run in self.latest_runs(steps, project_id).items()
            if run.get("run_id")
        }

    # ------------------------------------------------------------------- salidas
    def _table_exists(self, name: str) -> bool:
        now = time.time()
        cached = self._table_cache.get(name)
        if cached and now - cached[0] < 5:
            return cached[1]
        try:
            with self._connect() as conn:
                row = conn.execute("SELECT to_regclass(%s) AS reg", (name,)).fetchone()
                exists = bool(row and row.get("reg"))
        except Exception:  # noqa: BLE001
            exists = False
        self._table_cache[name] = (now, exists)
        return exists

    def _file_exists(self, target: str) -> bool:
        base = Path(self.settings.data_dir())
        if any(char in target for char in "*?["):
            return any(True for _ in base.glob(target))
        candidate = Path(target)
        return candidate.exists() if candidate.is_absolute() else (base / target).exists()

    def outputs_present(self, node: NodeDefinition) -> tuple[list[str], bool]:
        """Devuelve las salidas encontradas y si todas están presentes."""
        from .catalog import get_node_type

        node_type = get_node_type(node.type)
        found: list[str] = []
        all_present = True
        for spec in node_type.output_specs:
            if spec.kind is OutputKind.TABLE:
                present = self._table_exists(spec.target)
            else:
                present = self._file_exists(spec.target)
            if present:
                found.append(spec.key)
            else:
                all_present = False
        return found, all_present

    # -------------------------------------------------------------------- estado
    def compute(
        self, workflow: WorkflowDefinition, live_running: set[str] | None = None
    ) -> dict[str, NodeRuntimeInfo]:
        steps = sorted({node.type for node in workflow.nodes})
        runs = self.latest_runs(steps, workflow.project_id)
        live_running = live_running or set()
        parents: dict[str, list[str]] = {node.id: [] for node in workflow.nodes}
        for edge in workflow.edges:
            parents[edge.target].append(edge.source)

        result: dict[str, NodeRuntimeInfo] = {}
        for node_id in topological_order(workflow):
            node = next(n for n in workflow.nodes if n.id == node_id)
            run = runs.get(node.type)
            invalidated_at = self.invalidation_epoch(workflow.id, node_id)

            if node_id in live_running:
                result[node_id] = NodeRuntimeInfo(
                    state=NodeState.RUNNING,
                    detail="Ejecutándose",
                    run=run,
                )
                continue

            if run:
                finished = run.get("finished_at")
                finished_epoch = finished.timestamp() if isinstance(finished, datetime) else None
                stale = (
                    invalidated_at is not None
                    and finished_epoch is not None
                    and finished_epoch <= invalidated_at
                )
                if run.get("status") == "running" and not stale:
                    result[node_id] = NodeRuntimeInfo(
                        state=NodeState.RUNNING,
                        detail="Ejecución registrada en curso",
                        run=run,
                    )
                    continue
                if run.get("status") == "failed" and not stale:
                    result[node_id] = NodeRuntimeInfo(
                        state=NodeState.ERROR,
                        detail=(run.get("error") or "Fallo sin detalle")[:500],
                        run=run,
                    )
                    continue
                if run.get("status") == "success" and not stale:
                    result[node_id] = NodeRuntimeInfo(
                        state=NodeState.EXECUTED,
                        detail="Última ejecución correcta",
                        run=run,
                    )
                    continue

            if invalidated_at is None:
                outputs, all_present = self.outputs_present(node)
                if all_present:
                    result[node_id] = NodeRuntimeInfo(
                        state=NodeState.EXECUTED,
                        detail="Salida presente en disco/base de datos",
                        outputs=outputs,
                    )
                    continue

            deps = parents.get(node_id, [])
            deps_ok = all(
                result[dep].state is NodeState.EXECUTED for dep in deps if dep in result
            )
            if deps_ok:
                detail = (
                    "Listo para ejecutar"
                    if not deps
                    else "Dependencias ejecutadas; pendiente de ejecutar"
                )
                result[node_id] = NodeRuntimeInfo(state=NodeState.CONFIGURED, detail=detail)
            else:
                pending = [
                    dep
                    for dep in deps
                    if dep in result and result[dep].state is not NodeState.EXECUTED
                ]
                result[node_id] = NodeRuntimeInfo(
                    state=NodeState.IDLE,
                    detail=(
                        "Sin ejecutar" if not deps else f"Esperando: {', '.join(pending)}"
                    ),
                )
        return result


def run_duration(run: dict[str, Any] | None) -> float | None:
    if not run:
        return None
    started = run.get("started_at")
    finished = run.get("finished_at")
    if isinstance(started, datetime) and isinstance(finished, datetime):
        return round((finished - started).total_seconds(), 2)
    return None


def iso(value: Any) -> str | None:
    if isinstance(value, datetime):
        return value.isoformat()
    return None
