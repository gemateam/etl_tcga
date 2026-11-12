"""Aplicación FastAPI del ejecutor visual: API + frontend estático."""

from __future__ import annotations

import logging
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .api import build_router
from .engine import Engine
from .preview import PreviewService
from .settings import FlowSettings, get_flow_settings
from .state import StateService

log = logging.getLogger("tcga_flow")


def create_app(settings: FlowSettings | None = None) -> FastAPI:
    settings = settings or get_flow_settings()
    app = FastAPI(
        title="TCGA Medallion Flow",
        version="1.0.0",
        description="Ejecutor visual tipo KNIME para los ETL medallón (TCGA y GEMAMX).",
    )
    app.state.flow_settings = settings
    app.state.state_service = StateService(settings)
    app.state.engine = Engine(settings, app.state.state_service)
    app.state.preview_service = PreviewService(settings)

    app.include_router(build_router())

    @app.get("/health")
    def health() -> JSONResponse:
        return JSONResponse(
            {
                "status": "ok",
                "workflows_dir": str(settings.workflows_dir),
                "data_dir": str(settings.data_dir()),
            }
        )

    dist = settings.frontend_dist
    assets = dist / "assets"
    if assets.exists():
        app.mount("/assets", StaticFiles(directory=assets), name="assets")

    @app.get("/", response_class=HTMLResponse)
    def index() -> HTMLResponse:
        index_file = dist / "index.html"
        if index_file.exists():
            return FileResponse(index_file)
        return HTMLResponse(
            "<h1>TCGA Medallion Flow</h1>"
            "<p>El frontend no está compilado. Ejecuta <code>npm install && npm run build</code> "
            "en <code>flow/frontend</code> o levanta el servicio con Docker Compose.</p>"
            "<p>API: <a href='/docs'>/docs</a></p>"
        )

    @app.get("/favicon.svg")
    def favicon() -> FileResponse:
        icon = dist / "favicon.svg"
        if icon.exists():
            return FileResponse(icon)
        return FileResponse(Path(__file__).resolve().parent / "favicon.svg")

    return app


app = create_app()
