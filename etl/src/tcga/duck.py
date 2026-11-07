"""Configuración común de DuckDB y escritura de Parquet."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Sequence

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq

from .config import Settings, get_settings


def duck_connect(settings: Settings | None = None) -> duckdb.DuckDBPyConnection:
    settings = settings or get_settings()
    temp_dir = settings.data_dir / ".duckdb_tmp"
    temp_dir.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect()
    con.execute(f"SET memory_limit = '{settings.duckdb_memory_limit}'")
    con.execute(f"SET threads = {settings.duckdb_threads}")
    con.execute(f"SET temp_directory = '{temp_dir.as_posix().replace(chr(39), chr(39) * 2)}'")
    con.execute("SET preserve_insertion_order = false")
    return con


def write_parquet(
    path: Path,
    rows: Sequence[dict[str, Any]] | Sequence[Sequence[Any]],
    schema: pa.Schema | None = None,
    columns: Sequence[str] | None = None,
) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    if rows and isinstance(rows[0], dict):
        table = pa.Table.from_pylist(list(rows), schema=schema)
    elif rows:
        table = pa.table(
            {name: [row[i] for row in rows] for i, name in enumerate(columns or [])}
        )
    elif schema is not None:
        table = schema.empty_table()
    elif columns:
        table = pa.table({name: pa.array([], type=pa.string()) for name in columns})
    else:
        table = pa.table({})
    tmp = path.with_suffix(path.suffix + ".tmp")
    pq.write_table(table, tmp, compression="zstd")
    tmp.replace(path)
    return table.num_rows


def sql_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"
