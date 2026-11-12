"""Ejecutor de un nodo en un subproceso.

Uso interno del motor:

    python -m tcga_flow.runner <tipo_de_nodo> '<json_de_parametros>'

La última línea de la salida lleva el marcador ``##RESULT##`` con el resumen
en JSON. El resto de líneas son logs (JSON del ETL) que el motor reenvía por
SSE a la UI.
"""

from __future__ import annotations

import json
import sys
from typing import Any

RESULT_MARKER = "##RESULT##"


def _emit(payload: dict[str, Any]) -> None:
    sys.stdout.write(RESULT_MARKER + json.dumps(payload, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def run_node(type_id: str, params: dict[str, Any]) -> int:
    from tcga.logging_utils import configure_logging

    from .catalog import get_node_type
    from .settings import get_flow_settings

    settings = get_flow_settings()
    configure_logging(settings.log_level)

    def log(level: str, message: str) -> None:
        sys.stdout.write(
            json.dumps(
                {"level": level.upper(), "message": message, "logger": "tcga_flow"},
                ensure_ascii=False,
            )
            + "\n"
        )
        sys.stdout.flush()

    try:
        node_type = get_node_type(type_id)
        validated = node_type.param_model.model_validate(params or {})
        from .catalog import RunContext

        context = RunContext(settings=settings, log=log)
        result = node_type.run(validated, context)
        _emit(
            {
                "ok": True,
                "type": type_id,
                "rows_in": result.rows_in,
                "rows_out": result.rows_out,
                "outputs": result.outputs,
                "detail": result.detail,
            }
        )
        return 0
    except Exception as exc:  # noqa: BLE001 - el motor necesita el mensaje
        log("ERROR", f"{type(exc).__name__}: {exc}")
        _emit({"ok": False, "type": type_id, "error": f"{type(exc).__name__}: {exc}"})
        return 1


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) < 1:
        sys.stderr.write("Uso: python -m tcga_flow.runner <tipo> [<json>]\n")
        return 2
    type_id = args[0]
    try:
        params = json.loads(args[1]) if len(args) > 1 and args[1] else {}
    except json.JSONDecodeError as exc:
        sys.stderr.write(f"Parámetros inválidos: {exc}\n")
        return 2
    return run_node(type_id, params)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
