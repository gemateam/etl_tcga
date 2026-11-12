"""Carga, validación y persistencia de las definiciones de workflow."""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from .catalog import NODE_TYPES, get_node_type
from .schemas import EdgeDefinition, NodeDefinition, NodePatch, WorkflowDefinition
from .settings import FlowSettings


class WorkflowError(ValueError):
    """Definición de workflow inválida."""


_lock = threading.RLock()
_cache: dict[str, WorkflowDefinition] = {}
_cache_mtime: dict[str, float] = {}


def _path(settings: FlowSettings, workflow_id: str) -> Path:
    if not workflow_id or "/" in workflow_id or "\\" in workflow_id or ".." in workflow_id:
        raise WorkflowError(f"Identificador de workflow inválido: {workflow_id!r}")
    return settings.workflows_dir / f"{workflow_id}.json"


def validate_workflow(workflow: WorkflowDefinition) -> None:
    """Comprueba catálogo, capas, parámetros y ciclo del grafo."""
    ids = [node.id for node in workflow.nodes]
    if len(ids) != len(set(ids)):
        raise WorkflowError("Hay identificadores de nodo duplicados")

    seen_edges: set[tuple[str, str]] = set()
    for edge in workflow.edges:
        if edge.source not in ids:
            raise WorkflowError(f"La arista apunta a un nodo inexistente: {edge.source}")
        if edge.target not in ids:
            raise WorkflowError(f"La arista apunta a un nodo inexistente: {edge.target}")
        if edge.source == edge.target:
            raise WorkflowError(f"Arista consigo misma en {edge.source}")
        key = (edge.source, edge.target)
        if key in seen_edges:
            raise WorkflowError(f"Arista duplicada: {edge.source} → {edge.target}")
        seen_edges.add(key)

    for node in workflow.nodes:
        if node.type not in NODE_TYPES:
            raise WorkflowError(f"Tipo de nodo no registrado: {node.type}")
        node_type = get_node_type(node.type)
        if node.layer != node_type.layer:
            raise WorkflowError(
                f"{node.id} declara capa {node.layer} pero {node.type} es {node_type.layer}"
            )
        try:
            node_type.param_model.model_validate(node.params or {})
        except ValidationError as exc:
            raise WorkflowError(f"Parámetros inválidos en {node.id}: {exc}") from exc

    topological_order(workflow)


def topological_order(workflow: WorkflowDefinition) -> list[str]:
    """Orden topológico estable (respeta el orden del JSON entre nodos listos)."""
    order_index = {node.id: i for i, node in enumerate(workflow.nodes)}
    incoming: dict[str, int] = {node.id: 0 for node in workflow.nodes}
    children: dict[str, list[str]] = {node.id: [] for node in workflow.nodes}
    for edge in workflow.edges:
        incoming[edge.target] += 1
        children[edge.source].append(edge.target)

    ready = sorted((n for n, count in incoming.items() if count == 0), key=order_index.get)
    result: list[str] = []
    while ready:
        node_id = ready.pop(0)
        result.append(node_id)
        for child in sorted(children[node_id], key=order_index.get):
            incoming[child] -= 1
            if incoming[child] == 0:
                ready.append(child)
        ready.sort(key=order_index.get)

    if len(result) != len(workflow.nodes):
        raise WorkflowError("El grafo contiene un ciclo")
    return result


def parents(workflow: WorkflowDefinition) -> dict[str, list[str]]:
    result: dict[str, list[str]] = {node.id: [] for node in workflow.nodes}
    for edge in workflow.edges:
        result[edge.target].append(edge.source)
    return result


def ancestors(workflow: WorkflowDefinition, node_id: str) -> set[str]:
    by_parent = parents(workflow)
    found: set[str] = set()
    stack = list(by_parent.get(node_id, []))
    while stack:
        current = stack.pop()
        if current in found:
            continue
        found.add(current)
        stack.extend(by_parent.get(current, []))
    return found


def descendants(workflow: WorkflowDefinition, node_id: str) -> set[str]:
    children: dict[str, list[str]] = {node.id: [] for node in workflow.nodes}
    for edge in workflow.edges:
        children[edge.source].append(edge.target)
    found: set[str] = set()
    stack = list(children.get(node_id, []))
    while stack:
        current = stack.pop()
        if current in found:
            continue
        found.add(current)
        stack.extend(children.get(current, []))
    return found


def execution_plan(
    workflow: WorkflowDefinition,
    node_ids: list[str] | None = None,
    until: str | None = None,
    everything: bool = False,
) -> list[str]:
    """Nodos a ejecutar en orden, incluyendo ancestros pendientes si aplica."""
    order = topological_order(workflow)
    if everything:
        return order

    requested: set[str] = set(node_ids or [])
    if until:
        requested.add(until)
        requested |= ancestors(workflow, until)
    if not requested:
        raise WorkflowError("No se indicó ningún nodo")

    unknown = requested - set(order)
    if unknown:
        raise WorkflowError(f"Nodos desconocidos: {sorted(unknown)}")
    return [node_id for node_id in order if node_id in requested]


# ------------------------------------------------------------------ persistencia
def load_workflow(settings: FlowSettings, workflow_id: str) -> WorkflowDefinition:
    path = _path(settings, workflow_id)
    if not path.exists():
        raise FileNotFoundError(f"No existe el workflow {workflow_id}")
    mtime = path.stat().st_mtime
    with _lock:
        cached = _cache.get(workflow_id)
        if cached is not None and _cache_mtime.get(workflow_id) == mtime:
            return cached
        payload = json.loads(path.read_text(encoding="utf-8"))
        try:
            workflow = WorkflowDefinition.model_validate(payload)
        except ValidationError as exc:
            raise WorkflowError(f"Workflow inválido {workflow_id}: {exc}") from exc
        validate_workflow(workflow)
        _cache[workflow_id] = workflow
        _cache_mtime[workflow_id] = mtime
        return workflow


def list_workflows(settings: FlowSettings) -> list[dict[str, Any]]:
    directory = settings.workflows_dir
    if not directory.exists():
        return []
    items: list[dict[str, Any]] = []
    for path in sorted(directory.glob("*.json")):
        try:
            workflow = load_workflow(settings, path.stem)
        except (WorkflowError, ValidationError, json.JSONDecodeError):
            continue
        items.append(
            {
                "id": workflow.id,
                "name": workflow.name,
                "version": workflow.version,
                "description": workflow.description,
                "project_id": workflow.project_id,
                "node_count": len(workflow.nodes),
                "edge_count": len(workflow.edges),
            }
        )
    return items


def save_workflow(settings: FlowSettings, workflow: WorkflowDefinition) -> None:
    validate_workflow(workflow)
    path = _path(settings, workflow.id)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = workflow.model_dump(mode="json", exclude={"nodes": {"__all__": {"state", "state_detail"}}})
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    tmp.replace(path)
    with _lock:
        _cache.pop(workflow.id, None)
        _cache_mtime.pop(workflow.id, None)


def patch_node(
    settings: FlowSettings, workflow_id: str, node_id: str, patch: NodePatch
) -> WorkflowDefinition:
    with _lock:
        workflow = load_workflow(settings, workflow_id)
        updated = workflow.model_copy(deep=True)
        node = next((n for n in updated.nodes if n.id == node_id), None)
        if node is None:
            raise WorkflowError(f"No existe el nodo {node_id} en {workflow_id}")
        if patch.label is not None:
            node.label = patch.label
        if patch.notes is not None:
            node.notes = patch.notes
        if patch.status is not None:
            node.status = patch.status
        if patch.position is not None:
            node.position = patch.position
        if patch.params is not None:
            node.params = patch.params
        validate_workflow(updated)
        save_workflow(settings, updated)
        return load_workflow(settings, workflow_id)


def add_node(
    settings: FlowSettings,
    workflow_id: str,
    node: NodeDefinition,
    edges: list[EdgeDefinition] | None = None,
) -> WorkflowDefinition:
    with _lock:
        workflow = load_workflow(settings, workflow_id)
        updated = workflow.model_copy(deep=True)
        if any(n.id == node.id for n in updated.nodes):
            raise WorkflowError(f"Ya existe el nodo {node.id}")
        updated.nodes.append(node)
        for edge in edges or []:
            updated.edges.append(edge)
        validate_workflow(updated)
        save_workflow(settings, updated)
        return load_workflow(settings, workflow_id)


def delete_node(settings: FlowSettings, workflow_id: str, node_id: str) -> WorkflowDefinition:
    with _lock:
        workflow = load_workflow(settings, workflow_id)
        if not any(n.id == node_id for n in workflow.nodes):
            raise WorkflowError(f"No existe el nodo {node_id}")
        updated = workflow.model_copy(deep=True)
        updated.nodes = [n for n in updated.nodes if n.id != node_id]
        updated.edges = [
            e for e in updated.edges if e.source != node_id and e.target != node_id
        ]
        validate_workflow(updated)
        save_workflow(settings, updated)
        return load_workflow(settings, workflow_id)


def add_edge(
    settings: FlowSettings, workflow_id: str, edge: EdgeDefinition
) -> WorkflowDefinition:
    with _lock:
        workflow = load_workflow(settings, workflow_id)
        updated = workflow.model_copy(deep=True)
        if not any(
            item.source == edge.source and item.target == edge.target
            for item in updated.edges
        ):
            updated.edges.append(edge)
        validate_workflow(updated)
        save_workflow(settings, updated)
        return load_workflow(settings, workflow_id)


def delete_edge(
    settings: FlowSettings, workflow_id: str, source: str, target: str
) -> WorkflowDefinition:
    with _lock:
        workflow = load_workflow(settings, workflow_id)
        updated = workflow.model_copy(deep=True)
        updated.edges = [
            item
            for item in updated.edges
            if not (item.source == source and item.target == target)
        ]
        validate_workflow(updated)
        save_workflow(settings, updated)
        return load_workflow(settings, workflow_id)
