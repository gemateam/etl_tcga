"""E02: manifiesto de archivos de expresión génica a landing."""

from __future__ import annotations

from ..common import utcnow, write_json
from ..config import Settings, get_settings
from ..gdc import GDCClient
from ..gdc.fields import FILE_FIELDS, expression_filters
from ..logging_utils import get_logger
from ..ops.runs import get_gdc_status, pipeline_step, record_dq

log = get_logger(__name__)


def extract_files_manifest(projects: list[str], settings: Settings | None = None) -> dict[str, int]:
    settings = settings or get_settings()
    settings.ensure_dirs()
    client = GDCClient(settings)
    status = get_gdc_status() or {}
    result: dict[str, int] = {}

    for project in projects:
        with pipeline_step("extract_files_manifest", project) as run:
            hits, pagination = client.file_hits(project)
            target = settings.landing_files_dir(project)
            manifest = {
                "project_id": project,
                "generated_at": utcnow(),
                "data_release": status.get("data_release"),
                "query": {
                    "endpoint": "/files",
                    "filters": expression_filters(project),
                    "fields": list(FILE_FIELDS),
                },
                "pagination": pagination,
                "total_files": len(hits),
                "hits": hits,
            }
            write_json(target / "manifest.json", manifest)
            run.count(rows_in=len(hits), rows_out=len(hits))
            ids = [h.get("file_id") for h in hits]
            record_dq(
                run.run_id,
                run.step,
                project,
                "files_manifest_not_empty",
                "warning",
                len(hits) > 0,
                f"archivos={len(hits)}",
            )
            record_dq(
                run.run_id,
                run.step,
                project,
                "files_unique_ids",
                "critical",
                len(ids) == len(set(ids)),
                f"filas={len(ids)} únicos={len(set(ids))}",
            )
            without_case = sum(1 for h in hits if not h.get("cases"))
            record_dq(
                run.run_id,
                run.step,
                project,
                "files_with_case",
                "warning",
                without_case == 0,
                f"archivos_sin_caso={without_case}",
            )
            result[project] = len(hits)
            log.info(
                "Manifiesto escrito",
                extra={"event": "landing_files", "files": len(hits), "detail": str(target)},
            )
    return result


def read_manifest(project: str, settings: Settings | None = None) -> dict:
    settings = settings or get_settings()
    import json

    path = settings.landing_files_dir(project) / "manifest.json"
    if not path.exists():
        raise FileNotFoundError(
            f"No existe el manifiesto {path}. Ejecuta primero extract-files-manifest."
        )
    return json.loads(path.read_text(encoding="utf-8"))
