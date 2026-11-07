"""E01: extracción paginada de casos a landing."""

from __future__ import annotations

from pathlib import Path

from ..common import write_json
from ..config import Settings, get_settings
from ..gdc import GDCClient
from ..logging_utils import get_logger
from ..ops.runs import pipeline_step, record_dq

log = get_logger(__name__)


def extract_cases(projects: list[str], settings: Settings | None = None) -> dict[str, int]:
    settings = settings or get_settings()
    settings.ensure_dirs()
    client = GDCClient(settings)
    result: dict[str, int] = {}

    for project in projects:
        with pipeline_step("extract_cases", project) as run:
            target = settings.landing_cases_dir(project)
            target.mkdir(parents=True, exist_ok=True)
            total = 0
            api_total = None
            pages = 0
            for page, payload in client.case_pages(project):
                write_json(target / f"page_{page}.json", payload)
                total += len(payload.get("data", {}).get("hits", []))
                api_total = payload.get("data", {}).get("pagination", {}).get("total", api_total)
                pages = page
            for stale in target.glob("page_*.json"):
                try:
                    number = int(stale.stem.split("_", 1)[1])
                except (IndexError, ValueError):
                    continue
                if number > pages:
                    stale.unlink()
            run.count(rows_in=api_total or total, rows_out=total)
            record_dq(
                run.run_id,
                run.step,
                project,
                "cases_extracted",
                "critical",
                total > 0,
                f"casos={total} api_total={api_total}",
            )
            record_dq(
                run.run_id,
                run.step,
                project,
                "cases_match_api_total",
                "warning",
                api_total is None or total == api_total,
                f"casos={total} api_total={api_total}",
            )
            result[project] = total
            log.info(
                "Casos escritos en landing",
                extra={"event": "landing_cases", "rows": total, "detail": str(target)},
            )
    return result


def count_landing_cases(project: str, settings: Settings | None = None) -> int:
    settings = settings or get_settings()
    total = 0
    for _ in (settings.landing_cases_dir(project)).glob("page_*.json"):
        total += 1
    return total


def landing_case_files(project: str, settings: Settings | None = None) -> list[Path]:
    settings = settings or get_settings()
    return sorted(settings.landing_cases_dir(project).glob("page_*.json"))
