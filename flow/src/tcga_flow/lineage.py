"""Linaje estático: de una salida gold hasta sus fuentes en landing/bronze."""

from __future__ import annotations

from typing import Any

from .catalog import get_node_type
from .schemas import WorkflowDefinition
from .workflows import topological_order


def lineage(workflow: WorkflowDefinition, node_id: str) -> dict[str, Any]:
    """Devuelve la cadena de ancestros y sus salidas físicas.

    Se calcula sobre las aristas del workflow: es un linaje de definición, no
    de ejecución. Para el detalle por fila se usarían `run_id` y los metadatos
    de origen de cada capa.
    """
    ids = {node.id for node in workflow.nodes}
    if node_id not in ids:
        raise KeyError(node_id)

    parents: dict[str, list[str]] = {node.id: [] for node in workflow.nodes}
    for edge in workflow.edges:
        parents[edge.target].append(edge.source)

    needed: set[str] = set()
    stack = [node_id]
    while stack:
        current = stack.pop()
        if current in needed:
            continue
        needed.add(current)
        stack.extend(parents.get(current, []))

    nodes: dict[str, dict[str, Any]] = {}
    for current in topological_order(workflow):
        if current not in needed:
            continue
        node = next(n for n in workflow.nodes if n.id == current)
        node_type = get_node_type(node.type)
        nodes[current] = {
            "id": node.id,
            "label": node.label,
            "type": node.type,
            "layer": node.layer.value,
            "status": node.status,
            "is_target": current == node_id,
            "parents": parents.get(current, []),
            "outputs": [
                {
                    "key": spec.key,
                    "label": spec.label,
                    "kind": spec.kind.value,
                    "target": spec.target,
                }
                for spec in node_type.output_specs
            ],
        }

    return {
        "workflow_id": workflow.id,
        "target": node_id,
        "nodes": list(nodes.values()),
        "edges": [
            {"source": edge.source, "target": edge.target}
            for edge in workflow.edges
            if edge.source in needed and edge.target in needed
        ],
    }


def lineage_to_source(workflow: WorkflowDefinition, node_id: str) -> list[dict[str, Any]]:
    """Lista ordenada de capas con sus salidas, de origen a destino."""
    payload = lineage(workflow, node_id)
    return [
        {
            "layer": item["layer"],
            "id": item["id"],
            "label": item["label"],
            "outputs": item["outputs"],
        }
        for item in payload["nodes"]
    ]
