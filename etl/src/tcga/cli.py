"""CLI `tcga`: un comando por ETL y un orquestador completo."""

from __future__ import annotations


import typer
from psycopg.errors import UndefinedTable

from .bronze.cases import bronze_cases, bronze_files
from .bronze.expressions import bronze_expressions
from .config import get_settings
from .db import connect, init_schema
from .gold.cx_pivot import gold_cx_pivot
from .gold.extras import (
    gene_stats,
    populate_gene_dim,
    sample_marker_values,
    sample_summary,
    seed_marker_genes,
)
from .gold.top_diagnosis import gold_top_20_diagnosis
from .landing.cases import extract_cases
from .landing.expressions import download_expressions
from .landing.files import extract_files_manifest
from .logging_utils import configure_logging
from .ops.runs import get_gdc_status, record_gdc_status
from .silver.case_expressions import silver_case_expressions
from .silver.clinical import silver_clinical

app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    help="Pipeline medallion de datos abiertos del TCGA (GDC).",
)

LAYERS = {"landing", "bronze", "silver", "gold"}


def _settings():
    settings = get_settings()
    configure_logging(settings.log_level)
    return settings


def _projects(project: list[str] | None) -> list[str]:
    return _settings().resolve_projects(project)


@app.command("init-db")
def init_db() -> None:
    """Crea/actualiza esquemas y tablas (requiere el rol de escritura)."""
    _settings()
    init_schema()
    typer.echo("Esquema inicializado.")


@app.command("extract-cases")
def cmd_extract_cases(
    project: list[str] = typer.Option([], "--project", "-p", help="Proyecto TCGA, repetible."),
) -> None:
    """E01: extrae casos paginados a landing."""
    result = extract_cases(_projects(project))
    typer.echo(f"Casos: {result}")


@app.command("extract-files-manifest")
def cmd_extract_files(
    project: list[str] = typer.Option([], "--project", "-p", help="Proyecto TCGA, repetible."),
) -> None:
    """E02: crea el manifiesto de archivos de expresión."""
    result = extract_files_manifest(_projects(project))
    typer.echo(f"Archivos: {result}")


@app.command("download-expressions")
def cmd_download(
    project: list[str] = typer.Option([], "--project", "-p", help="Proyecto TCGA, repetible."),
    limit: int = typer.Option(None, "--limit", "-n", help="Máximo de archivos a descargar."),
    retry_failed: bool = typer.Option(
        False, "--retry-failed", help="Reintentar archivos marcados como fallidos."
    ),
) -> None:
    """E03: descarga idempotente de TSV de expresión con validación md5."""
    result = download_expressions(_projects(project), limit=limit, retry_failed=retry_failed)
    typer.echo(f"Descargas: {result}")


@app.command("bronze-cases")
def cmd_bronze_cases(
    project: list[str] = typer.Option([], "--project", "-p"),
) -> None:
    """E04: aplana casos JSON a Parquet y JSONB."""
    typer.echo(f"Bronze casos: {bronze_cases(_projects(project))}")


@app.command("bronze-files")
def cmd_bronze_files(
    project: list[str] = typer.Option([], "--project", "-p"),
) -> None:
    """E04: aplana el manifiesto a Parquet."""
    typer.echo(f"Bronze archivos: {bronze_files(_projects(project))}")


@app.command("bronze-expressions")
def cmd_bronze_expressions(
    project: list[str] = typer.Option([], "--project", "-p"),
) -> None:
    """E05: convierte TSV de expresión a Parquet con DuckDB."""
    typer.echo(f"Bronze expresiones: {bronze_expressions(_projects(project))}")


@app.command("silver-case-expressions")
def cmd_silver_expressions(
    project: list[str] = typer.Option([], "--project", "-p"),
) -> None:
    """S01: expresión narrow con case_id y muestra."""
    typer.echo(f"Silver expresiones: {silver_case_expressions(_projects(project))}")


@app.command("silver-clinical")
def cmd_silver_clinical(
    project: list[str] = typer.Option([], "--project", "-p"),
) -> None:
    """S02: tablas clínicas normalizadas."""
    typer.echo(f"Silver clínico: {silver_clinical(_projects(project))}")


@app.command("gold-top-diagnosis")
def cmd_gold_top(
    project: list[str] = typer.Option([], "--project", "-p"),
) -> None:
    """G01: top 20 diagnósticos primarios."""
    typer.echo(f"Gold top 20: {gold_top_20_diagnosis(_projects(project))}")


@app.command("gold-cx-pivot")
def cmd_gold_pivot(
    project: list[str] = typer.Option([], "--project", "-p"),
    top_genes: int = typer.Option(100, "--top-genes"),
    metric: str = typer.Option("fpkm_unstranded", "--metric"),
    log2: bool = typer.Option(False, "--log2"),
    sample_type: str = typer.Option(None, "--sample-type"),
) -> None:
    """G02: varianza por gen y matriz ancha por muestra."""
    n = gold_cx_pivot(_projects(project), top_genes, metric, log2, sample_type)
    typer.echo(f"Gold cx_pivot: {n} muestras")


@app.command("gold-extras")
def cmd_gold_extras(
    project: list[str] = typer.Option([], "--project", "-p"),
) -> None:
    """Tablas gold de apoyo: sample_summary, gene_stats, gene_dim y marcadores."""
    projects = _projects(project)
    typer.echo(f"Sample summary: {sample_summary(projects)}")
    typer.echo(f"Gene stats: {gene_stats(projects)}")
    typer.echo(f"Gene dim: {populate_gene_dim(projects)}")
    typer.echo(f"Marker genes: {seed_marker_genes()}")
    typer.echo(f"Sample marker values: {sample_marker_values(projects)}")


def _run_pipeline(
    projects: list[str],
    layers: str,
    limit: int | None,
    retry_failed: bool,
    top_genes: int,
    metric: str,
    log2: bool,
    sample_type: str | None,
) -> None:
    selected = [part.strip() for part in layers.split(",") if part.strip()]
    unknown = set(selected) - LAYERS
    if unknown:
        raise typer.BadParameter(f"Capas desconocidas: {sorted(unknown)}")
    if not selected:
        raise typer.BadParameter("Debes indicar al menos una capa")

    if "landing" in selected:
        for project in projects:
            extract_cases([project])
        for project in projects:
            extract_files_manifest([project])
        download_expressions(projects, limit=limit, retry_failed=retry_failed)

    if "bronze" in selected:
        for project in projects:
            bronze_cases([project])
        for project in projects:
            bronze_files([project])
        for project in projects:
            bronze_expressions([project])

    if "silver" in selected:
        for project in projects:
            silver_case_expressions([project])
            silver_clinical([project])

    if "gold" in selected:
        for project in projects:
            gold_top_20_diagnosis([project])
            sample_summary([project])
        gene_stats(projects)
        populate_gene_dim(projects)
        seed_marker_genes()
        sample_marker_values(projects)
        gold_cx_pivot(projects, top_genes, metric, log2, sample_type)


@app.command("run")
def cmd_run(
    project: list[str] = typer.Option([], "--project", "-p", help="Proyecto TCGA, repetible."),
    layers: str = typer.Option(
        "landing,bronze,silver,gold", "--layers", help="Capas separadas por comas."
    ),
    limit: int = typer.Option(None, "--limit", help="Límite de descargas para pruebas."),
    retry_failed: bool = typer.Option(
        True, "--retry-failed/--no-retry-failed", help="Reintentar descargas fallidas."
    ),
    top_genes: int = typer.Option(100, "--top-genes"),
    metric: str = typer.Option("fpkm_unstranded", "--metric"),
    log2: bool = typer.Option(False, "--log2"),
    sample_type: str = typer.Option(None, "--sample-type"),
) -> None:
    """Ejecuta el pipeline completo en orden; se detiene ante el primer fallo."""
    _settings()
    projects = _projects(project)
    typer.echo(f"Proyectos={projects} capas={layers} limit={limit}")
    _run_pipeline(projects, layers, limit, retry_failed, top_genes, metric, log2, sample_type)
    typer.echo("Pipeline completado.")


@app.command("status")
def cmd_status() -> None:
    """Muestra el estado de las ejecuciones y los conteos por capa."""
    _settings()
    try:
        with connect() as conn:
            runs = conn.execute(
                """
                SELECT DISTINCT ON (step)
                       step, project_id, status, rows_in, rows_out, gdc_data_release,
                       started_at, finished_at, error
                FROM ops.pipeline_runs
                ORDER BY step, started_at DESC
                """
            ).fetchall()
            release = conn.execute(
                "SELECT data_release, checked_at FROM ops.gdc_status WHERE id = 1"
            ).fetchone()
            downloads = conn.execute(
                "SELECT status, count(*) AS n FROM ops.download_control GROUP BY status"
            ).fetchall()
            counts = {}
            for table in (
                "silver.cases",
                "silver.cases_diagnoses",
                "silver.samples",
                "gold.top_20_diagnosis",
                "gold.gene_stats",
            ):
                counts[table] = conn.execute(f"SELECT count(*) AS n FROM {table}").fetchone()["n"]
    except UndefinedTable:
        typer.echo("La base de datos aún no tiene el esquema. Ejecuta `tcga init-db`.")
        raise typer.Exit(code=1)

    if release:
        typer.echo(f"GDC data release: {release['data_release']} (check {release['checked_at']})")
    typer.echo("Descargas: " + ", ".join(f"{r['status']}={r['n']}" for r in downloads))
    typer.echo("Conteos: " + ", ".join(f"{k}={v}" for k, v in counts.items()))
    typer.echo("")
    typer.echo(f"{'PASO':32} {'PROYECTO':12} {'ESTADO':8} {'SALIDA':>10} {'INICIO':20}")
    for run in runs:
        started = run["started_at"].strftime("%Y-%m-%d %H:%M:%S") if run["started_at"] else ""
        typer.echo(
            f"{run['step']:32} {(run['project_id'] or '-'):12} {run['status']:8} "
            f"{str(run['rows_out'] or 0):>10} {started:20}"
        )
        if run["error"]:
            typer.echo(f"  ERROR: {run['error'][:300]}")


@app.command("check-release")
def cmd_check_release(
    run_if_changed: bool = typer.Option(
        False, "--run-if-changed", help="Lanza el pipeline si cambió data_release."
    ),
) -> None:
    """Compara la versión de datos del GDC y opcionalmente relanza el pipeline."""
    settings = _settings()
    status = get_gdc_status()
    if not status:
        typer.echo("No se pudo consultar /status del GDC.")
        raise typer.Exit(code=1)
    with connect() as conn:
        row = conn.execute("SELECT data_release FROM ops.gdc_status WHERE id = 1").fetchone()
    previous = row["data_release"] if row else None
    current = status.get("data_release")
    record_gdc_status(status)
    typer.echo(f"GDC: {current} (anterior: {previous})")
    if run_if_changed and previous and previous != current:
        typer.echo("Nueva versión detectada; ejecutando pipeline.")
        projects = settings.resolve_projects(None)
        _run_pipeline(projects, "landing,bronze,silver,gold", None, True, 100, "fpkm_unstranded", False, None)


@app.command("web-check")
def cmd_web_check() -> None:
    """Verifica conectividad con PostgreSQL (usado en pruebas)."""
    _settings()
    with connect() as conn:
        conn.execute("SELECT 1")
    typer.echo("PostgreSQL OK")


@app.command("export-gemamx")
def cmd_export_gemamx(
    outdir: str = typer.Option(
        "exports/tcga_brca_hispanic", "--outdir", "-o",
        help="Directorio de destino del bundle del contrato."
    ),
) -> None:
    """Exporta el Contrato de Datos Versionado V3 para GEMAMX (Cáncer de Mama + Hispano/Latino)."""
    from pathlib import Path
    from .export_contract_v3 import (
        build_clinical_attributes,
        export_contract,
        fetch_gdc_ssms,
        get_hispanic_cases_from_db,
        parse_mutations,
    )
    _settings()
    with connect() as conn:
        with conn.cursor() as cur:
            cases = get_hispanic_cases_from_db(cur)
            if not cases:
                typer.echo("No se encontraron casos de cáncer de mama hispanos/latinos en BD.")
                raise typer.Exit(code=1)
            typer.echo(f"Casos seleccionados (TCGA-BRCA Hispano/Latino): {len(cases)}")
            case_codes = [c["case_code"] for c in cases]
            target_set = set(case_codes)

    hits = fetch_gdc_ssms(case_codes)
    mutations = parse_mutations(hits, target_set)
    clinical = build_clinical_attributes(cases)

    export_contract(cases, mutations, clinical, Path(outdir))
    typer.echo(f"Exportación de contrato V3 completada en: {outdir}")


if __name__ == "__main__":  # pragma: no cover
    app()
