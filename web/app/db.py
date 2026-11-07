"""Acceso de solo lectura a PostgreSQL."""

from __future__ import annotations

from typing import Any, Sequence

import psycopg
from psycopg.errors import UndefinedColumn, UndefinedTable
from psycopg.rows import dict_row
from psycopg import Error as PsycopgError

from .config import settings


def query(sql: str, params: Sequence[Any] | None = None) -> list[dict[str, Any]]:
    with psycopg.connect(settings.database_url, row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params or ())
            return list(cur.fetchall())


def query_one(sql: str, params: Sequence[Any] | None = None) -> dict[str, Any] | None:
    rows = query(sql, params)
    return rows[0] if rows else None


def safe_query(sql: str, params: Sequence[Any] | None = None) -> list[dict[str, Any]] | None:
    """Devuelve None si el esquema todavía no existe."""
    try:
        return query(sql, params)
    except (UndefinedTable, UndefinedColumn, PsycopgError):
        return None


def safe_scalar(sql: str, params: Sequence[Any] | None = None) -> Any | None:
    rows = safe_query(sql, params)
    if not rows:
        return None
    return next(iter(rows[0].values()))


def ping() -> bool:
    with psycopg.connect(settings.database_url, connect_timeout=3) as conn:
        conn.execute("SELECT 1")
    return True
