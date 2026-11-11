"""CLI `tcga`: un comando por ETL y un orquestador completo."""

from __future__ import annotations

import typer
from psycopg.errors import UndefinedTable

from .bronze.cases import bronze_cases, bronze_files
from .bronze.expressions import bronze_expressions
from .bronze.mutations import bronze_mutations
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
from .landing.mutations import extract_maf_manifest
from .logging_utils import configure_logging
from .ops.runs import get_gdc_status, record_gdc_status
from .silver.case_expressions import silver_case_expressions
from .silver.clinical import silver_clinical
from .silver.mutations import silver_mutations

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
def cmd_bronze_expressions(project: list[str] = typer.Option([], "--project", "-p")) -> None:
    """E05: convierte TSV de expresión a Parquet con DuckDB."""
    typer.echo(f"Bronze expresiones: {bronze_expressions(_projects(project))}")


@app.command("silver-case-expressions")
def cmd_silver_expressions(project: list[str] = typer.Option([], "--project", "-p")) -> None:
    """S01: expresión narrow con case_id y muestra."""
    typer.echo(f"Silver expresiones: {silver_case_expressions(_projects(project))}")


@app.command("extract-maf-manifest")
def cmd_extract_maf(project: list[str] = typer.Option([], "--project", "-p")) -> None:
    """L03: descarga el manifiesto de archivos MAF (mutaciones somáticas)."""
    typer.echo(f"Archivos MAF: {extract_maf_manifest(_projects(project))}")


@app.command("bronze-mutations")
def cmd_bronze_mutations(project: list[str] = typer.Option([], "--project", "-p")) -> None:
    """B03: descarga y parsea MAFs a Parquet + bronze.mutations."""
    typer.echo(f"Bronze mutaciones: {bronze_mutations(_projects(project))}")


@app.command("silver-mutations")
def cmd_silver_mutations(project: list[str] = typer.Option([], "--project", "-p")) -> None:
    """S03: mutaciones normalizadas en silver.mutations (solo PASS, deduplicadas)."""
    typer.echo(f"Silver mutaciones: {silver_mutations(_projects(project))}")


@app.command("silver-clinical")
def cmd_silver_clinical(project: list[str] = typer.Option([], "--project", "-p")) -> None:
    """S02: tablas clínicas normalizadas."""
    typer.echo(f"Silver clínico: {silver_clinical(_projects(project))}")


@app.command("gold-top-diagnosis")
def cmd_gold_top(project: list[str] = typer.Option([], "--project", "-p")) -> None:
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
def cmd_gold_extras(project: list[str] = typer.Option([], "--project", "-p")) -> None:
    """Tablas gold de apoyo: sample_summary, gene_stats, gene_dim y marcadores."""
    projects = _projects(project)
    typer.echo(f"Sample summary: {sample_summary(projects)}")
    typer.echo(f"Gene stats: {gene_stats(projects)}")
    typer.echo(f"Gene dim: {populate_gene_dim(projects)}")
    typer.echo(f"Marker genes: {seed_marker_genes()}")
    typer.echo(f"Sample marker values: {sample_marker_values(projects)}")


def _run_pipeline(
    projects: list[str], layers: str, limit: int | None, retry_failed: bool,
    top_genes: int, metric: str, log2: bool, sample_type: str | None,
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
            extract_files_manifest([project])
            extract_maf_manifest([project])
        download_expressions(projects, limit=limit, retry_failed=retry_failed)

    if "bronze" in selected:
        for project in projects:
            bronze_cases([project])
            bronze_files([project])
            bronze_expressions([project])
            bronze_mutations([project])

    if "silver" in selected:
        for project in projects:
            silver_case_expressions([project])
            silver_clinical([project])
            silver_mutations([project])

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
    data_dir: str = typer.Option(
        "/data", "--data-dir",
        help="Raíz del medallón (debe contener bronze/cases y bronze/mutations)."
    ),
    project: str = typer.Option("TCGA-BRCA", "--project", "-p"),
    ethnicity: str = typer.Option(
        "HISPANIC OR LATINO", "--ethnicity",
        help="Etnia autorreportada a incluir."
    ),
    scope: str = typer.Option(
        "target_panel", "--scope",
        help="target_panel (11 genes) o all_missense."
    ),
    offline: bool = typer.Option(
        False, "--offline", help="No consultar /status del GDC."
    ),
    allow_curated: bool = typer.Option(
        False, "--allow-curated",
        help="Permitir bundles curados (solo smoke tests)."
    ),
) -> None:
    """Exporta el Contrato de Datos V3 (TCGA-BRCA + Hispano/Latino) desde el medallón."""
    from pathlib import Path

    from .export_contract_v3 import export_bundle

    _settings()
    result = export_bundle(
        data_dir=Path(data_dir),
        outdir=Path(outdir),
        project=project,
        ethnicity=ethnicity,
        scope=scope,
        offline=offline,
        require_science=not allow_curated,
    )
    manifest = result["manifest"]
    typer.echo(f"Bundle: {result['path']}")
    typer.echo(f"  contract_version: {manifest['contract_version']}")
    typer.echo(f"  gdc_data_release: {manifest['gdc_data_release']}")
    typer.echo(f"  casos: {manifest['counts']['cohort_cases']}")
    typer.echo(f"  mutaciones: {manifest['counts']['mutation_records']}")
    typer.echo(f"  portadoras: {manifest['counts']['mutation_patients']}")
    typer.echo(f"  clínica: {manifest['counts']['clinical_records']}")
    if result["errors"]:
        for error in result["errors"]:
            typer.echo(f"  ERROR: {error}", err=True)
        raise typer.Exit(code=1)
    typer.echo("  validación: OK")


@app.command("case-report")
def cmd_case_report(
    case: str = typer.Argument(..., help="submitter_id (p. ej. TCGA-5L-AAT1) o UUID"),
    project: str = typer.Option("TCGA-BRCA", "--project", "-p"),
    legacy_case_id: list[str] = typer.Option([], "--legacy-case-id", help="UUID anterior para redirigir una ruta local al submitter actual; repetible."),
    outdir: str | None = typer.Option(None, "--out", "-o", help="Directorio de reportes (por defecto DATA_DIR/case_reports)."),
    percentiles: bool = typer.Option(False, "--percentiles", help="Descargar cohortes para percentiles (incluye BRCA hispana/lobulillar)."),
    max_cohort_files: int = typer.Option(0, "--max-cohort-files", min=0, help="Límite por cohorte; 0 procesa todos los archivos."),
    workers: int = typer.Option(4, "--workers", min=1, max=4),
    cdr: str | None = typer.Option(None, help="Tabla TCGA-CDR local (CSV/TSV/XLSX)."),
    pam50: str | None = typer.Option(None, help="Tabla PAM50 local (CSV/TSV/XLSX)."),
    panc: str | None = typer.Option(None, help="Tabla PanCanAtlas local (CSV/TSV/XLSX)."),
    id_col: str = typer.Option("bcr_patient_barcode", "--id-col"),
    prioritize_hispanic: bool = typer.Option(True, "--prioritize-hispanic/--no-prioritize-hispanic"),
    refresh: bool = typer.Option(False, "--refresh", help="Ignora el reporte en caché y vuelve a consultar el caso."),
) -> None:
    """Genera un reporte clínico/molecular GDC; prioriza Hispanic or Latino al resolver IDs ambiguos."""
    from pathlib import Path

    from .case_report import generate_case_report

    settings = _settings()
    report = generate_case_report(
        case,
        settings=settings,
        out_dir=Path(outdir) if outdir else settings.data_dir / "case_reports",
        percentiles=percentiles,
        max_cohort_files=max_cohort_files,
        workers=workers,
        cdr=Path(cdr) if cdr else None,
        pam50=Path(pam50) if pam50 else None,
        panc=Path(panc) if panc else None,
        id_col=id_col,
        prioritize_hispanic=prioritize_hispanic,
        refresh=refresh,
        project=project,
        legacy_case_ids=legacy_case_id,
    )
    typer.echo(f"Reporte: {report['report_path']}")
    typer.echo(f"Caso: {report['identity']['submitter_id']} · etnicidad: {report['identity']['ethnicity'] or 'sin dato'}")
    typer.echo(f"Prioridad hispana/latina: {'sí' if report['identity']['hispanic_latino_priority'] else 'no'}")
    typer.echo(f"GDC: {report['metadata']['gdc_data_release'] or 'versión no disponible'}")
    typer.echo(f"Mutaciones driver: {len(report['mutations'])} · archivos inventariados: {len(report['files_inventory']['files'])}")
    if report["warnings"]:
        typer.echo(f"Advertencias/capas no disponibles: {len(report['warnings'])}")


@app.command("case-report-batch")
def cmd_case_report_batch(
    project: str = typer.Option("TCGA-BRCA", "--project", "-p"),
    outdir: str | None = typer.Option(None, "--out", "-o", help="Directorio de reportes (por defecto DATA_DIR/case_reports)."),
    limit: int = typer.Option(0, "--limit", min=0, help="Máximo de casos; 0 procesa toda la cohorte."),
    percentiles: bool = typer.Option(True, "--percentiles/--no-percentiles", help="Calcular percentiles completos de expresión (activado por defecto)."),
    max_cohort_files: int = typer.Option(0, "--max-cohort-files", min=0, help="Límite por cohorte para percentiles; 0 procesa todos los archivos."),
    workers: int = typer.Option(1, "--workers", min=1, max=4),
    cdr: str | None = typer.Option(None, help="Tabla TCGA-CDR local (CSV/TSV/XLSX)."),
    pam50: str | None = typer.Option(None, help="Tabla PAM50 local (CSV/TSV/XLSX)."),
    panc: str | None = typer.Option(None, help="Tabla PanCanAtlas local (CSV/TSV/XLSX)."),
    id_col: str = typer.Option("bcr_patient_barcode", "--id-col"),
    refresh: bool = typer.Option(False, "--refresh", help="Ignora reportes en caché y vuelve a consultar los casos."),
) -> None:
    """Genera en lote reportes complementarios de casos Hispanic or Latino."""
    from pathlib import Path

    from .case_report import generate_case_report_batch

    settings = _settings()
    result = generate_case_report_batch(
        settings=settings,
        project=project,
        out_dir=Path(outdir) if outdir else None,
        percentiles=percentiles,
        max_cohort_files=max_cohort_files,
        workers=workers,
        max_cases=limit,
        cdr=Path(cdr) if cdr else None,
        pam50=Path(pam50) if pam50 else None,
        panc=Path(panc) if panc else None,
        id_col=id_col,
        refresh=refresh,
    )
    typer.echo(f"Proyecto: {result['project']}")
    typer.echo(f"Casos hispanos/latinos encontrados: {result['cases_found']}")
    typer.echo(f"Reportes procesados: {result['reports_processed']}")
    typer.echo(f"Directorio: {result['out_dir']}")
    for failure in result["failures"]:
        typer.echo(
            f"ERROR: {failure['submitter_id']} ({failure['error']})",
            err=True,
        )
    if result["failures"]:
        raise typer.Exit(code=1)


if __name__ == "__main__":  # pragma: no cover
    app()
