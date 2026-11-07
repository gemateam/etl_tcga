"""Logs estructurados en JSON para la salida estándar de los contenedores."""

from __future__ import annotations

import contextvars
import json
import logging
import sys
from datetime import datetime, timezone
from typing import Any

run_id_var: contextvars.ContextVar[str | None] = contextvars.ContextVar("run_id", default=None)
step_var: contextvars.ContextVar[str | None] = contextvars.ContextVar("step", default=None)
project_var: contextvars.ContextVar[str | None] = contextvars.ContextVar("project", default=None)


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "run_id": run_id_var.get(),
            "step": step_var.get(),
            "project": project_var.get(),
        }
        for key in ("event", "rows", "files", "bytes", "duration_s", "detail"):
            value = getattr(record, key, None)
            if value is not None:
                payload[key] = value
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, default=str)


def configure_logging(level: str = "INFO") -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(getattr(logging, level.upper(), logging.INFO))
    for noisy in ("urllib3", "requests"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)


def bind_run(run_id: str, step: str, project: str | None) -> tuple[contextvars.Token, ...]:
    return (run_id_var.set(run_id), step_var.set(step), project_var.set(project))


def reset_run(tokens: tuple[contextvars.Token, ...]) -> None:
    for token in reversed(tokens):
        token.var.reset(token)
