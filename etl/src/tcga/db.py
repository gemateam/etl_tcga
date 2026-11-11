"""Acceso a PostgreSQL (rol de escritura del ETL)."""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import psycopg
from psycopg import Connection, sql
from psycopg.errors import UndefinedObject
from psycopg.rows import dict_row

from .config import get_settings
from .logging_utils import get_logger

log = get_logger(__name__)


@contextmanager
def connect() -> Iterator[Connection]:
    settings = get_settings()
    with psycopg.connect(settings.database_url, row_factory=dict_row) as conn:
        yield conn


def _admin_url(database: str | None = None) -> str | None:
    """URL del superusuario para operaciones de DDL que requieren privilegios elevados.

    Si se indica ``database`` se usa esa base; en caso contrario se respeta la
    base de ``DATABASE_URL`` (importante para las pruebas de integración, que
    trabajan sobre ``tcga_test``) y se cae a ``POSTGRES_DB``.
    """
    import os
    from urllib.parse import urlsplit

    user = os.getenv("POSTGRES_USER")
    password = os.getenv("POSTGRES_PASSWORD")
    if not user or not password:
        return None
    current = urlsplit(get_settings().database_url)
    host = current.hostname or os.getenv("POSTGRES_HOST", "postgres")
    port = current.port or os.getenv("POSTGRES_PORT", "5432")
    db = database or current.path.lstrip("/") or os.getenv("POSTGRES_DB", "tcga")
    return f"postgresql://{user}:{password}@{host}:{port}/{db}"


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
        if admin_url:
            _grant_etl_privileges(conn)
    log.info("Esquema asegurado", extra={"event": "schema_ready", "detail": str(schema_file)})


def _grant_etl_privileges(conn: Connection) -> None:
    """Da acceso al rol ETL cuando el DDL se aplicó con el superusuario.

    En docker-compose el DDL lo ejecuta ``etl_rw`` y las tablas ya le
    pertenecen. Fuera de ese flujo (por ejemplo, ``tcga init-db`` sobre una
    base nueva o las pruebas de integración) el dueño es el administrador y
    este GRANT evita errores de permisos en los pasos del pipeline.
    """
    import os

    role = os.getenv("ETL_DB_USER", "etl_rw")
    try:
        conn.execute(
            sql.SQL(
                "GRANT USAGE, CREATE ON SCHEMA bronze, silver, gold, ops TO {}"
            ).format(sql.Identifier(role))
        )
        conn.execute(
            sql.SQL(
                "GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA "
                "bronze, silver, gold, ops TO {}"
            ).format(sql.Identifier(role))
        )
        conn.execute(
            sql.SQL(
                "GRANT ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA "
                "bronze, silver, gold, ops TO {}"
            ).format(sql.Identifier(role))
        )
        conn.execute(
            sql.SQL(
                "ALTER DEFAULT PRIVILEGES IN SCHEMA bronze, silver, gold, ops "
                "GRANT ALL ON TABLES TO {}"
            ).format(sql.Identifier(role))
        )
        conn.execute(
            sql.SQL(
                "ALTER DEFAULT PRIVILEGES IN SCHEMA bronze, silver, gold, ops "
                "GRANT ALL ON SEQUENCES TO {}"
            ).format(sql.Identifier(role))
        )
    except UndefinedObject:
        log.warning(
            "El rol ETL no existe; se omiten los GRANT",
            extra={"event": "schema_grant_skipped", "detail": role},
        )


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
