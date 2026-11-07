"""Registro de ejecuciones, versión de datos y calidad en PostgreSQL."""

from __future__ import annotations

import json
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Iterator

from ..config import get_settings
from ..db import connect
from ..logging_utils import bind_run, get_logger, reset_run

log = get_logger(__name__)

_CRITICAL_ERROR = "Fallo crítico de calidad de datos"


class DataQualityError(RuntimeError):
    pass


@dataclass
class Run:
    run_id: str
    step: str
    project_id: str | None
    gdc_data_release: str | None
    rows_in: int = 0
    rows_out: int = 0
    params: dict[str, Any] = field(default_factory=dict)

    def count(self, rows_in: int | None = None, rows_out: int | None = None) -> None:
        if rows_in is not None:
            self.rows_in = int(rows_in)
        if rows_out is not None:
            self.rows_out = int(rows_out)


def get_gdc_status() -> dict[str, Any] | None:
    """Lee /status del GDC con tolerancia a fallos de red."""

    from ..gdc.client import GDCClient

    try:
        return GDCClient(get_settings()).status()
    except Exception as exc:  # noqa: BLE001 - no debe tumbar un paso de datos
        log.warning("No se pudo consultar /status del GDC: %s", exc)
        return None


def record_gdc_status(status: dict[str, Any] | None) -> str | None:
    if not status:
        return None
    release = status.get("data_release")
    if not release:
        return None
    previous = None
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT data_release FROM ops.gdc_status WHERE id = 1")
            row = cur.fetchone()
            previous = row["data_release"] if row else None
            cur.execute(
                """
                INSERT INTO ops.gdc_status (id, data_release, tag, commit, checked_at)
                VALUES (1, %s, %s, %s, now())
                ON CONFLICT (id) DO UPDATE SET
                    data_release = EXCLUDED.data_release,
                    tag = EXCLUDED.tag,
                    commit = EXCLUDED.commit,
                    checked_at = now()
                """,
                (release, status.get("tag"), status.get("commit")),
            )
    if previous and previous != release:
        log.warning(
            "Nueva versión de datos del GDC: %s (antes %s)",
            release,
            previous,
            extra={"event": "gdc_data_release_changed"},
        )
    return release


def recorded_release() -> str | None:
    """Última versión del GDC registrada en ops.gdc_status."""
    with connect() as conn:
        row = conn.execute("SELECT data_release FROM ops.gdc_status WHERE id = 1").fetchone()
        return row["data_release"] if row else None


@contextmanager
def pipeline_step(
    step: str,
    project_id: str | None = None,
    params: dict[str, Any] | None = None,
) -> Iterator[Run]:
    """Registra un paso en ops.pipeline_runs y aplica DQ crítica al final."""

    status = get_gdc_status()
    release = record_gdc_status(status)
    run_id = str(uuid.uuid4())
    run = Run(
        run_id=run_id,
        step=step,
        project_id=project_id,
        gdc_data_release=release,
        params=params or {},
    )
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO ops.pipeline_runs
                (run_id, step, project_id, status, rows_in, rows_out, params,
                 gdc_data_release, started_at)
            VALUES (%s, %s, %s, 'running', 0, 0, %s::jsonb, %s, now())
            """,
            (run_id, step, project_id, json.dumps(run.params), release),
        )

    tokens = bind_run(run_id, step, project_id)
    log.info("Inicio del paso", extra={"event": "step_start"})
    try:
        yield run
        with connect() as conn:
            critical = conn.execute(
                """
                SELECT check_name, details FROM ops.dq_results
                WHERE run_id = %s AND severity = 'critical' AND NOT passed
                ORDER BY created_at
                """,
                (run_id,),
            ).fetchall()
            if critical:
                detail = "; ".join(
                    f"{r['check_name']}: {r['details']}" for r in critical[:10]
                )
                raise DataQualityError(f"{_CRITICAL_ERROR}: {detail}")
            conn.execute(
                """
                UPDATE ops.pipeline_runs
                SET status = 'success', rows_in = %s, rows_out = %s,
                    finished_at = now()
                WHERE run_id = %s
                """,
                (run.rows_in, run.rows_out, run_id),
            )
        log.info(
            "Paso completado",
            extra={
                "event": "step_success",
                "rows": run.rows_out,
                "detail": f"in={run.rows_in} out={run.rows_out}",
            },
        )
    except Exception as exc:  # noqa: BLE001 - se registra y se relanza
        with connect() as conn:
            conn.execute(
                """
                UPDATE ops.pipeline_runs
                SET status = 'failed', rows_in = %s, rows_out = %s,
                    finished_at = now(), error = %s
                WHERE run_id = %s
                """,
                (run.rows_in, run.rows_out, str(exc)[:4000], run_id),
            )
        log.error("Paso fallido: %s", exc, exc_info=True, extra={"event": "step_failed"})
        raise
    finally:
        reset_run(tokens)


def record_dq(
    run_id: str,
    step: str,
    project_id: str | None,
    check_name: str,
    severity: str,
    passed: bool,
    details: str,
) -> None:
    if severity not in {"critical", "warning"}:
        raise ValueError("severity debe ser 'critical' o 'warning'")
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO ops.dq_results
                (run_id, step, project_id, check_name, severity, passed, details)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            """,
            (run_id, step, project_id, check_name, severity, passed, details[:4000]),
        )
    if not passed:
        log.warning(
            "DQ %s: %s",
            severity,
            check_name,
            extra={"event": "dq_failure", "detail": details[:500]},
        )
