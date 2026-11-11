"""Regresión de la migración idempotente de ``tumor_sample_barcode``.

El DDL usa ``CREATE TABLE IF NOT EXISTS``; si la tabla ya existía sin la
columna, el ``CREATE`` no la añade. Estas pruebas cubren la migración que
alinea las bases existentes con el esquema actual.
"""

from __future__ import annotations

import os
import re
import uuid
from pathlib import Path

import pytest

from tcga.config import get_settings


def _schema_sql() -> str:
    sql_dir = get_settings().sql_dir
    if not sql_dir.exists():
        sql_dir = Path(__file__).resolve().parents[1] / "sql"
    return (sql_dir / "001_schema.sql").read_text(encoding="utf-8")


def _normalize(sql: str) -> str:
    return re.sub(r"\s+", " ", sql).strip().lower()


def test_schema_declares_idempotent_barcode_migration() -> None:
    ddl = _normalize(_schema_sql())

    assert (
        "alter table bronze.mutations add column if not exists tumor_sample_barcode text"
        in ddl
    )
    assert (
        "alter table silver.mutations add column if not exists tumor_sample_barcode text"
        in ddl
    )


def _admin_url(database: str) -> str:
    host = os.getenv("POSTGRES_HOST", "localhost")
    port = os.getenv("POSTGRES_PORT", "5432")
    user = os.getenv("POSTGRES_USER", "tcga_admin")
    password = os.getenv("POSTGRES_PASSWORD", "")
    return f"postgresql://{user}:{password}@{host}:{port}/{database}"


@pytest.mark.integration
@pytest.mark.skipif(
    os.getenv("TCGA_INTEGRATION") != "1",
    reason="define TCGA_INTEGRATION=1 y levanta PostgreSQL para ejecutarla",
)
def test_init_schema_adds_columns_to_existing_tables(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import psycopg

    from tcga.db import init_schema

    database = f"tcga_migration_{uuid.uuid4().hex[:8]}"
    admin_url = _admin_url("postgres")
    with psycopg.connect(admin_url, autocommit=True) as conn:
        conn.execute(f'CREATE DATABASE "{database}"')

    try:
        monkeypatch.setenv("DATABASE_URL", _admin_url(database))
        # Simula una base creada antes de que existiera la columna: se aplica
        # el esquema, se elimina la columna y se vuelve a aplicar el DDL.
        init_schema()
        with psycopg.connect(_admin_url(database), autocommit=True) as conn:
            conn.execute(
                "ALTER TABLE bronze.mutations DROP COLUMN tumor_sample_barcode"
            )
            conn.execute(
                "ALTER TABLE silver.mutations DROP COLUMN tumor_sample_barcode"
            )
        init_schema()

        with psycopg.connect(_admin_url(database)) as conn:
            rows = conn.execute(
                """
                SELECT table_schema
                FROM information_schema.columns
                WHERE table_name = 'mutations'
                  AND column_name = 'tumor_sample_barcode'
                ORDER BY table_schema
                """
            ).fetchall()

        assert [row[0] for row in rows] == ["bronze", "silver"]
    finally:
        with psycopg.connect(admin_url, autocommit=True) as conn:
            conn.execute(f'DROP DATABASE IF EXISTS "{database}" WITH (FORCE)')
