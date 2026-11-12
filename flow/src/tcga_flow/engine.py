"""Motor de ejecución: una ejecución global, subproceso por nodo y SSE."""

from __future__ import annotations

import json
import os
import queue
import subprocess
import sys
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterator

from .catalog import get_node_type
from .runner import RESULT_MARKER
from .schemas import LogLine, WorkflowDefinition
from .settings import FlowSettings
from .state import StateService, iso, run_duration
from .workflows import descendants, execution_plan


class EngineError(RuntimeError):
    pass


class BusyError(EngineError):
    """Ya hay una ejecución en curso (lock global)."""


@dataclass
class Execution:
    workflow_id: str
    execution: str = "idle"  # idle | running | cancelling
    current_node: str | None = None
    queue: list[str] = field(default_factory=list)
    done: list[str] = field(default_factory=list)
    cancel_requested: bool = False
    started_at: float | None = None
    finished_at: float | None = None
    process: subprocess.Popen[str] | None = None
    last_error: str | None = None


class Engine:
    """Un único worker global para proteger DuckDB y `gold.cx_pivot`."""

    def __init__(self, settings: FlowSettings, state: StateService) -> None:
        self.settings = settings
        self.state = state
        self._lock = threading.Lock()
        self._mutex = threading.RLock()
        self._execution: Execution | None = None
        self._thread: threading.Thread | None = None
        self._subscribers: list[queue.Queue[dict[str, Any]]] = []
        self._seq = 0
        self._logs: dict[str | None, deque[LogLine]] = {}

    # --------------------------------------------------------------------- SSE
    def subscribe(self) -> queue.Queue[dict[str, Any]]:
        channel: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=500)
        with self._mutex:
            self._subscribers.append(channel)
        return channel

    def unsubscribe(self, channel: queue.Queue[dict[str, Any]]) -> None:
        with self._mutex:
            if channel in self._subscribers:
                self._subscribers.remove(channel)

    def _publish(self, kind: str, payload: dict[str, Any]) -> None:
        event = {"type": kind, "payload": payload}
        with self._mutex:
            subscribers = list(self._subscribers)
        for channel in subscribers:
            try:
                channel.put_nowait(event)
            except queue.Full:
                try:
                    channel.get_nowait()
                    channel.put_nowait(event)
                except queue.Empty:  # pragma: no cover - carrera improbable
                    pass

    def _log(self, node_id: str | None, level: str, message: str) -> None:
        with self._mutex:
            self._seq += 1
            line = LogLine(
                seq=self._seq,
                ts=datetime.now(timezone.utc).isoformat(timespec="seconds"),
                node_id=node_id,
                level=level.upper(),
                message=message[:4000],
            )
            self._logs.setdefault(node_id, deque(maxlen=3000)).append(line)
            self._logs.setdefault(None, deque(maxlen=5000)).append(line)
        self._publish("log", line.model_dump())

    def logs(self, node_id: str | None = None, after: int = 0) -> list[dict[str, Any]]:
        with self._mutex:
            lines = list(self._logs.get(node_id, ()))
        return [line.model_dump() for line in lines if line.seq > after]

    # ------------------------------------------------------------------ estado
    def execution_payload(self) -> dict[str, Any]:
        with self._mutex:
            execution = self._execution
            if execution is None:
                return {
                    "workflow_id": None,
                    "execution": "idle",
                    "current_node": None,
                    "queue": [],
                    "done": [],
                }
            return {
                "workflow_id": execution.workflow_id,
                "execution": execution.execution,
                "current_node": execution.current_node,
                "queue": list(execution.queue),
                "done": list(execution.done),
                "last_error": execution.last_error,
            }

    def running_nodes(self) -> set[str]:
        with self._mutex:
            execution = self._execution
            if execution and execution.execution in {"running", "cancelling"}:
                return {execution.current_node} if execution.current_node else set()
            return set()

    def runtime(self, workflow: WorkflowDefinition) -> dict[str, Any]:
        infos = self.state.compute(workflow, self.running_nodes())
        nodes = []
        for node in workflow.nodes:
            info = infos[node.id]
            run = info.run or {}
            nodes.append(
                {
                    "id": node.id,
                    "state": info.state.value,
                    "detail": info.detail,
                    "run_id": str(run["run_id"]) if run.get("run_id") else None,
                    "rows_in": int(run.get("rows_in") or 0),
                    "rows_out": int(run.get("rows_out") or 0),
                    "started_at": iso(run.get("started_at")),
                    "finished_at": iso(run.get("finished_at")),
                    "duration_seconds": run_duration(run),
                    "error": run.get("error"),
                }
            )
        payload = self.execution_payload()
        return {
            "workflow_id": workflow.id,
            "execution": payload["execution"],
            "current_node": payload["current_node"],
            "queue": payload["queue"],
            "nodes": nodes,
        }

    # --------------------------------------------------------------- acciones
    def start(
        self,
        workflow: WorkflowDefinition,
        plan: list[str],
        project_id: str | None = None,
    ) -> dict[str, Any]:
        if not plan:
            raise EngineError("El plan de ejecución está vacío")
        if not self._lock.acquire(blocking=False):
            raise BusyError("Ya hay una ejecución en curso")

        with self._mutex:
            self._logs.clear()
            self._seq = 0
            execution = Execution(
                workflow_id=workflow.id,
                execution="running",
                queue=list(plan),
                started_at=time.time(),
            )
            self._execution = execution

        self._log(
            None,
            "INFO",
            f"Plan de ejecución: {' → '.join(plan)} (proyecto {project_id or workflow.project_id})",
        )
        self._publish("execution", self.execution_payload())
        thread = threading.Thread(
            target=self._run_plan,
            args=(workflow, plan, project_id or workflow.project_id),
            daemon=True,
            name=f"flow-{workflow.id}",
        )
        self._thread = thread
        thread.start()
        return self.execution_payload()

    def _run_plan(
        self, workflow: WorkflowDefinition, plan: list[str], project_id: str
    ) -> None:
        try:
            for node_id in plan:
                with self._mutex:
                    execution = self._execution
                    if execution is None:
                        return
                    if execution.cancel_requested:
                        break
                    execution.current_node = node_id

                node = next(n for n in workflow.nodes if n.id == node_id)
                self._log(
                    node_id,
                    "INFO",
                    f"Inicio del nodo {node.label} ({node.type})",
                )
                self._publish("state", {"workflow_id": workflow.id, "node": node_id})
                code, result, error = self._run_node(node_id, node)

                with self._mutex:
                    execution = self._execution
                    if execution is None:
                        return
                    cancelled = execution.cancel_requested
                    execution.current_node = None

                if cancelled:
                    self._log(node_id, "WARNING", "Ejecución cancelada por el usuario")
                    break

                if code != 0 or not (result or {}).get("ok"):
                    detail = error or (result or {}).get("error") or f"código {code}"
                    with self._mutex:
                        if self._execution:
                            self._execution.last_error = detail
                    self._log(node_id, "ERROR", f"Nodo fallido: {detail}")
                    self._publish("state", {"workflow_id": workflow.id, "node": node_id})
                    break

                self.state.clear_invalidation(workflow.id, node_id)
                with self._mutex:
                    if self._execution:
                        self._execution.done.append(node_id)
                        if self._execution.queue and self._execution.queue[0] == node_id:
                            self._execution.queue.pop(0)
                rows = (result or {}).get("rows_out", 0)
                self._log(node_id, "INFO", f"Nodo completado · {rows} filas")
                self._publish("state", {"workflow_id": workflow.id, "node": node_id})
        finally:
            with self._mutex:
                execution = self._execution
                if execution is not None:
                    execution.execution = "idle"
                    execution.current_node = None
                    execution.queue = []
                    execution.process = None
                    execution.finished_at = time.time()
            self._publish("execution", self.execution_payload())
            self._lock.release()

    def _run_node(
        self, node_id: str, node
    ) -> tuple[int, dict[str, Any] | None, str | None]:
        node_type = get_node_type(node.type)
        command = [
            sys.executable,
            "-m",
            "tcga_flow.runner",
            node_type.key,
            json.dumps(node.params or {}, ensure_ascii=False),
        ]
        env = os.environ.copy()
        env.setdefault("PYTHONUNBUFFERED", "1")
        env["FLOW_WORKFLOWS_DIR"] = str(self.settings.workflows_dir)
        env["GEMAMX_DIR"] = str(self.settings.gemamx_dir)
        result: dict[str, Any] | None = None
        error: str | None = None

        try:
            process = subprocess.Popen(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                env=env,
            )
        except OSError as exc:
            return 127, None, f"No se pudo iniciar el subproceso: {exc}"

        with self._mutex:
            if self._execution:
                self._execution.process = process

        assert process.stdout is not None
        try:
            for raw in process.stdout:
                line = raw.rstrip("\r\n")
                if not line:
                    continue
                if line.startswith(RESULT_MARKER):
                    try:
                        result = json.loads(line[len(RESULT_MARKER) :])
                        if not result.get("ok"):
                            error = result.get("error")
                    except json.JSONDecodeError:
                        self._log(node_id, "WARNING", line)
                    continue
                level, message = _parse_log_line(line)
                self._log(node_id, level, message)
        finally:
            code = process.wait()
            with self._mutex:
                if self._execution:
                    self._execution.process = None
        if code != 0 and not error:
            error = f"el subproceso terminó con código {code}"
        return code, result, error

    def cancel(self) -> dict[str, Any]:
        with self._mutex:
            execution = self._execution
            if execution is None or execution.execution not in {"running", "cancelling"}:
                raise EngineError("No hay ninguna ejecución en curso")
            execution.execution = "cancelling"
            execution.cancel_requested = True
            process = execution.process
        if process is not None and process.poll() is None:
            self._log(None, "WARNING", "Cancelando el subproceso en curso")
            try:
                process.terminate()
            except OSError:  # pragma: no cover
                pass
        payload = self.execution_payload()
        self._publish("execution", payload)
        return payload

    def reset(self, workflow: WorkflowDefinition, node_ids: list[str]) -> list[str]:
        if not node_ids:
            node_ids = [node.id for node in workflow.nodes]
        invalid: set[str] = set()
        valid_ids = {node.id for node in workflow.nodes}
        for node_id in node_ids:
            if node_id not in valid_ids:
                raise EngineError(f"Nodo desconocido: {node_id}")
            invalid.add(node_id)
            invalid |= descendants(workflow, node_id)
        self.state.invalidate(workflow.id, sorted(invalid))
        self._log(None, "INFO", f"Reset de nodos: {', '.join(sorted(invalid))}")
        self._publish("state", {"workflow_id": workflow.id, "node": None})
        return sorted(invalid)

    def plan(
        self,
        workflow: WorkflowDefinition,
        node_ids: list[str] | None = None,
        until: str | None = None,
        everything: bool = False,
    ) -> list[str]:
        return execution_plan(workflow, node_ids=node_ids, until=until, everything=everything)

    def wait(self, timeout: float | None = None) -> None:
        thread = self._thread
        if thread is not None:
            thread.join(timeout)


def _parse_log_line(line: str) -> tuple[str, str]:
    stripped = line.strip()
    if stripped.startswith("{"):
        try:
            payload = json.loads(stripped)
        except json.JSONDecodeError:
            return "INFO", line
        level = str(payload.get("level") or "INFO").upper()
        message = payload.get("message") or payload.get("event") or stripped
        detail = payload.get("detail")
        if detail:
            message = f"{message} · {detail}"
        return level, str(message)
    return "INFO", line


def iter_sse(engine: Engine, heartbeat: float = 15.0) -> Iterator[str]:
    """Generador SSE con heartbeat para evitar proxys que cortan la conexión."""
    channel = engine.subscribe()
    try:
        yield _sse({"type": "hello", "payload": engine.execution_payload()})
        while True:
            try:
                event = channel.get(timeout=heartbeat)
            except queue.Empty:
                yield ": ping\n\n"
                continue
            yield _sse(event)
    finally:
        engine.unsubscribe(channel)


def _sse(event: dict[str, Any]) -> str:
    return f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
