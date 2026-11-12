"""Vistas previas paginadas de las salidas (PostgreSQL y Parquet).

Nunca se carga una tabla completa: PostgreSQL usa LIMIT/OFFSET y Parquet se
consulta por lotes con DuckDB.
"""

from __future__ import annotations

import datetime as dt
import decimal
import json
import uuid
from pathlib import Path
from typing import Any

from .catalog import get_node_type
from .schemas import NodeDefinition, OutputKind, OutputSpec
from .settings import FlowSettings

MAX_PAGE_SIZE = 200
DEFAULT_PAGE_SIZE = 100


class PreviewError(RuntimeError):
    pass


def _jsonable(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, (dt.datetime, dt.date, dt.time)):
        return value.isoformat()
    if isinstance(value, decimal.Decimal):
        return float(value)
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, (bytes, memoryview)):
        raw = bytes(value)
        return raw.decode("utf-8", errors="replace")
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    return str(value)


class PreviewService:
    def __init__(self, settings: FlowSettings) -> None:
        self.settings = settings

    def output_spec(self, node: NodeDefinition, output_key: str | None) -> OutputSpec:
        node_type = get_node_type(node.type)
        if not node_type.output_specs:
            raise PreviewError(f"El nodo {node.id} no declara salidas")
        if output_key:
            for spec in node_type.output_specs:
                if spec.key == output_key:
                    return spec
            raise PreviewError(f"Salida desconocida {output_key!r} para {node.id}")
        return node_type.output_specs[0]

    def preview(
        self,
        node: NodeDefinition,
        output_key: str | None = None,
        page: int = 1,
        page_size: int = DEFAULT_PAGE_SIZE,
    ) -> dict[str, Any]:
        page = max(1, int(page))
        page_size = max(1, min(MAX_PAGE_SIZE, int(page_size)))
        spec = self.output_spec(node, output_key)
        if spec.kind is OutputKind.TABLE:
            return self._preview_table(spec, page, page_size)
        if spec.kind is OutputKind.PARQUET:
            return self._preview_parquet(spec, page, page_size)
        return self._preview_json(spec, page, page_size)

    # ------------------------------------------------------------------ tablas
    @staticmethod
    def _split_table(name: str) -> tuple[str, str]:
        schema, _, table = name.partition(".")
        allowed = set("abcdefghijklmnopqrstuvwxyz_0123456789")
        if not schema or not table or not set(name.replace(".", "")) <= allowed:
            raise PreviewError(f"Nombre de tabla inválido: {name}")
        return schema, table

    def _preview_table(self, spec: OutputSpec, page: int, page_size: int) -> dict[str, Any]:
        from psycopg import sql

        from tcga.db import connect

        schema, table = self._split_table(spec.target)
        offset = (page - 1) * page_size
        try:
            with connect() as conn:
                meta = conn.execute(
                    """
                    SELECT column_name, data_type
                    FROM information_schema.columns
                    WHERE table_schema = %s AND table_name = %s
                    ORDER BY ordinal_position
                    """,
                    (schema, table),
                ).fetchall()
                if not meta:
                    raise PreviewError(f"La tabla {spec.target} no existe")
                total = conn.execute(
                    sql.SQL("SELECT count(*) AS n FROM {}").format(
                        sql.Identifier(schema, table)
                    )
                ).fetchone()["n"]
                size_row = conn.execute(
                    "SELECT pg_total_relation_size(%s::regclass) AS bytes", (spec.target,)
                ).fetchone()
                rows = conn.execute(
                    sql.SQL("SELECT * FROM {} LIMIT %s OFFSET %s").format(
                        sql.Identifier(schema, table)
                    ),
                    (page_size, offset),
                ).fetchall()
        except PreviewError:
            raise
        except Exception as exc:  # noqa: BLE001 - se devuelve a la UI
            raise PreviewError(f"No se pudo leer {spec.target}: {exc}") from exc

        return {
            "output": spec.key,
            "kind": spec.kind.value,
            "columns": [{"name": row["column_name"], "type": row["data_type"]} for row in meta],
            "rows": [{k: _jsonable(v) for k, v in dict(row).items()} for row in rows],
            "total_rows": int(total),
            "page": page,
            "page_size": page_size,
            "size_bytes": int(size_row["bytes"]) if size_row else None,
            "truncated": False,
        }

    # ----------------------------------------------------------------- parquet
    def _parquet_files(self, target: str) -> list[Path]:
        base = Path(self.settings.data_dir())
        if any(char in target for char in "*?["):
            return sorted(path for path in base.glob(target) if path.is_file())
        candidate = Path(target)
        path = candidate if candidate.is_absolute() else base / target
        return [path] if path.is_file() else []

    def _preview_parquet(
        self, spec: OutputSpec, page: int, page_size: int
    ) -> dict[str, Any]:
        from tcga.duck import duck_connect, sql_literal

        files = self._parquet_files(spec.target)
        if not files:
            raise PreviewError(f"No hay archivos Parquet en {spec.target}")
        pattern = (Path(self.settings.data_dir()) / spec.target).as_posix()
        if any(char in spec.target for char in "*?["):
            reader = f"read_parquet({sql_literal(pattern)})"
        else:
            reader = f"read_parquet({sql_literal(str(files[0]))})"
        offset = (page - 1) * page_size
        con = duck_connect(self.settings.etl)
        try:
            description = con.execute(f"DESCRIBE SELECT * FROM {reader}").fetchall()
            total = con.execute(f"SELECT count(*) FROM {reader}").fetchone()[0]
            cursor = con.execute(
                f"SELECT * FROM {reader} LIMIT {page_size} OFFSET {offset}"
            )
            column_names = [d[0] for d in cursor.description]
            rows = cursor.fetchall()
        except Exception as exc:  # noqa: BLE001
            raise PreviewError(f"No se pudo leer {spec.target}: {exc}") from exc
        finally:
            con.close()

        size = sum(path.stat().st_size for path in files)
        return {
            "output": spec.key,
            "kind": spec.kind.value,
            "columns": [{"name": row[0], "type": str(row[1])} for row in description],
            "rows": [
                {name: _jsonable(value) for name, value in zip(column_names, row)}
                for row in rows
            ],
            "total_rows": int(total),
            "page": page,
            "page_size": page_size,
            "size_bytes": size,
            "truncated": False,
        }

    # -------------------------------------------------------------------- json
    def _preview_json(self, spec: OutputSpec, page: int, page_size: int) -> dict[str, Any]:
        base = Path(self.settings.data_dir())
        target = spec.target
        if any(char in target for char in "*?["):
            files = sorted(path for path in base.glob(target) if path.is_file())
        else:
            candidate = Path(target)
            files = [candidate if candidate.is_absolute() else base / target]
        files = [path for path in files if path.is_file()]
        if not files:
            raise PreviewError(f"No hay archivos JSON en {spec.target}")

        payloads: list[Any] = []
        try:
            for path in files:
                payloads.append(json.loads(path.read_text(encoding="utf-8")))
        except Exception as exc:  # noqa: BLE001
            raise PreviewError(f"No se pudo leer {files[0].name}: {exc}") from exc

        records: list[Any] = []
        for payload in payloads:
            if isinstance(payload, dict) and isinstance(payload.get("data"), list):
                records.extend(payload["data"])
            elif isinstance(payload, list):
                records.extend(payload)
            else:
                records.append(payload)

        items: list[dict[str, Any]]
        if records and all(isinstance(item, dict) for item in records):
            keys: list[str] = []
            for item in records:
                for key in item:
                    if key not in keys:
                        keys.append(key)
            columns = [{"name": key, "type": "text"} for key in keys]
            items = [
                {key: _jsonable(item.get(key)) for key in keys}
                for item in records
            ]
        elif len(records) == 1 and isinstance(records[0], dict):
            items = [
                {"clave": key, "valor": _jsonable(value)}
                for key, value in records[0].items()
            ]
            columns = [{"name": "clave", "type": "text"}, {"name": "valor", "type": "json"}]
        else:
            items = [{"valor": _jsonable(item)} for item in records]
            columns = [{"name": "valor", "type": "text"}]

        offset = (page - 1) * page_size
        rows = items[offset : offset + page_size]
        return {
            "output": spec.key,
            "kind": spec.kind.value,
            "columns": columns,
            "rows": rows,
            "total_rows": len(items),
            "page": page,
            "page_size": page_size,
            "size_bytes": sum(path.stat().st_size for path in files),
            "truncated": False,
        }
