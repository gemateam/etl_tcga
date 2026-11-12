"""Configuración del servicio `flow`.

Reutiliza `tcga.config.Settings` para no duplicar la resolución de rutas y
variables de entorno del ETL. Aquí solo se añaden las rutas propias del
ejecutor visual.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from tcga.config import Settings, get_settings as get_etl_settings


def _env(name: str, default: str | None = None) -> str:
    value = os.getenv(name, default)
    if value is None or value.strip() == "":
        raise RuntimeError(f"Falta la variable de entorno obligatoria {name}")
    return value.strip()


@dataclass(frozen=True)
class FlowSettings:
    etl: Settings
    workflows_dir: Path
    frontend_dist: Path
    gemamx_dir: Path
    flow_port: int
    log_level: str

    @classmethod
    def from_env(cls) -> "FlowSettings":
        etl = get_etl_settings()

        raw_workflows = os.getenv("FLOW_WORKFLOWS_DIR")
        if raw_workflows:
            workflows_dir = Path(raw_workflows)
        else:
            workflows_dir = Path(__file__).resolve().parents[2] / "workflows"

        raw_dist = os.getenv("FLOW_FRONTEND_DIST")
        if raw_dist:
            frontend_dist = Path(raw_dist)
        else:
            frontend_dist = Path(__file__).resolve().parents[2] / "frontend" / "dist"

        return cls(
            etl=etl,
            workflows_dir=workflows_dir,
            frontend_dist=frontend_dist,
            gemamx_dir=Path(_env("GEMAMX_DIR", "D:/RespaldoLaptopHP/ProyectosIA/GEMAMX")),
            flow_port=int(_env("FLOW_PORT", "8100")),
            log_level=_env("LOG_LEVEL", "INFO").upper(),
        )

    # ------------------------------------------------------------------ rutas
    def data_dir(self) -> Path:
        return self.etl.data_dir

    def resolve_path(self, relative: str) -> Path:
        """Resuelve una ruta del catálogo relativa a DATA_DIR."""
        candidate = Path(relative)
        if candidate.is_absolute():
            return candidate
        return self.data_dir() / candidate


def get_flow_settings() -> FlowSettings:
    return FlowSettings.from_env()
