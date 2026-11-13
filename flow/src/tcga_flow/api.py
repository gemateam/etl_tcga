"""API HTTP del ejecutor visual."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, PlainTextResponse, StreamingResponse
from pydantic import ValidationError

from .catalog import catalog_payload, get_node_source
from .engine import BusyError, Engine, EngineError, iter_sse
from .lineage import lineage
from .preview import PreviewError, PreviewService
from .schemas import (
    CreateNodeRequest,
    EdgeDefinition,
    ExecuteRequest,
    NodePatch,
    NodeState,
    WorkflowDefinition,
)
from .settings import FlowSettings
from .state import StateService
from .workflows import (
    WorkflowError,
    add_edge,
    add_node,
    ancestors,
    delete_edge,
    delete_node,
    list_workflows,
    load_workflow,
    patch_node,
    topological_order,
)

LAYER_COLORS = {
    "landing": "#0f766e",
    "bronze": "#d97706",
    "silver": "#2563eb",
    "gold": "#7c3aed",
}

STATE_COLORS = {
    "idle": "#dc2626",
    "configured": "#eab308",
    "running": "#2563eb",
    "executed": "#15803d",
    "error": "#b91c1c",
}


def _services(request: Request) -> tuple[FlowSettings, StateService, Engine, PreviewService]:
    app = request.app
    return (
        app.state.flow_settings,
        app.state.state_service,
        app.state.engine,
        app.state.preview_service,
    )


def _workflow(request: Request, workflow_id: str) -> WorkflowDefinition:
    settings, _, _, _ = _services(request)
    try:
        return load_workflow(settings, workflow_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except WorkflowError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


def _handle_error(exc: Exception) -> HTTPException:
    if isinstance(exc, BusyError):
        return HTTPException(status_code=409, detail=str(exc))
    if isinstance(exc, WorkflowError):
        return HTTPException(status_code=422, detail=str(exc))
    if isinstance(exc, EngineError):
        return HTTPException(status_code=409, detail=str(exc))
    if isinstance(exc, ValidationError):
        return HTTPException(status_code=422, detail=exc.errors())
    return HTTPException(status_code=400, detail=str(exc))


def _plan_for(request: Request, workflow: WorkflowDefinition, body: ExecuteRequest) -> list[str]:
    _, state, _, _ = _services(request)
    order = topological_order(workflow)
    runtime = state.compute(workflow)
    executed = {
        node_id for node_id, info in runtime.items() if info.state is NodeState.EXECUTED
    }

    if body.all:
        return order
    if body.until:
        if body.until not in order:
            raise HTTPException(status_code=404, detail=f"Nodo desconocido: {body.until}")
        wanted = ancestors(workflow, body.until) | {body.until}
        return [node_id for node_id in order if node_id in wanted and node_id not in executed]
    if body.nodes:
        unknown = [node_id for node_id in body.nodes if node_id not in order]
        if unknown:
            raise HTTPException(status_code=404, detail=f"Nodos desconocidos: {unknown}")
        if len(body.nodes) > 1:
            missing: list[str] = []
            for node_id in body.nodes:
                missing.extend(
                    sorted(ancestors(workflow, node_id) - executed - set(body.nodes))
                )
            if missing:
                raise HTTPException(
                    status_code=409,
                    detail=(
                        "Faltan ancestros ejecutados: " + ", ".join(sorted(set(missing)))
                    ),
                )
        else:
            node_id = body.nodes[0]
            missing = sorted(ancestors(workflow, node_id) - executed)
            if missing:
                raise HTTPException(
                    status_code=409,
                    detail="Faltan ancestros ejecutados: " + ", ".join(missing),
                )
        return [node_id for node_id in order if node_id in set(body.nodes)]
    raise HTTPException(
        status_code=422, detail="Indica 'nodes', 'until' o 'all' en la petición"
    )


def build_router() -> APIRouter:
    router = APIRouter(prefix="/api/flow")

    @router.get("/config")
    def config(request: Request) -> JSONResponse:
        settings, _, _, _ = _services(request)
        return JSONResponse(
            {
                "layers": [
                    {"key": key, "color": color, "label": key.capitalize()}
                    for key, color in LAYER_COLORS.items()
                ],
                "states": STATE_COLORS,
                "workflows": list_workflows(settings),
                "data_dir": str(settings.data_dir()),
                "gemamx_dir": str(settings.gemamx_dir),
            }
        )

    @router.get("/catalog")
    def catalog() -> JSONResponse:
        return JSONResponse({"nodes": catalog_payload()})

    @router.get("/catalog/{node_type_id}/source")
    def node_source(node_type_id: str) -> JSONResponse:
        """Devuelve el código fuente Python de la implementación de un tipo de nodo."""
        try:
            return JSONResponse(get_node_source(node_type_id))
        except KeyError:
            raise HTTPException(status_code=404, detail=f"Tipo de nodo desconocido: {node_type_id}")

    @router.get("/workflows")
    def workflows(request: Request) -> JSONResponse:
        settings, _, _, _ = _services(request)
        return JSONResponse({"workflows": list_workflows(settings)})

    @router.get("/workflows/{workflow_id}")
    def workflow_detail(request: Request, workflow_id: str) -> JSONResponse:
        workflow = _workflow(request, workflow_id)
        return JSONResponse(workflow.model_dump(mode="json"))

    @router.patch("/workflows/{workflow_id}/nodes/{node_id}")
    def update_node(
        request: Request, workflow_id: str, node_id: str, patch: NodePatch
    ) -> JSONResponse:
        settings, _, _, _ = _services(request)
        try:
            workflow = patch_node(settings, workflow_id, node_id, patch)
        except (WorkflowError, ValidationError) as exc:
            raise _handle_error(exc) from exc
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return JSONResponse(workflow.model_dump(mode="json"))

    @router.post("/workflows/{workflow_id}/nodes")
    def create_node(
        request: Request, workflow_id: str, body: CreateNodeRequest
    ) -> JSONResponse:
        settings, _, _, _ = _services(request)
        try:
            workflow = add_node(settings, workflow_id, body.node, body.edges)
        except (WorkflowError, ValidationError) as exc:
            raise _handle_error(exc) from exc
        return JSONResponse(workflow.model_dump(mode="json"))

    @router.delete("/workflows/{workflow_id}/nodes/{node_id}")
    def remove_node(request: Request, workflow_id: str, node_id: str) -> JSONResponse:
        settings, _, _, _ = _services(request)
        try:
            workflow = delete_node(settings, workflow_id, node_id)
        except (WorkflowError, ValidationError) as exc:
            raise _handle_error(exc) from exc
        return JSONResponse(workflow.model_dump(mode="json"))

    @router.post("/workflows/{workflow_id}/edges")
    def create_edge(
        request: Request, workflow_id: str, edge: EdgeDefinition
    ) -> JSONResponse:
        settings, _, _, _ = _services(request)
        try:
            workflow = add_edge(settings, workflow_id, edge)
        except (WorkflowError, ValidationError) as exc:
            raise _handle_error(exc) from exc
        return JSONResponse(workflow.model_dump(mode="json"))

    @router.delete("/workflows/{workflow_id}/edges")
    def remove_edge(
        request: Request, workflow_id: str, source: str, target: str
    ) -> JSONResponse:
        settings, _, _, _ = _services(request)
        try:
            workflow = delete_edge(settings, workflow_id, source, target)
        except (WorkflowError, ValidationError) as exc:
            raise _handle_error(exc) from exc
        return JSONResponse(workflow.model_dump(mode="json"))

    @router.get("/workflows/{workflow_id}/state")
    def workflow_state(request: Request, workflow_id: str) -> JSONResponse:
        _, _, engine, _ = _services(request)
        workflow = _workflow(request, workflow_id)
        return JSONResponse(engine.runtime(workflow))

    @router.post("/workflows/{workflow_id}/execute")
    def execute(request: Request, workflow_id: str, body: ExecuteRequest) -> JSONResponse:
        _, _, engine, _ = _services(request)
        workflow = _workflow(request, workflow_id)
        plan = _plan_for(request, workflow, body)
        if not plan:
            return JSONResponse({"execution": "idle", "plan": [], "detail": "Nada pendiente"})
        try:
            payload = engine.start(workflow, plan, workflow.project_id)
        except (BusyError, EngineError) as exc:
            raise _handle_error(exc) from exc
        return JSONResponse({**payload, "plan": plan})

    @router.post("/workflows/{workflow_id}/cancel")
    def cancel(request: Request, workflow_id: str) -> JSONResponse:
        _, _, engine, _ = _services(request)
        _workflow(request, workflow_id)
        try:
            payload = engine.cancel()
        except EngineError as exc:
            raise _handle_error(exc) from exc
        return JSONResponse(payload)

    @router.post("/workflows/{workflow_id}/reset")
    def reset(request: Request, workflow_id: str, node_ids: list[str] | None = None) -> JSONResponse:
        _, _, engine, _ = _services(request)
        workflow = _workflow(request, workflow_id)
        try:
            invalidated = engine.reset(workflow, node_ids or [])
        except EngineError as exc:
            raise _handle_error(exc) from exc
        return JSONResponse({"invalidated": invalidated})

    @router.get("/workflows/{workflow_id}/nodes/{node_id}/output")
    def node_output(
        request: Request,
        workflow_id: str,
        node_id: str,
        output: str | None = None,
        page: int = 1,
        page_size: int = 100,
    ) -> JSONResponse:
        _, _, _, preview = _services(request)
        workflow = _workflow(request, workflow_id)
        node = next((n for n in workflow.nodes if n.id == node_id), None)
        if node is None:
            raise HTTPException(status_code=404, detail=f"Nodo desconocido: {node_id}")
        try:
            payload = preview.preview(node, output, page=page, page_size=page_size)
        except PreviewError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return JSONResponse(payload)

    @router.get("/workflows/{workflow_id}/nodes/{node_id}/log")
    def node_log(
        request: Request, workflow_id: str, node_id: str, after: int = 0
    ) -> JSONResponse:
        _, _, engine, _ = _services(request)
        _workflow(request, workflow_id)
        return JSONResponse(
            {"node_id": node_id, "lines": engine.logs(node_id, after=after)}
        )

    @router.get("/logs")
    def global_log(request: Request, after: int = 0) -> JSONResponse:
        _, _, engine, _ = _services(request)
        return JSONResponse({"lines": engine.logs(None, after=after)})

    @router.get("/workflows/{workflow_id}/lineage/{node_id}")
    def node_lineage(request: Request, workflow_id: str, node_id: str) -> JSONResponse:
        workflow = _workflow(request, workflow_id)
        try:
            payload = lineage(workflow, node_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=f"Nodo desconocido: {node_id}") from exc
        return JSONResponse(payload)

    @router.get("/workflows/{workflow_id}/export.json")
    def export_json(request: Request, workflow_id: str) -> JSONResponse:
        workflow = _workflow(request, workflow_id)
        return JSONResponse(
            workflow.model_dump(mode="json"),
            headers={
                "Content-Disposition": f'attachment; filename="{workflow_id}.json"'
            },
        )

    @router.get("/workflows/{workflow_id}/export.mermaid")
    def export_mermaid(request: Request, workflow_id: str) -> PlainTextResponse:
        workflow = _workflow(request, workflow_id)
        return PlainTextResponse(
            to_mermaid(workflow),
            headers={
                "Content-Disposition": f'attachment; filename="{workflow_id}.mmd"'
            },
        )

    @router.get("/events")
    def events(request: Request) -> StreamingResponse:
        _, _, engine, _ = _services(request)
        return StreamingResponse(
            iter_sse(engine),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    return router


def to_mermaid(workflow: WorkflowDefinition) -> str:
    lines = ["flowchart LR"]
    for layer in ("landing", "bronze", "silver", "gold"):
        ids = [node.id for node in workflow.nodes if node.layer.value == layer]
        if not ids:
            continue
        lines.append(f"  subgraph {layer}")
        for node_id in ids:
            node = next(n for n in workflow.nodes if n.id == node_id)
            label = node.label.replace('"', "'")
            lines.append(f'    {node_id}["{label}"]')
        lines.append("  end")
    for edge in workflow.edges:
        lines.append(f"  {edge.source} --> {edge.target}")
    return "\n".join(lines) + "\n"
