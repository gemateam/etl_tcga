"""L03: manifiesto de archivos MAF (mutaciones somáticas) a landing."""

from __future__ import annotations

from ..common import utcnow, write_json
from ..config import Settings, get_settings
from ..gdc import GDCClient
from ..gdc.maf_fields import MAF_FILE_FIELDS, maf_filters
from ..logging_utils import get_logger
from ..ops.runs import get_gdc_status, pipeline_step, record_dq

log = get_logger(__name__)


def extract_maf_manifest(
    projects: list[str],
    settings: Settings | None = None,
) -> dict[str, int]:
    """Descarga el manifiesto de archivos MAF del GDC y lo guarda en landing."""
    settings = settings or get_settings()
    settings.ensure_dirs()
    client = GDCClient(settings)
    gdc_status = get_gdc_status() or {}
    result: dict[str, int] = {}

    for project in projects:
        with pipeline_step("extract_maf_manifest", project) as run:
            hits: list[dict] = []
            offset = 0
            pagination: dict = {}
            page_size = 500

            while True:
                body = {
                    "filters": maf_filters(project),
                    "fields": ",".join(MAF_FILE_FIELDS),
                    "format": "JSON",
                    "size": page_size,
                    "from": offset,
                    "sort": "file_id:asc",
                }
                payload = client._post_json("files", body)
                data = payload.get("data", {})
                batch = data.get("hits", [])
                pagination = data.get("pagination", {})
                hits.extend(batch)
                if not batch:
                    break
                offset += len(batch)
                total = pagination.get("total")
                if total is not None and offset >= total:
                    break

            target = settings.landing_root() / "mutations" / project
            target.mkdir(parents=True, exist_ok=True)
            manifest = {
                "project_id": project,
                "generated_at": utcnow(),
                "data_release": gdc_status.get("data_release"),
                "query": {
                    "endpoint": "/files",
                    "filters": maf_filters(project),
                    "fields": list(MAF_FILE_FIELDS),
                },
                "pagination": pagination,
                "total_files": len(hits),
                "hits": hits,
            }
            write_json(target / "maf_manifest.json", manifest)
            run.count(rows_in=len(hits), rows_out=len(hits))

            ids = [h.get("file_id") for h in hits]
            record_dq(
                run.run_id, run.step, project,
                "maf_manifest_not_empty", "warning",
                len(hits) > 0,
                f"archivos_maf={len(hits)}",
            )
            record_dq(
                run.run_id, run.step, project,
                "maf_unique_ids", "critical",
                len(ids) == len(set(ids)),
                f"filas={len(ids)} únicos={len(set(ids))}",
            )

            result[project] = len(hits)
            log.info(
                "Manifiesto MAF escrito",
                extra={"event": "landing_maf", "files": len(hits), "detail": str(target)},
            )

    return result


def read_maf_manifest(project: str, settings: Settings | None = None) -> dict:
    import json
    settings = settings or get_settings()
    path = settings.landing_root() / "mutations" / project / "maf_manifest.json"
    if not path.exists():
        raise FileNotFoundError(
            f"No existe el manifiesto MAF en {path}. Ejecuta primero extract-maf-manifest."
        )
    return json.loads(path.read_text(encoding="utf-8"))
