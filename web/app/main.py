"""Aplicación web de consulta y reportes (solo lectura)."""

from __future__ import annotations

import csv
import io
import json
import math
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import numpy as np
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from . import analytics, db

BASE_DIR = Path(__file__).resolve().parent
app = FastAPI(title="TCGA Medallion", docs_url=None, redoc_url=None)
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
templates = Jinja2Templates(directory=BASE_DIR / "templates")


def _ctx(request: Request, **kwargs: Any) -> dict[str, Any]:
    return {"request": request, "projects": _projects_available(), **kwargs}


def _projects_available() -> list[str]:
    rows = db.safe_query("SELECT DISTINCT project_id FROM silver.cases ORDER BY 1") or []
    return [r["project_id"] for r in rows]


def _project(request: Request) -> str | None:
    value = request.query_params.get("project")
    available = _projects_available()
    if value in available:
        return value
    return available[0] if available else None


def _fmt(value: Any, digits: int = 3) -> str:
    if value is None:
        return "—"
    if isinstance(value, float):
        if math.isnan(value) or math.isinf(value):
            return "—"
        return f"{value:,.{digits}f}"
    if isinstance(value, int):
        return f"{value:,}"
    return str(value)


def _days_to_years(days: Any) -> str:
    """Convierte días a años (age_at_diagnosis del GDC viene en días)."""
    if days is None:
        return "—"
    try:
        years = int(days) // 365
        return f"{years} años"
    except (TypeError, ValueError):
        return "—"


templates.env.filters["fmt"] = _fmt
templates.env.filters["days_to_years"] = _days_to_years
templates.env.globals["json_dumps"] = lambda v: json.dumps(v, ensure_ascii=False, default=str)
templates.env.filters["tojson"] = lambda v: json.dumps(v)


# --------------------------------------------------------------- estado
@app.get("/health")
def health() -> JSONResponse:
    try:
        db.ping()
    except Exception as exc:  # noqa: BLE001
        return JSONResponse({"status": "unhealthy", "detail": str(exc)}, status_code=503)
    return JSONResponse({"status": "ok"})


@app.get("/", response_class=HTMLResponse)
def status_page(request: Request) -> HTMLResponse:
    projects = _projects_available()
    release = db.safe_query(
        "SELECT data_release, tag, checked_at FROM ops.gdc_status WHERE id = 1"
    )
    runs = db.safe_query(
        """
        SELECT DISTINCT ON (step)
               step, project_id, status, rows_in, rows_out, gdc_data_release,
               started_at, finished_at, error
        FROM ops.pipeline_runs
        ORDER BY step, started_at DESC
        """
    ) or []
    downloads = db.safe_query(
        """
        SELECT COALESCE(project_id, '—') AS project_id, status, count(*) AS n
        FROM ops.download_control GROUP BY 1, 2 ORDER BY 1, 2
        """
    ) or []
    qc = db.safe_query(
        """
        SELECT check_name, severity, passed, details, created_at
        FROM ops.dq_results
        WHERE NOT passed
        ORDER BY created_at DESC LIMIT 15
        """
    ) or []
    counts = {
        "Casos": db.safe_scalar("SELECT count(*) FROM silver.cases"),
        "Diagnósticos": db.safe_scalar("SELECT count(*) FROM silver.cases_diagnoses"),
        "Muestras": db.safe_scalar("SELECT count(*) FROM silver.samples"),
        "Archivos de expresión": db.safe_scalar(
            "SELECT count(*) FROM ops.download_control WHERE status = 'complete'"
        ),
        "Genes con estadísticas": db.safe_scalar(
            "SELECT count(DISTINCT gene_id) FROM gold.gene_stats"
        ),
    }
    exp_stats = analytics.expression_file_stats()
    totals: dict[str, int] = {}
    for row in downloads:
        totals[row["status"]] = totals.get(row["status"], 0) + row["n"]
    total_downloads = sum(totals.values()) or 0
    progress = round(100 * totals.get("complete", 0) / total_downloads) if total_downloads else 0
    return templates.TemplateResponse(
        "status.html",
        _ctx(
            request,
            active="status",
            projects=projects,
            release=release[0] if release else None,
            runs=runs,
            downloads=downloads,
            totals=totals,
            total_downloads=total_downloads,
            progress=progress,
            counts=counts,
            qc=qc,
            exp_stats=exp_stats,
        ),
    )


# ---------------------------------------------------------------- casos
@app.get("/casos", response_class=HTMLResponse)
def cases_page(request: Request) -> HTMLResponse:
    filters = _case_filters(request)
    rows, total, page, pages = _case_rows(filters, request)
    options = _case_options(filters["project"])
    return templates.TemplateResponse(
        "cases.html",
        _ctx(
            request,
            active="cases",
            project=filters["project"],
            filters=filters,
            rows=rows,
            total=total,
            page=page,
            pages=pages,
            options=options,
        ),
    )


@app.get("/casos/tabla", response_class=HTMLResponse)
def cases_table(request: Request) -> HTMLResponse:
    filters = _case_filters(request)
    rows, total, page, pages = _case_rows(filters, request)
    return templates.TemplateResponse(
        "partials/cases_table.html",
        {
            "request": request,
            "project": filters["project"],
            "filters": filters,
            "rows": rows,
            "total": total,
            "page": page,
            "pages": pages,
        },
    )


@app.get("/casos/export.csv")
def cases_export(request: Request) -> StreamingResponse:
    filters = _case_filters(request)
    sql = f"""
        SELECT c.case_id, c.submitter_id, c.project_id, d.sex_at_birth, d.race,
               d.ethnicity, d.vital_status, d.age_at_index, d.days_to_death,
               dx.primary_diagnosis, dx.ajcc_pathologic_stage
        {filters["from"]}
        {filters["where"]}
        ORDER BY c.submitter_id
    """
    rows = db.safe_query(sql, filters["params"]) or []
    return _csv_response(
        "casos_tcga.csv",
        [
            "case_id",
            "submitter_id",
            "project_id",
            "sex_at_birth",
            "race",
            "ethnicity",
            "vital_status",
            "age_at_index",
            "days_to_death",
            "primary_diagnosis",
            "ajcc_pathologic_stage",
        ],
        rows,
    )


def _case_filters(request: Request) -> dict[str, Any]:
    project = _project(request)
    conditions = []
    params: list[Any] = []
    if project:
        conditions.append("c.project_id = %s")
        params.append(project)
    for field, column in (
        ("sexo", "d.sex_at_birth"),
        ("raza", "d.race"),
        ("etnicidad", "d.ethnicity"),
        ("vital", "d.vital_status"),
        ("diagnostico", "dx.primary_diagnosis"),
        ("estadio", "dx.ajcc_pathologic_stage"),
    ):
        value = request.query_params.get(field)
        if value:
            conditions.append(f"{column} = %s")
            params.append(value)
    where = "WHERE " + " AND ".join(conditions) if conditions else ""
    base_from = """
        FROM silver.cases c
        LEFT JOIN silver.cases_demographics d USING (case_id)
        LEFT JOIN LATERAL (
            SELECT primary_diagnosis, ajcc_pathologic_stage
            FROM silver.cases_diagnoses x
            WHERE x.case_id = c.case_id
            ORDER BY diagnosis_is_primary_disease DESC NULLS LAST, diagnosis_id
            LIMIT 1
        ) dx ON TRUE
    """
    return {
        "project": project,
        "sexo": request.query_params.get("sexo", ""),
        "raza": request.query_params.get("raza", ""),
        "etnicidad": request.query_params.get("etnicidad", ""),
        "vital": request.query_params.get("vital", ""),
        "diagnostico": request.query_params.get("diagnostico", ""),
        "estadio": request.query_params.get("estadio", ""),
        "where": where,
        "params": params,
        "from": base_from,
        "base_qs": urlencode(
            {
                key: value
                for key, value in request.query_params.items()
                if key != "page" and value
            }
        ),
    }


def _case_rows(filters: dict[str, Any], request: Request) -> tuple[list[dict], int, int, int]:
    try:
        page = max(1, int(request.query_params.get("page", "1")))
    except ValueError:
        page = 1
    per_page = 25
    count_sql = f"SELECT count(*) AS n {filters['from']} {filters['where']}"
    total_row = db.safe_query(count_sql, filters["params"])
    total = total_row[0]["n"] if total_row else 0
    pages = max(1, math.ceil(total / per_page))
    page = min(page, pages)
    sql = f"""
        SELECT c.case_id, c.submitter_id, c.project_id, d.sex_at_birth, d.race,
               d.vital_status, d.age_at_index, dx.primary_diagnosis,
               dx.ajcc_pathologic_stage
        {filters['from']}
        {filters['where']}
        ORDER BY c.submitter_id
        LIMIT %s OFFSET %s
    """
    rows = db.safe_query(sql, filters["params"] + [per_page, (page - 1) * per_page]) or []
    return rows, total, page, pages


def _case_options(project: str | None) -> dict[str, list[str]]:
    def demographic(column: str) -> list[str]:
        sql = (
            f"SELECT DISTINCT d.{column} AS v FROM silver.cases_demographics d "
            "JOIN silver.cases c USING (case_id) "
            f"WHERE d.{column} IS NOT NULL"
        )
        params: list[Any] = []
        if project:
            sql += " AND c.project_id = %s"
            params.append(project)
        sql += " ORDER BY 1"
        return [r["v"] for r in (db.safe_query(sql, params) or [])]

    def diagnosis(column: str) -> list[str]:
        sql = (
            f"SELECT DISTINCT dx.{column} AS v FROM silver.cases_diagnoses dx "
            "JOIN silver.cases c USING (case_id) "
            f"WHERE dx.{column} IS NOT NULL"
        )
        params: list[Any] = []
        if project:
            sql += " AND c.project_id = %s"
            params.append(project)
        sql += " ORDER BY 1"
        return [r["v"] for r in (db.safe_query(sql, params) or [])]

    return {
        "sexo": demographic("sex_at_birth"),
        "raza": demographic("race"),
        "etnicidad": demographic("ethnicity"),
        "vital": demographic("vital_status"),
        "diagnostico": diagnosis("primary_diagnosis"),
        "estadio": diagnosis("ajcc_pathologic_stage"),
    }


@app.get("/casos/{case_id}", response_class=HTMLResponse)
def case_detail(request: Request, case_id: str) -> HTMLResponse:
    case = db.safe_query(
        """
        SELECT c.*, d.demographic_id, d.sex_at_birth, d.race, d.ethnicity,
               d.vital_status, d.days_to_death, d.days_to_birth, d.age_at_index,
               d.year_of_birth, d.year_of_death, d.cause_of_death, d.marital_status,
               d.education_level, d.country_of_residence_at_enrollment
        FROM silver.cases c
        LEFT JOIN silver.cases_demographics d USING (case_id)
        WHERE c.case_id = %s
        """,
        [case_id],
    )
    if not case:
        return templates.TemplateResponse(
            "no_data.html",
            _ctx(request, active="cases", title="Caso no encontrado",
                 message="No existe ese caso en la capa silver."),
            status_code=404,
        )
    diagnoses = db.safe_query(
        "SELECT * FROM silver.cases_diagnoses WHERE case_id = %s ORDER BY diagnosis_id",
        [case_id],
    ) or []
    exposures = db.safe_query(
        "SELECT * FROM silver.cases_exposures WHERE case_id = %s ORDER BY exposure_id",
        [case_id],
    ) or []
    samples = db.safe_query(
        "SELECT * FROM silver.samples WHERE case_id = %s ORDER BY sample_id", [case_id]
    ) or []
    files = db.safe_query(
        "SELECT * FROM silver.files WHERE case_id = %s ORDER BY file_name", [case_id]
    ) or []
    # Identifica qué diagnóstico corresponde a los archivos de expresión
    # Un diagnóstico "corresponde" si es de tejido mamario o es el diagnóstico primario de la enfermedad
    has_multi_dx = len([d for d in (db.safe_query(
        "SELECT primary_diagnosis FROM silver.cases_diagnoses WHERE case_id = %s", [case_id]
    ) or [])]) > 1
    return templates.TemplateResponse(
        "case_detail.html",
        _ctx(
            request,
            active="cases",
            project=case[0].get("project_id"),
            case=case[0],
            diagnoses=diagnoses,
            exposures=exposures,
            samples=samples,
            files=files,
            has_multi_dx=has_multi_dx,
        ),
    )


@app.get("/casos/{case_id}/pdf", response_class=HTMLResponse)
def case_pdf(request: Request, case_id: str) -> HTMLResponse:
    """Vista de impresión/PDF con todos los datos expandidos (sin HTMX lazy)."""
    case = db.safe_query(
        """
        SELECT c.*, d.demographic_id, d.sex_at_birth, d.race, d.ethnicity,
               d.vital_status, d.days_to_death, d.days_to_birth, d.age_at_index,
               d.year_of_birth, d.year_of_death, d.cause_of_death, d.marital_status,
               d.education_level, d.country_of_residence_at_enrollment
        FROM silver.cases c
        LEFT JOIN silver.cases_demographics d USING (case_id)
        WHERE c.case_id = %s
        """,
        [case_id],
    )
    if not case:
        return HTMLResponse("<p>Caso no encontrado.</p>", status_code=404)
    diagnoses = db.safe_query(
        "SELECT * FROM silver.cases_diagnoses WHERE case_id = %s ORDER BY diagnosis_id",
        [case_id],
    ) or []
    exposures = db.safe_query(
        "SELECT * FROM silver.cases_exposures WHERE case_id = %s ORDER BY exposure_id",
        [case_id],
    ) or []
    samples = db.safe_query(
        "SELECT * FROM silver.samples WHERE case_id = %s ORDER BY sample_id", [case_id]
    ) or []
    files = db.safe_query(
        "SELECT * FROM silver.files WHERE case_id = %s ORDER BY file_name", [case_id]
    ) or []
    # Marcadores: carga completa sin HTMX
    sample_row = db.safe_query(
        """
        SELECT sample_id FROM gold.sample_marker_values
        WHERE case_id = %s
        ORDER BY CASE sample_type WHEN 'Primary Tumor' THEN 0 ELSE 1 END, sample_id
        LIMIT 1
        """,
        [case_id],
    )
    markers: list[dict] = []
    marker_sample_id = None
    if sample_row:
        marker_sample_id = sample_row[0]["sample_id"]
        markers = db.safe_query(
            """
            SELECT smv.gene_name, smv.tpm, smv.pct_rank, smv.category, smv.sample_type,
                   mg.description, mg.note
            FROM gold.sample_marker_values smv
            JOIN gold.marker_genes mg USING (gene_name)
            WHERE smv.sample_id = %s
            ORDER BY mg.gene_name
            """,
            [marker_sample_id],
        ) or []
    # Top genes de expresión del primer archivo
    top_genes: list[dict] = []
    expr_file_id = None
    for f in files:
        if f.get("data_type") == "Gene Expression Quantification":
            expr_file_id = f["file_id"]
            top_genes = analytics.expression_top_genes(expr_file_id, limit=50)
            break

    import datetime
    return templates.TemplateResponse(
        "case_pdf.html",
        {
            "request": request,
            "case": case[0],
            "diagnoses": diagnoses,
            "exposures": exposures,
            "samples": samples,
            "files": files,
            "markers": markers,
            "marker_sample_id": marker_sample_id,
            "top_genes": top_genes,
            "expr_file_id": expr_file_id,
            "project": case[0].get("project_id"),
            "generated_at": datetime.datetime.utcnow().strftime("%d/%m/%Y %H:%M"),
        },
    )


# ------------------------------------------------- expresión por muestra
@app.get("/casos/{case_id}/expresion/marcadores", response_class=HTMLResponse)
def case_markers(request: Request, case_id: str) -> HTMLResponse:
    """HTMX: panel de marcadores clínicos desde gold.sample_marker_values."""
    # Busca el sample_id primario de este caso (Primary Tumor si existe)
    sample = db.safe_query(
        """
        SELECT sample_id FROM gold.sample_marker_values
        WHERE case_id = %s
        ORDER BY CASE sample_type WHEN 'Primary Tumor' THEN 0 ELSE 1 END, sample_id
        LIMIT 1
        """,
        [case_id],
    )
    if not sample:
        return HTMLResponse(
            "<p class='muted small'>Sin datos de expresión de marcadores para este caso.</p>"
        )
    sample_id = sample[0]["sample_id"]
    markers = db.safe_query(
        """
        SELECT smv.gene_name, smv.tpm, smv.pct_rank, smv.category, smv.sample_type,
               mg.description, mg.note
        FROM gold.sample_marker_values smv
        JOIN gold.marker_genes mg USING (gene_name)
        WHERE smv.sample_id = %s
        ORDER BY mg.gene_name
        """,
        [sample_id],
    ) or []
    return templates.TemplateResponse(
        "partials/expression_markers.html",
        {"request": request, "markers": markers, "case_id": case_id,
         "sample_id": sample_id, "from_gold": True},
    )


@app.get("/casos/{case_id}/expresion/tabla", response_class=HTMLResponse)
def case_expression_table(request: Request, case_id: str) -> HTMLResponse:
    """HTMX: top genes de un archivo de expresión, filtrable por tipo."""
    file_id = request.query_params.get("file_id", "")
    gene_type = request.query_params.get("gene_type", "") or None
    limit = min(int(request.query_params.get("limit", "50")), 200)
    if not file_id:
        return HTMLResponse("<p class='muted'>Selecciona un archivo.</p>")
    rows = analytics.expression_top_genes(file_id, limit=limit, gene_type_filter=gene_type)
    gene_types = analytics.expression_gene_types(file_id)
    return templates.TemplateResponse(
        "partials/expression_table.html",
        {
            "request": request,
            "rows": rows,
            "file_id": file_id,
            "case_id": case_id,
            "gene_type": gene_type or "",
            "gene_types": gene_types,
            "limit": limit,
        },
    )


# --------------------------------------------------------------- genes
@app.get("/api/genes")
def api_genes(request: Request) -> JSONResponse:
    q = request.query_params.get("q", "").strip()
    if len(q) < 2:
        return JSONResponse([])
    project = request.query_params.get("project") or _project(request)
    params: list[Any] = [f"%{q}%", f"%{q}%"]
    sql = (
        "SELECT DISTINCT ON (gene_id) gene_id, gene_name, gene_type "
        "FROM gold.gene_dim WHERE (gene_name ILIKE %s OR gene_id ILIKE %s)"
    )
    if project:
        sql += " AND project_id = %s"
        params.append(project)
    sql += " ORDER BY gene_id LIMIT 20"
    rows = db.safe_query(sql, params) or []
    return JSONResponse(rows)


@app.get("/genes", response_class=HTMLResponse)
def gene_page(request: Request) -> HTMLResponse:
    gene_q = request.query_params.get("gene", "").strip()
    metric = request.query_params.get("metric", "fpkm_unstranded")
    if metric not in ("fpkm_unstranded", "tpm_unstranded"):
        metric = "fpkm_unstranded"
    log2 = request.query_params.get("log2", "1") != "0"
    project = _project(request)
    gene = None
    stats: list[dict] = []
    percentiles: list[dict] = []
    values: list[dict] = []
    box_type: list[dict] = []
    box_stage: list[dict] = []
    n_by_group: dict[str, int] = {}

    if gene_q:
        params: list[Any] = [gene_q, gene_q]
        sql = (
            "SELECT gene_id, gene_name, gene_type FROM gold.gene_dim "
            "WHERE (gene_id = %s OR lower(gene_name) = lower(%s))"
        )
        if project:
            sql += " AND project_id = %s"
            params.append(project)
        sql += " ORDER BY gene_id LIMIT 1"
        rows = db.safe_query(sql, params) or []
        if rows:
            gene = rows[0]
            # Estadísticas precalculadas (media, std, varianza, n)
            stats = db.safe_query(
                """
                SELECT sample_type, metric, n_samples, n_expressed, mean, std, variance
                FROM gold.gene_stats
                WHERE gene_id = %s AND metric = %s
                """
                + (" AND project_id = %s" if project else "")
                + " ORDER BY sample_type",
                [gene["gene_id"], metric] + ([project] if project else []),
            ) or []
            # Percentiles on-demand desde el Parquet silver (solo ~1200 valores)
            percentiles = analytics.gene_percentiles(
                gene["gene_id"], metric=metric,
                projects=[project] if project else None,
            )
            # Valores individuales para boxplots
            values = analytics.expression_values(
                gene["gene_id"], [project] if project else None
            )
            stages = _stage_map(project)
            for item in values:
                item["stage"] = _stage_group(stages.get(item["case_id"], "Sin dato"))
            box_type = _box_payload(values, "sample_type", metric_key=metric, log2=log2)
            box_stage = _box_payload(values, "stage", metric_key=metric, log2=log2)
            # n por grupo para anotaciones
            for grp in box_type:
                n_by_group[grp["name"]] = len(grp["y"])

    return templates.TemplateResponse(
        "gene.html",
        _ctx(
            request,
            active="genes",
            project=project,
            gene_q=gene_q,
            gene=gene,
            stats=stats,
            percentiles=percentiles,
            metric=metric,
            log2=log2,
            n_values=len(values),
            box_type=json.dumps(box_type, default=str),
            box_stage=json.dumps(box_stage, default=str),
            n_by_group=json.dumps(n_by_group, default=str),
        ),
    )


def _stage_group(stage: str) -> str:
    """Agrupa estadios detallados en I, II, III, IV y Sin dato."""
    import re
    if not stage or stage == "Sin dato":
        return "Sin dato"
    m = re.search(r"\b(I{1,3}V?|IV)\b", stage.upper())
    if not m:
        return "Sin dato"
    roman = m.group(1)
    if roman in ("I",):
        return "Estadio I"
    if roman in ("II",):
        return "Estadio II"
    if roman in ("III",):
        return "Estadio III"
    if roman in ("IV",):
        return "Estadio IV"
    return stage


def _stage_map(project: str | None) -> dict[str, str]:
    rows = db.safe_query(
        """
        SELECT DISTINCT ON (dx.case_id) dx.case_id,
               COALESCE(NULLIF(dx.ajcc_pathologic_stage, ''), 'Sin dato') AS stage
        FROM silver.cases_diagnoses dx
        JOIN silver.cases c USING (case_id)
        """
        + (" WHERE c.project_id = %s" if project else "")
        + " ORDER BY dx.case_id, dx.diagnosis_is_primary_disease DESC NULLS LAST, dx.diagnosis_id",
        [project] if project else [],
    ) or []
    return {r["case_id"]: r["stage"] for r in rows}


def _box_payload(
    values: list[dict],
    key: str,
    metric_key: str = "fpkm_unstranded",
    log2: bool = True,
) -> list[dict]:
    import math
    groups: dict[str, list[float]] = {}
    for item in values:
        val = item.get(metric_key)
        if val is None:
            continue
        if log2:
            val = math.log2(float(val) + 1)
        groups.setdefault(str(item.get(key) or "Sin dato"), []).append(float(val))
    return [
        {"name": name, "y": pts, "type": "box", "boxpoints": "outliers"}
        for name, pts in sorted(groups.items())
        if len(pts) >= 5  # oculta grupos con < 5 muestras
    ]


@app.get("/genes/export.csv")
def gene_export(request: Request) -> StreamingResponse:
    gene_q = request.query_params.get("gene", "").strip()
    project = _project(request)
    params: list[Any] = [gene_q, gene_q]
    sql = (
        "SELECT gene_id, gene_name, gene_type FROM gold.gene_dim "
        "WHERE (gene_id = %s OR lower(gene_name) = lower(%s))"
    )
    if project:
        sql += " AND project_id = %s"
        params.append(project)
    sql += " LIMIT 1"
    gene = (db.safe_query(sql, params) or [None])[0]
    rows: list[dict] = []
    if gene:
        rows = db.safe_query(
            """
            SELECT project_id, gene_id, gene_name, gene_type, sample_type,
                   n_samples, mean, variance, n_gt_zero
            FROM gold.gene_stats WHERE gene_id = %s
            """
            + (" AND project_id = %s" if project else "")
            + " ORDER BY project_id, sample_type",
            [gene["gene_id"]] + ([project] if project else []),
        ) or []
    return _csv_response(
        "estadisticas_gen.csv",
        [
            "project_id",
            "gene_id",
            "gene_name",
            "gene_type",
            "sample_type",
            "n_samples",
            "mean",
            "variance",
            "n_gt_zero",
        ],
        rows,
    )


# ------------------------------------------------------------- reportes
@app.get("/reportes", response_class=HTMLResponse)
def reports_page(request: Request) -> HTMLResponse:
    project = _project(request)
    top = db.safe_query(
        "SELECT diagnosis, cases, pct, diagnosis_rank FROM gold.top_20_diagnosis "
        + ("WHERE project_id = %s " if project else "")
        + "ORDER BY diagnosis_rank LIMIT 20",
        [project] if project else [],
    ) or []
    demo = _demographics(project)
    sample_types = _sample_types(project)
    pivot = _pivot(project)
    km = _kaplan_meier(project)
    release = (db.safe_query(
        "SELECT data_release, tag, checked_at FROM ops.gdc_status WHERE id = 1"
    ) or [None])[0]
    last_run = (db.safe_query(
        "SELECT max(finished_at) AS finished_at FROM ops.pipeline_runs"
    ) or [{"finished_at": None}])[0]["finished_at"]

    top_json = json.dumps(
        [
            {
                "diagnosis": row["diagnosis"],
                "cases": int(row["cases"]),
                "pct": float(row["pct"]) if row["pct"] is not None else 0.0,
            }
            for row in top
        ]
    )
    demo_json = json.dumps(
        {
            key: [{"label": row["label"], "n": int(row["n"])} for row in items]
            for key, items in demo.items()
        }
    )
    sample_json = json.dumps(
        [
            {
                "sample_type": row["sample_type"],
                "cases": int(row["cases"]),
                "samples": int(row["samples"]),
            }
            for row in sample_types
        ]
    )
    sample_group_json = json.dumps(
        [
            {"label": label, "samples": int(n)}
            for label, n in _sample_groups(sample_types).items()
        ]
    )
    return templates.TemplateResponse(
        "reports.html",
        _ctx(
            request,
            active="reports",
            project=project,
            top=top,
            demo=demo,
            sample_types=sample_types,
            pivot=pivot,
            km=km,
            release=release,
            last_run=last_run,
            top_json=top_json,
            demo_json=demo_json,
            sample_json=sample_json,
            sample_group_json=sample_group_json,
        ),
    )


def _sample_groups(rows: list[dict]) -> dict[str, int]:
    groups = {"Tumor": 0, "Normal": 0, "Otros": 0}
    for row in rows:
        label = str(row.get("sample_type") or "").lower()
        if "tumor" in label or "carcinoma" in label:
            key = "Tumor"
        elif "normal" in label:
            key = "Normal"
        else:
            key = "Otros"
        groups[key] += int(row.get("samples") or 0)
    return {key: value for key, value in groups.items() if value}


def _demographics(project: str | None) -> dict[str, list[dict]]:
    where = "WHERE c.project_id = %s" if project else ""
    params = [project] if project else []
    result: dict[str, list[dict]] = {}
    for name, column in (
        ("sexo", "d.sex_at_birth"),
        ("raza", "d.race"),
        ("etnicidad", "d.ethnicity"),
        ("vital", "d.vital_status"),
    ):
        result[name] = db.safe_query(
            f"""
            SELECT COALESCE(NULLIF({column}, ''), 'Sin dato') AS label, count(*) AS n
            FROM silver.cases c LEFT JOIN silver.cases_demographics d USING (case_id)
            {where}
            GROUP BY 1 ORDER BY n DESC
            """,
            params,
        ) or []
    return result


def _sample_types(project: str | None) -> list[dict]:
    return db.safe_query(
        """
        SELECT COALESCE(NULLIF(s.sample_type, ''), 'Sin dato') AS sample_type,
               count(DISTINCT s.case_id) AS cases,
               count(DISTINCT s.sample_id) AS samples
        FROM silver.samples s JOIN silver.cases c USING (case_id)
        """
        + ("WHERE c.project_id = %s " if project else "")
        + "GROUP BY 1 ORDER BY samples DESC",
        [project] if project else [],
    ) or []


def _pivot(project: str | None) -> dict[str, Any] | None:
    rows = db.safe_query(
        "SELECT * FROM gold.cx_pivot " + ("WHERE project_id = %s" if project else ""),
        [project] if project else [],
    )
    if not rows:
        return None
    columns = list(rows[0].keys())
    base = ["sample_id", "project_id", "case_id", "sample_type", "gdc_data_release", "computed_at"]
    genes = [c for c in columns if c not in base]
    matrix = np.array([[float(r.get(g) or 0.0) for g in genes] for r in rows], dtype=float)
    matrix[~np.isfinite(matrix)] = np.nan
    col_means = np.nanmean(matrix, axis=0)
    col_means = np.where(np.isfinite(col_means), col_means, 0.0)
    filled = np.where(np.isnan(matrix), col_means, matrix)
    centered = filled - filled.mean(axis=0)
    std = centered.std(axis=0)
    std[std == 0] = 1
    scaled = centered / std
    n_components = min(2, scaled.shape[0], scaled.shape[1])
    if n_components == 2:
        u, s, _ = np.linalg.svd(scaled, full_matrices=False)
        coords = u[:, :2] * s[:2]
    else:
        coords = np.zeros((scaled.shape[0], 2))
    pca = [
        {
            "x": float(coords[i, 0]),
            "y": float(coords[i, 1]),
            "sample_type": rows[i].get("sample_type") or "Sin dato",
            "sample_id": rows[i].get("sample_id"),
        }
        for i in range(len(rows))
    ]
    heat_rows = sorted(rows, key=lambda r: (str(r.get("sample_type")), str(r.get("sample_id"))))[:120]
    heat_genes = genes[:60]
    z = [
        [round(float(math.log1p(max(_num(r.get(g)) or 0.0, 0.0))), 4) for g in heat_genes]
        for r in heat_rows
    ]
    return {
        "sample_count": len(rows),
        "gene_count": len(genes),
        "genes": heat_genes,
        "samples": [r.get("sample_id") for r in heat_rows],
        "sample_types": [r.get("sample_type") or "Sin dato" for r in heat_rows],
        "z": z,
        "pca": pca,
        "json": json.dumps(
            {
                "heatmap": {
                    "z": z,
                    "x": heat_genes,
                    "y": [r.get("sample_id") for r in heat_rows],
                    "sample_types": [r.get("sample_type") or "Sin dato" for r in heat_rows],
                },
                "pca": pca,
            },
            default=str,
        ),
    }


def _num(value: Any, default: float | None = None) -> float | None:
    try:
        out = float(value)
        return out if math.isfinite(out) else default
    except (TypeError, ValueError):
        return default


def _kaplan_meier(project: str | None) -> dict[str, Any] | None:
    rows = db.safe_query(
        """
        SELECT d.vital_status, d.days_to_death, dx.days_to_last_follow_up,
               COALESCE(NULLIF(dx.ajcc_pathologic_stage, ''), 'Sin dato') AS stage
        FROM silver.cases c
        JOIN silver.cases_demographics d USING (case_id)
        LEFT JOIN LATERAL (
            SELECT ajcc_pathologic_stage, days_to_last_follow_up
            FROM silver.cases_diagnoses x WHERE x.case_id = c.case_id
            ORDER BY diagnosis_is_primary_disease DESC NULLS LAST, diagnosis_id LIMIT 1
        ) dx ON TRUE
        """
        + ("WHERE c.project_id = %s" if project else ""),
        [project] if project else [],
    ) or []
    groups: dict[str, list[tuple[float, int]]] = {}
    for row in rows:
        dead = str(row.get("vital_status") or "").lower().startswith("dead")
        time = row.get("days_to_death") if dead else row.get("days_to_last_follow_up")
        if time is None:
            continue
        groups.setdefault(row["stage"], []).append((max(float(time), 0.0), 1 if dead else 0))
    if not groups:
        return None
    traces = []
    for stage, items in sorted(groups.items()):
        items.sort()
        n = len(items)
        survival = 1.0
        xs = [0.0]
        ys = [1.0]
        at_risk = n
        for i, (time, event) in enumerate(items):
            if i > 0 and time != items[i - 1][0]:
                xs.append(time)
                ys.append(survival)
            if event:
                survival *= (at_risk - 1) / at_risk if at_risk > 0 else 0
                xs.append(time)
                ys.append(survival)
            at_risk -= 1
        traces.append({"name": stage, "x": xs, "y": ys, "type": "scatter", "mode": "lines"})
    return {"traces": traces, "json": json.dumps(traces, default=str)}


@app.get("/reportes/export/{report}.csv")
def report_export(request: Request, report: str) -> StreamingResponse:
    project = _project(request)
    if report == "r1":
        rows = db.safe_query(
            "SELECT diagnosis_rank, diagnosis, cases, pct FROM gold.top_20_diagnosis "
            + ("WHERE project_id = %s " if project else "")
            + "ORDER BY diagnosis_rank",
            [project] if project else [],
        ) or []
        return _csv_response("r1_diagnosticos.csv", list(rows[0].keys()) if rows else ["diagnosis"], rows)
    if report == "r2":
        rows = []
        for name, items in _demographics(project).items():
            for item in items:
                rows.append({"categoria": name, "valor": item["label"], "casos": item["n"]})
        return _csv_response("r2_demografia.csv", ["categoria", "valor", "casos"], rows)
    if report == "r3":
        rows = _sample_types(project)
        return _csv_response("r3_muestras.csv", ["sample_type", "cases", "samples"], rows)
    if report == "r4":
        rows = db.safe_query(
            "SELECT * FROM gold.cx_pivot " + ("WHERE project_id = %s " if project else "")
            + "ORDER BY sample_id",
            [project] if project else [],
        ) or []
        columns = list(rows[0].keys()) if rows else ["sample_id"]
        return _csv_response("r4_genes_mas_variables.csv", columns, rows)
    return _csv_response("reporte.csv", ["error"], [{"error": "reporte desconocido"}])


def _csv_response(filename: str, columns: list[str], rows: list[dict]) -> StreamingResponse:
    buffer = io.StringIO()
    buffer.write("\ufeff")
    writer = csv.DictWriter(buffer, fieldnames=columns, extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        writer.writerow({key: _csv_value(row.get(key)) for key in columns})
    return StreamingResponse(
        iter([buffer.getvalue()]),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def _csv_value(value: Any) -> Any:
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, default=str)
    return value
