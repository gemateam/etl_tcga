"""Configuración del contenedor web (solo lectura)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class WebSettings:
    data_dir: Path
    database_url: str
    duckdb_memory_limit: str
    duckdb_threads: int

    @classmethod
    def from_env(cls) -> "WebSettings":
        database_url = os.getenv("WEB_DATABASE_URL")
        if not database_url:
            user = os.getenv("WEB_DB_USER", "web_ro")
            password = os.getenv("WEB_DB_PASSWORD", "web_ro_password")
            host = os.getenv("POSTGRES_HOST", "postgres")
            port = os.getenv("POSTGRES_PORT", "5432")
            database = os.getenv("POSTGRES_DB", "tcga")
            database_url = f"postgresql://{user}:{password}@{host}:{port}/{database}"
        return cls(
            data_dir=Path(os.getenv("DATA_DIR", "/data")),
            database_url=database_url,
            duckdb_memory_limit=os.getenv("DUCKDB_MEMORY_LIMIT", "1GB"),
            duckdb_threads=int(os.getenv("DUCKDB_THREADS", "2")),
        )


settings = WebSettings.from_env()
