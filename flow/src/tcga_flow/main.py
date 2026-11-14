"""Aplicación FastAPI del ejecutor visual: API + frontend estático."""

from __future__ import annotations

import base64
import logging
import os
import secrets
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles

from .api import build_router
from .engine import Engine
from .preview import PreviewService
from .settings import FlowSettings, get_flow_settings
from .state import StateService

log = logging.getLogger("tcga_flow")


def _basic_credentials() -> tuple[str, str] | None:
    """Credenciales opcionales para exponer el lienzo en Internet.

    Si `FLOW_AUTH_USER` y `FLOW_AUTH_PASSWORD` están definidas, todas las rutas
    salvo `/health` exigen autenticación HTTP Basic. Sin ellas el servicio queda
    abierto (útil en local y en la red privada de Docker).
    """
    user = os.getenv("FLOW_AUTH_USER", "").strip()
    password = os.getenv("FLOW_AUTH_PASSWORD", "")
    if user and password:
        return user, password
    return None


def _valid_basic(header: str, expected: tuple[str, str]) -> bool:
    if not header.lower().startswith("basic "):
        return False
    try:
        decoded = base64.b64decode(header[6:].strip()).decode("utf-8")
    except (ValueError, UnicodeDecodeError):
        return False
    user, _, password = decoded.partition(":")
    expected_user, expected_password = expected
    return secrets.compare_digest(user, expected_user) and secrets.compare_digest(
        password, expected_password
    )


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

    credentials = _basic_credentials()
    if credentials is not None:

        @app.middleware("http")
        async def require_basic_auth(request: Request, call_next):
            if request.url.path == "/health":
                return await call_next(request)
            if _valid_basic(request.headers.get("authorization", ""), credentials):
                return await call_next(request)
            return PlainTextResponse(
                "Autenticación requerida",
                status_code=401,
                headers={"WWW-Authenticate": 'Basic realm="TCGA Flow"'},
            )

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
