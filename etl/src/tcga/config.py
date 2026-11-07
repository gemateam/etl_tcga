"""Configuración central del ETL, leída de variables de entorno."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_PROJECTS = ("TCGA-BRCA",)


def _env(name: str, default: str | None = None) -> str:
    value = os.getenv(name, default)
    if value is None or value.strip() == "":
        raise RuntimeError(f"Falta la variable de entorno obligatoria {name}")
    return value.strip()


def _int_env(name: str, default: int) -> int:
    raw = os.getenv(name)
    return int(raw) if raw not in (None, "") else default


def parse_projects(value: str | None) -> tuple[str, ...]:
    if not value:
        return DEFAULT_PROJECTS
    items = tuple(sorted({p.strip() for p in value.split(",") if p.strip()}))
    return items or DEFAULT_PROJECTS


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    gdc_api_url: str
    projects: tuple[str, ...]
    database_url: str
    duckdb_memory_limit: str
    duckdb_threads: int
    download_workers: int
    download_batch_bytes: int
    http_timeout: int
    http_retries: int
    http_backoff: float
    log_level: str
    sql_dir: Path = field(default_factory=lambda: Path(_env("SQL_DIR", "/sql")))
    gdc_data_release: str | None = field(default=None, compare=False)

    @classmethod
    def from_env(cls) -> "Settings":
        db_url = os.getenv("DATABASE_URL")
        if not db_url:
            db_url = (
                "postgresql://{user}:{password}@{host}:{port}/{db}".format(
                    user=_env("ETL_DB_USER", "etl_rw"),
                    password=_env("ETL_DB_PASSWORD", "etl_rw_password"),
                    host=_env("POSTGRES_HOST", "postgres"),
                    port=_env("POSTGRES_PORT", "5432"),
                    db=_env("POSTGRES_DB", "tcga"),
                )
            )
        return cls(
            data_dir=Path(_env("DATA_DIR", "/data")),
            gdc_api_url=_env("GDC_API_URL", "https://api.gdc.cancer.gov").rstrip("/"),
            projects=parse_projects(os.getenv("GDC_PROJECTS")),
            database_url=db_url,
            duckdb_memory_limit=_env("DUCKDB_MEMORY_LIMIT", "2GB"),
            duckdb_threads=_int_env("DUCKDB_THREADS", 2),
            download_workers=max(1, min(2, _int_env("GDC_DOWNLOAD_WORKERS", 2))),
            download_batch_bytes=_int_env("GDC_DOWNLOAD_BATCH_BYTES", 1_000_000_000),
            http_timeout=_int_env("GDC_HTTP_TIMEOUT", 120),
            http_retries=_int_env("GDC_HTTP_RETRIES", 5),
            http_backoff=float(os.getenv("GDC_HTTP_BACKOFF", "2")),
            log_level=_env("LOG_LEVEL", "INFO").upper(),
        )

    # ------------------------------------------------------------------ paths
    def landing_root(self) -> Path:
        return self.data_dir / "landing"

    def landing_cases_dir(self, project: str) -> Path:
        return self.landing_root() / "cases" / project

    def landing_files_dir(self, project: str) -> Path:
        return self.landing_root() / "files" / project

    def landing_expressions_dir(self, project: str) -> Path:
        return self.landing_root() / "expressions" / project

    def bronze_root(self) -> Path:
        return self.data_dir / "bronze"

    def silver_root(self) -> Path:
        return self.data_dir / "silver"

    def gold_root(self) -> Path:
        return self.data_dir / "gold"

    def resolve_projects(self, override: list[str] | None) -> list[str]:
        if override:
            cleaned = sorted({p.strip() for p in override if p and p.strip()})
            if cleaned:
                return cleaned
        return list(self.projects)

    def ensure_dirs(self) -> None:
        for path in (
            self.landing_root(),
            self.bronze_root(),
            self.silver_root(),
            self.gold_root(),
        ):
            path.mkdir(parents=True, exist_ok=True)


def get_settings() -> Settings:
    return Settings.from_env()
