"""Acceso a PostgreSQL (rol de escritura del ETL)."""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import psycopg
from psycopg import Connection
from psycopg.rows import dict_row

from .config import get_settings
from .logging_utils import get_logger

log = get_logger(__name__)


@contextmanager
def connect() -> Iterator[Connection]:
    settings = get_settings()
    with psycopg.connect(settings.database_url, row_factory=dict_row) as conn:
        yield conn


def _admin_url() -> str | None:
    """URL del superusuario para operaciones de DDL que requieren privilegios elevados."""
    import os

    user = os.getenv("POSTGRES_USER")
    password = os.getenv("POSTGRES_PASSWORD")
    host = os.getenv("POSTGRES_HOST", "postgres")
    port = os.getenv("POSTGRES_PORT", "5432")
    db = os.getenv("POSTGRES_DB", "tcga")
    if user and password:
        return f"postgresql://{user}:{password}@{host}:{port}/{db}"
    return None


def init_schema() -> None:
    sql_dir = get_settings().sql_dir
    if not sql_dir.exists():
        sql_dir = Path(__file__).resolve().parents[3] / "sql"
    schema_file = sql_dir / "001_schema.sql"
    if not schema_file.exists():
        raise RuntimeError(f"No se encontró el DDL en {schema_file}")
    ddl = schema_file.read_text(encoding="utf-8")
    # El DDL incluye CREATE SCHEMA, GRANT y ALTER DEFAULT PRIVILEGES que
    # requieren privilegios de superusuario. Usamos la URL del admin si está
    # disponible; si no, intentamos con el rol ETL (puede fallar si no es dueño).
    admin_url = _admin_url()
    url = admin_url or get_settings().database_url
    with psycopg.connect(url, row_factory=dict_row) as conn:
        conn.execute(ddl)
    log.info("Esquema asegurado", extra={"event": "schema_ready", "detail": str(schema_file)})


def copy_rows(
    conn: Connection,
    table: str,
    columns: Sequence[str],
    rows: Iterable[Sequence[Any]],
) -> int:
    """Carga filas con COPY (rápido) y devuelve el número insertado."""
    count = 0
    cols = ", ".join('"' + c.replace('"', '""') + '"' for c in columns)
    with conn.cursor() as cur:
        with cur.copy(f"COPY {table} ({cols}) FROM STDIN") as copy:
            for row in rows:
                copy.write_row(row)
                count += 1
    return count


def fetch_all(query: str, params: Sequence[Any] | None = None) -> list[dict[str, Any]]:
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(query, params or ())
            return list(cur.fetchall())


def fetch_one(query: str, params: Sequence[Any] | None = None) -> dict[str, Any] | None:
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(query, params or ())
            return cur.fetchone()
