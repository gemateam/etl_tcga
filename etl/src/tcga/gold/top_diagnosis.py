"""G01: top 20 de diagnósticos primarios."""

from __future__ import annotations

from ..config import Settings, get_settings
from ..db import connect
from ..logging_utils import get_logger
from ..ops.runs import pipeline_step, record_dq, recorded_release

log = get_logger(__name__)


def gold_top_20_diagnosis(
    projects: list[str], settings: Settings | None = None
) -> dict[str, int]:
    settings = settings or get_settings()
    release = recorded_release()
    result: dict[str, int] = {}

    for project in projects:
        with pipeline_step("gold_top_20_diagnosis", project) as run:
            with connect() as conn:
                conn.execute(
                    "DELETE FROM gold.top_20_diagnosis WHERE project_id = %s", (project,)
                )
                conn.execute(
                    """
                    WITH d AS (
                        SELECT dx.primary_diagnosis, count(DISTINCT dx.case_id) AS n
                        FROM silver.cases_diagnoses dx
                        JOIN silver.cases c USING (case_id)
                        WHERE c.project_id = %s
                          AND dx.primary_diagnosis IS NOT NULL
                          -- Filtra solo diagnósticos de tejido mamario o sin tejido indicado
                          -- (diagnóstico_is_primary_disease o classification != metástasis/recurrencia)
                          AND (
                            dx.tissue_or_organ_of_origin ILIKE '%%breast%%'
                            OR dx.tissue_or_organ_of_origin IS NULL
                            OR dx.classification_of_tumor IS NULL
                            OR dx.classification_of_tumor NOT IN
                               ('metastasis', 'recurrence', 'Prior primary',
                                'Subsequent Primary', 'Synchronous primary')
                            OR dx.diagnosis_is_primary_disease = true
                          )
                        GROUP BY dx.primary_diagnosis
                    ),
                    t AS (
                        SELECT count(DISTINCT case_id) AS total
                        FROM silver.cases WHERE project_id = %s
                    )
                    INSERT INTO gold.top_20_diagnosis
                        (project_id, diagnosis, cases, pct, diagnosis_rank,
                         gdc_data_release, computed_at)
                    SELECT
                        %s,
                        primary_diagnosis,
                        n,
                        round(100.0 * n / NULLIF(t.total, 0), 2),
                        row_number() OVER (ORDER BY n DESC, primary_diagnosis),
                        %s,
                        now()
                    FROM d CROSS JOIN t
                    ORDER BY n DESC, primary_diagnosis
                    LIMIT 20
                    """,
                    (project, project, project, release),
                )
                rows = conn.execute(
                    "SELECT count(*) AS n FROM gold.top_20_diagnosis WHERE project_id = %s",
                    (project,),
                ).fetchone()["n"]
            run.count(rows_in=rows, rows_out=rows)
            record_dq(
                run.run_id,
                run.step,
                project,
                "top_20_not_empty",
                "critical" if rows == 0 else "warning",
                rows > 0,
                f"filas={rows}",
            )
            result[project] = rows
            log.info("Gold top 20 diagnósticos", extra={"event": "gold_top_20", "rows": rows})
    return result
