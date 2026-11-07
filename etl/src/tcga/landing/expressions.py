"""E03: descarga idempotente de perfiles de expresión desde el GDC."""

from __future__ import annotations

import shutil
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Sequence

from ..common import md5sum
from ..config import Settings, get_settings
from ..db import connect
from ..gdc.client import GDCClient, extract_download_safely, is_tar_gz
from ..logging_utils import get_logger
from ..ops.runs import pipeline_step, record_dq

log = get_logger(__name__)


@dataclass
class ControlRow:
    file_id: str
    project_id: str
    file_name: str
    file_size: int
    expected_md5: str | None


def chunk_by_size(rows: Sequence[ControlRow], max_bytes: int) -> list[list[ControlRow]]:
    """Agrupa archivos por tamaño declarado sin superar max_bytes."""
    batches: list[list[ControlRow]] = []
    current: list[ControlRow] = []
    current_size = 0
    for row in rows:
        size = max(int(row.file_size or 0), 0)
        if current and current_size + size > max_bytes:
            batches.append(current)
            current = []
            current_size = 0
        current.append(row)
        current_size += size
        if size >= max_bytes:
            batches.append(current)
            current = []
            current_size = 0
    if current:
        batches.append(current)
    return batches


def _ensure_control(project: str, hits: Iterable[dict[str, Any]]) -> None:
    rows = []
    for hit in hits:
        file_id = hit.get("file_id")
        if not file_id:
            continue
        rows.append(
            (
                file_id,
                project,
                hit.get("file_name"),
                hit.get("file_size"),
                hit.get("md5sum"),
            )
        )
    ids = [row[0] for row in rows]
    with connect() as conn:
        if rows:
            with conn.cursor() as cur:
                cur.executemany(
                    """
                    INSERT INTO ops.download_control
                        (file_id, project_id, file_name, file_size, expected_md5, status)
                    VALUES (%s, %s, %s, %s, %s, 'pending')
                    ON CONFLICT (file_id) DO UPDATE SET
                        project_id = EXCLUDED.project_id,
                        file_name = EXCLUDED.file_name,
                        file_size = EXCLUDED.file_size,
                        expected_md5 = EXCLUDED.expected_md5,
                        status = CASE
                            WHEN ops.download_control.expected_md5 IS DISTINCT FROM EXCLUDED.expected_md5
                            THEN 'pending' ELSE ops.download_control.status END,
                        updated_at = now()
                    """,
                    rows,
                )
        conn.execute(
            """
            DELETE FROM ops.download_control
            WHERE project_id = %s AND file_id <> ALL(%s)
            """,
            (project, ids),
        )


def _reset_missing(project: str, settings: Settings) -> int:
    """Marca como pendientes los archivos completos que ya no están en disco."""
    project_dir = settings.landing_expressions_dir(project)
    missing: list[str] = []
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT file_id, file_name FROM ops.download_control
            WHERE project_id = %s AND status = 'complete'
            """,
            (project,),
        ).fetchall()
        for row in rows:
            target = project_dir / row["file_id"] / Path(row["file_name"]).name
            if not target.exists():
                missing.append(row["file_id"])
        if missing:
            conn.execute(
                """
                UPDATE ops.download_control
                SET status = 'pending', md5_ok = NULL, bytes = NULL,
                    downloaded_at = NULL, updated_at = now()
                WHERE file_id = ANY(%s)
                """,
                (missing,),
            )
    if missing:
        log.warning(
            "Archivos completos que faltan en disco; se reencolan",
            extra={"event": "download_reset_missing", "files": len(missing)},
        )
    return len(missing)


def _pending_rows(project: str, limit: int | None, retry_failed: bool) -> list[ControlRow]:
    statuses = ["pending"]
    if retry_failed:
        statuses.append("failed")
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT file_id, project_id, file_name, file_size, expected_md5
                FROM ops.download_control
                WHERE project_id = %s AND status = ANY(%s)
                ORDER BY file_id
                """,
                (project, statuses),
            )
            rows = [
                ControlRow(
                    file_id=r["file_id"],
                    project_id=r["project_id"],
                    file_name=r["file_name"],
                    file_size=r["file_size"] or 0,
                    expected_md5=r["expected_md5"],
                )
                for r in cur.fetchall()
            ]
    return rows[:limit] if limit else rows


def _mark_failed(file_ids: Sequence[str], error: str) -> None:
    if not file_ids:
        return
    with connect() as conn:
        conn.execute(
            """
            UPDATE ops.download_control
            SET status = 'failed', last_error = %s, updated_at = now()
            WHERE file_id = ANY(%s)
            """,
            (error[:2000], list(file_ids)),
        )


def _mark_complete(row: ControlRow, path: Path) -> None:
    with connect() as conn:
        conn.execute(
            """
            UPDATE ops.download_control
            SET status = 'complete', md5_ok = TRUE, bytes = %s,
                downloaded_at = now(), updated_at = now(), last_error = NULL
            WHERE file_id = %s
            """,
            (path.stat().st_size, row.file_id),
        )


def _process_batch(
    client: GDCClient,
    project: str,
    rows: Sequence[ControlRow],
    settings: Settings,
) -> tuple[list[str], list[str]]:
    """Descarga y valida un lote. Devuelve (completados, fallidos)."""
    temp_dir = settings.landing_root() / ".tmp"
    temp_dir.mkdir(parents=True, exist_ok=True)
    batch_id = uuid.uuid4().hex
    archive = temp_dir / f"batch_{batch_id}.tar.gz"
    staging = temp_dir / f"extract_{batch_id}"
    ids = [r.file_id for r in rows]
    completed: list[str] = []
    failed: list[str] = []
    try:
        with connect() as conn:
            conn.execute(
                """
                UPDATE ops.download_control
                SET attempts = attempts + 1, updated_at = now()
                WHERE file_id = ANY(%s)
                """,
                (list(ids),),
            )
        size = client.download_batch(ids, archive)
        log.info(
            "Lote descargado",
            extra={
                "event": "download_batch",
                "files": len(rows),
                "bytes": size,
                "detail": f"proyecto={project}",
            },
        )
        project_dir = settings.landing_expressions_dir(project)
        if len(rows) == 1 and not is_tar_gz(archive):
            row = rows[0]
            digest = md5sum(archive)
            if row.expected_md5 and digest.lower() != row.expected_md5.lower():
                raise ValueError(
                    f"md5 inválido para {row.file_id}: {digest} != {row.expected_md5}"
                )
            target = project_dir / row.file_id / Path(row.file_name).name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(archive), target)
            _mark_complete(row, target)
            return [row.file_id], []

        extracted = extract_download_safely(archive, staging)
        for row in rows:
            try:
                base = Path(row.file_name).name
                source = extracted.get(base)
                if source is None:
                    raise FileNotFoundError(f"no está {base} dentro del tar del GDC")
                digest = md5sum(source)
                if row.expected_md5 and digest.lower() != row.expected_md5.lower():
                    raise ValueError(f"md5 inválido: {digest} != {row.expected_md5}")
                target = project_dir / row.file_id / Path(row.file_name).name
                target.parent.mkdir(parents=True, exist_ok=True)
                if target.exists():
                    target.unlink()
                shutil.move(str(source), target)
                _mark_complete(row, target)
                completed.append(row.file_id)
            except Exception as exc:  # noqa: BLE001 - un archivo no tumba el lote
                failed.append(row.file_id)
                _mark_failed([row.file_id], str(exc))
                log.error(
                    "Fallo al validar archivo %s: %s",
                    row.file_id,
                    exc,
                    extra={"event": "download_file_failed"},
                )
    except Exception as exc:  # noqa: BLE001 - se marcan todos los del lote
        failed.extend(r.file_id for r in rows if r.file_id not in completed)
        _mark_failed([r.file_id for r in rows if r.file_id not in completed], str(exc))
        log.error(
            "Fallo el lote de descarga: %s",
            exc,
            extra={"event": "download_batch_failed", "files": len(rows)},
        )
    finally:
        archive.unlink(missing_ok=True)
        shutil.rmtree(staging, ignore_errors=True)
    return completed, failed


def _stats(project: str) -> dict[str, int]:
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT status, count(*) AS n FROM ops.download_control
            WHERE project_id = %s GROUP BY status
            """,
            (project,),
        ).fetchall()
    stats = {"pending": 0, "complete": 0, "failed": 0}
    for row in rows:
        stats[row["status"]] = row["n"]
    return stats


def download_expressions(
    projects: list[str],
    limit: int | None = None,
    retry_failed: bool = False,
    settings: Settings | None = None,
) -> dict[str, dict[str, int]]:
    settings = settings or get_settings()
    client = GDCClient(settings)
    results: dict[str, dict[str, int]] = {}

    for project in projects:
        from .files import read_manifest

        manifest = read_manifest(project, settings)
        hits = manifest.get("hits", [])
        with pipeline_step(
            "download_expressions",
            project,
            params={"limit": limit, "retry_failed": retry_failed},
        ) as run:
            _ensure_control(project, hits)
            _reset_missing(project, settings)
            pending = _pending_rows(project, limit, retry_failed)
            batches = chunk_by_size(pending, settings.download_batch_bytes)
            run.count(rows_in=len(hits), rows_out=0)
            log.info(
                "Descargas pendientes",
                extra={
                    "event": "download_plan",
                    "files": len(pending),
                    "detail": f"lotes={len(batches)}",
                },
            )
            completed_total = 0
            failed_total = 0
            if pending:
                with ThreadPoolExecutor(max_workers=settings.download_workers) as executor:
                    futures = {
                        executor.submit(_process_batch, client, project, batch, settings): batch
                        for batch in batches
                    }
                    for future in as_completed(futures):
                        completed, failed = future.result()
                        completed_total += len(completed)
                        failed_total += len(failed)
                        stats = _stats(project)
                        log.info(
                            "Progreso de descargas",
                            extra={
                                "event": "download_progress",
                                "files": stats["complete"],
                                "detail": (
                                    f"completos={stats['complete']} pendientes={stats['pending']} "
                                    f"fallidos={stats['failed']}"
                                ),
                            },
                        )
            stats = _stats(project)
            run.count(rows_in=len(hits), rows_out=stats["complete"])
            record_dq(
                run.run_id,
                run.step,
                project,
                "downloads_md5_complete",
                "critical",
                failed_total == 0 and (limit is not None or stats["pending"] == 0),
                (
                    f"manifiesto={len(hits)} completos={stats['complete']} "
                    f"pendientes={stats['pending']} fallidos={stats['failed']}"
                ),
            )
            record_dq(
                run.run_id,
                run.step,
                project,
                "downloads_manifest_matches_control",
                "warning",
                stats["complete"] + stats["pending"] + stats["failed"] == len(hits),
                f"control={sum(stats.values())} manifiesto={len(hits)}",
            )
            results[project] = stats
    return results


def download_progress(project: str, settings: Settings | None = None) -> dict[str, int]:
    return _stats(project)
