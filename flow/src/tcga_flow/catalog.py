"""Catálogo cerrado de nodos del ejecutor visual.

Cada nodo declara su capa, puertos, parámetros validados y la función del
paquete `tcga` que ejecuta. La UI nunca puede invocar comandos ni código
arbitrario: solo los tipos registrados aquí.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Literal

from pydantic import BaseModel, Field

from .schemas import Layer, OutputKind, OutputSpec, Port, PortKind
from .settings import FlowSettings


class EmptyParams(BaseModel):
    model_config = {"extra": "forbid"}


class ProjectParams(BaseModel):
    model_config = {"extra": "forbid"}

    project: str | None = Field(
        default=None, description="Proyecto GDC; vacío usa GDC_PROJECTS."
    )


class DownloadParams(ProjectParams):
    limit: int | None = Field(default=None, ge=1, description="Máximo de archivos a descargar.")
    retry_failed: bool = Field(default=False, description="Reintentar descargas fallidas.")


class CxPivotParams(ProjectParams):
    top_genes: int = Field(default=100, ge=1, le=5000)
    metric: Literal["fpkm_unstranded", "tpm_unstranded"] = "fpkm_unstranded"
    log2: bool = False
    sample_type: str | None = None


@dataclass
class RunContext:
    """Entorno de ejecución de un nodo."""

    settings: FlowSettings
    log: Callable[[str, str], None] = lambda level, message: None

    def projects(self, project: str | None) -> list[str]:
        return self.settings.etl.resolve_projects([project] if project else None)


@dataclass
class RunResult:
    rows_in: int = 0
    rows_out: int = 0
    outputs: list[str] = field(default_factory=list)
    detail: dict[str, Any] = field(default_factory=dict)


def _total(value: Any) -> int:
    if isinstance(value, dict):
        return sum(int(v) for v in value.values() if isinstance(v, (int, float)))
    if isinstance(value, (int, float)):
        return int(value)
    return 0


def _as_detail(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return {str(k): v for k, v in value.items()}
    return {}


# --------------------------------------------------------------------- runners
def _run_extract_cases(params: ProjectParams, ctx: RunContext) -> RunResult:
    from tcga.landing.cases import extract_cases

    projects = ctx.projects(params.project)
    result = extract_cases(projects)
    return RunResult(rows_out=_total(result), detail=_as_detail(result))


def _run_extract_files_manifest(params: ProjectParams, ctx: RunContext) -> RunResult:
    from tcga.landing.files import extract_files_manifest

    projects = ctx.projects(params.project)
    result = extract_files_manifest(projects)
    return RunResult(rows_out=_total(result), detail=_as_detail(result))


def _run_extract_maf_manifest(params: ProjectParams, ctx: RunContext) -> RunResult:
    from tcga.landing.mutations import extract_maf_manifest

    projects = ctx.projects(params.project)
    result = extract_maf_manifest(projects)
    return RunResult(rows_out=_total(result), detail=_as_detail(result))


def _run_download_expressions(params: DownloadParams, ctx: RunContext) -> RunResult:
    from tcga.landing.expressions import download_expressions

    projects = ctx.projects(params.project)
    result = download_expressions(
        projects, limit=params.limit, retry_failed=params.retry_failed
    )
    return RunResult(rows_out=_total(result), detail=_as_detail(result))


def _run_bronze_cases(params: ProjectParams, ctx: RunContext) -> RunResult:
    from tcga.bronze.cases import bronze_cases

    result = bronze_cases(ctx.projects(params.project))
    return RunResult(rows_out=_total(result), detail=_as_detail(result))


def _run_bronze_files(params: ProjectParams, ctx: RunContext) -> RunResult:
    from tcga.bronze.cases import bronze_files

    result = bronze_files(ctx.projects(params.project))
    return RunResult(rows_out=_total(result), detail=_as_detail(result))


def _run_bronze_expressions(params: ProjectParams, ctx: RunContext) -> RunResult:
    from tcga.bronze.expressions import bronze_expressions

    result = bronze_expressions(ctx.projects(params.project))
    return RunResult(rows_out=_total(result), detail=_as_detail(result))


def _run_bronze_mutations(params: ProjectParams, ctx: RunContext) -> RunResult:
    from tcga.bronze.mutations import bronze_mutations

    result = bronze_mutations(ctx.projects(params.project))
    return RunResult(rows_out=_total(result), detail=_as_detail(result))


def _run_silver_clinical(params: ProjectParams, ctx: RunContext) -> RunResult:
    from tcga.silver.clinical import silver_clinical

    result = silver_clinical(ctx.projects(params.project))
    return RunResult(rows_out=_total(result), detail=_as_detail(result))


def _run_silver_case_expressions(params: ProjectParams, ctx: RunContext) -> RunResult:
    from tcga.silver.case_expressions import silver_case_expressions

    result = silver_case_expressions(ctx.projects(params.project))
    return RunResult(rows_out=_total(result), detail=_as_detail(result))


def _run_silver_mutations(params: ProjectParams, ctx: RunContext) -> RunResult:
    from tcga.silver.mutations import silver_mutations

    result = silver_mutations(ctx.projects(params.project))
    return RunResult(rows_out=_total(result), detail=_as_detail(result))


def _run_gold_top_20_diagnosis(params: ProjectParams, ctx: RunContext) -> RunResult:
    from tcga.gold.top_diagnosis import gold_top_20_diagnosis

    result = gold_top_20_diagnosis(ctx.projects(params.project))
    return RunResult(rows_out=_total(result), detail=_as_detail(result))


def _run_gold_sample_summary(params: ProjectParams, ctx: RunContext) -> RunResult:
    from tcga.gold.extras import sample_summary

    result = sample_summary(ctx.projects(params.project))
    return RunResult(rows_out=_total(result), detail=_as_detail(result))


def _run_gold_gene_stats(params: ProjectParams, ctx: RunContext) -> RunResult:
    from tcga.gold.extras import gene_stats

    result = gene_stats(ctx.projects(params.project))
    return RunResult(rows_out=_total(result), detail=_as_detail(result))


def _run_gold_gene_dim(params: ProjectParams, ctx: RunContext) -> RunResult:
    from tcga.gold.extras import populate_gene_dim

    result = populate_gene_dim(ctx.projects(params.project))
    return RunResult(rows_out=_total(result), detail={"genes": result})


def _run_gold_marker_genes(params: EmptyParams, ctx: RunContext) -> RunResult:
    from tcga.gold.extras import seed_marker_genes

    result = seed_marker_genes()
    return RunResult(rows_out=_total(result), detail={"marcadores": result})


def _run_gold_sample_marker_values(params: ProjectParams, ctx: RunContext) -> RunResult:
    from tcga.gold.extras import sample_marker_values

    result = sample_marker_values(ctx.projects(params.project))
    return RunResult(rows_out=_total(result), detail=_as_detail(result))


def _run_gold_cx_pivot(params: CxPivotParams, ctx: RunContext) -> RunResult:
    from tcga.gold.cx_pivot import gold_cx_pivot

    result = gold_cx_pivot(
        ctx.projects(params.project),
        top_genes=params.top_genes,
        metric=params.metric,
        log2=params.log2,
        sample_type=params.sample_type,
    )
    return RunResult(rows_out=_total(result), detail={"muestras": result})


# ------------------------------------------------------------- runners GEMAMX v2
def _run_read_moesm8_maf(params: EmptyParams, ctx: RunContext) -> RunResult:
    from .gemamx.v2 import read_moesm8_maf
    res = read_moesm8_maf(ctx.settings.gemamx_dir, Path(ctx.settings.data_dir()))
    return RunResult(rows_out=res["total_rows"], detail=res)


def _run_read_moesm9_drivers(params: EmptyParams, ctx: RunContext) -> RunResult:
    from .gemamx.v2 import read_moesm9_drivers
    res = read_moesm9_drivers(ctx.settings.gemamx_dir, Path(ctx.settings.data_dir()))
    return RunResult(rows_out=res["total_rows"], detail=res)


def _run_build_driver_lookup(params: EmptyParams, ctx: RunContext) -> RunResult:
    from .gemamx.v2 import build_driver_lookup
    res = build_driver_lookup(Path(ctx.settings.data_dir()))
    return RunResult(rows_out=res["total_drivers"], detail=res)


def _run_normalize_romero_mutations(params: EmptyParams, ctx: RunContext) -> RunResult:
    from .gemamx.v2 import normalize_romero_mutations
    res = normalize_romero_mutations(Path(ctx.settings.data_dir()))
    return RunResult(rows_in=res["total_rows"], rows_out=res["total_rows"], detail=res)


def _run_compute_top_variants(params: EmptyParams, ctx: RunContext) -> RunResult:
    from .gemamx.v2 import compute_top_variants
    res = compute_top_variants(Path(ctx.settings.data_dir()))
    return RunResult(rows_out=res["top_variants_count"], detail=res)


def _run_compute_mutation_stats(params: EmptyParams, ctx: RunContext) -> RunResult:
    from .gemamx.v2 import compute_mutation_stats
    res = compute_mutation_stats(Path(ctx.settings.data_dir()))
    return RunResult(rows_out=res["total_mutations"], detail=res)


def _run_generate_fasta_variants(params: EmptyParams, ctx: RunContext) -> RunResult:
    from .gemamx.v2 import generate_fasta_variants
    res = generate_fasta_variants(ctx.settings.gemamx_dir, Path(ctx.settings.data_dir()))
    return RunResult(rows_out=res["total_fasta_files"], detail=res)


# -------------------------------------------------------- runners KNIME detallados v2
def _run_moesm8_reader(params: EmptyParams, ctx: RunContext) -> RunResult:
    from .gemamx.knime_v2 import step_moesm8_reader
    res = step_moesm8_reader(ctx.settings.gemamx_dir, Path(ctx.settings.data_dir()))
    return RunResult(rows_out=res["total_rows"], detail=res)


def _run_moesm8_key(params: EmptyParams, ctx: RunContext) -> RunResult:
    from .gemamx.knime_v2 import step_moesm8_key
    res = step_moesm8_key(Path(ctx.settings.data_dir()))
    return RunResult(rows_in=res["total_rows"], rows_out=res["total_rows"], detail=res)


def _run_moesm9_reader(params: EmptyParams, ctx: RunContext) -> RunResult:
    from .gemamx.knime_v2 import step_moesm9_reader
    res = step_moesm9_reader(ctx.settings.gemamx_dir, Path(ctx.settings.data_dir()))
    return RunResult(rows_out=res["total_rows"], detail=res)


def _run_moesm9_regex_extract(params: EmptyParams, ctx: RunContext) -> RunResult:
    from .gemamx.knime_v2 import step_moesm9_regex_extract
    res = step_moesm9_regex_extract(Path(ctx.settings.data_dir()))
    return RunResult(rows_out=res["total_extracted"], detail=res)


def _run_moesm9_key(params: EmptyParams, ctx: RunContext) -> RunResult:
    from .gemamx.knime_v2 import step_moesm9_key
    res = step_moesm9_key(Path(ctx.settings.data_dir()))
    return RunResult(rows_in=res["total_rows"], rows_out=res["total_rows"], detail=res)


def _run_joiner_maf_drivers(params: EmptyParams, ctx: RunContext) -> RunResult:
    from .gemamx.knime_v2 import step_joiner_maf_drivers
    res = step_joiner_maf_drivers(Path(ctx.settings.data_dir()))
    return RunResult(rows_in=res["total_rows"], rows_out=res["total_rows"], detail=res)


def _run_regex_extractor_prot(params: EmptyParams, ctx: RunContext) -> RunResult:
    from .gemamx.knime_v2 import step_regex_extractor_prot
    res = step_regex_extractor_prot(Path(ctx.settings.data_dir()))
    return RunResult(rows_in=res["total_rows"], rows_out=res["total_rows"], detail=res)


def _run_cell_replacer_aa(params: EmptyParams, ctx: RunContext) -> RunResult:
    from .gemamx.knime_v2 import step_cell_replacer_aa
    res = step_cell_replacer_aa(Path(ctx.settings.data_dir()))
    return RunResult(rows_in=res["total_rows"], rows_out=res["total_rows"], detail=res)


def _run_string_manip_pchange(params: EmptyParams, ctx: RunContext) -> RunResult:
    from .gemamx.knime_v2 import step_string_manip_pchange
    res = step_string_manip_pchange(Path(ctx.settings.data_dir()))
    return RunResult(rows_in=res["total_rows"], rows_out=res["total_rows"], detail=res)


def _run_ref_row_filter_genes(params: EmptyParams, ctx: RunContext) -> RunResult:
    from .gemamx.knime_v2 import step_ref_row_filter_genes
    res = step_ref_row_filter_genes(Path(ctx.settings.data_dir()))
    return RunResult(rows_out=res["retained_rows"], detail=res)


def _run_row_filter_missense(params: EmptyParams, ctx: RunContext) -> RunResult:
    from .gemamx.knime_v2 import step_row_filter_missense
    res = step_row_filter_missense(Path(ctx.settings.data_dir()))
    return RunResult(rows_out=res["retained_rows"], detail=res)


def _run_row_filter_resolved(params: EmptyParams, ctx: RunContext) -> RunResult:
    from .gemamx.knime_v2 import step_row_filter_resolved
    res = step_row_filter_resolved(Path(ctx.settings.data_dir()))
    return RunResult(rows_out=res["retained_rows"], detail=res)


def _run_groupby_variants(params: EmptyParams, ctx: RunContext) -> RunResult:
    from .gemamx.knime_v2 import step_groupby_variants
    res = step_groupby_variants(Path(ctx.settings.data_dir()))
    return RunResult(rows_out=res["unique_variants"], detail=res)


def _run_math_formula_pct(params: EmptyParams, ctx: RunContext) -> RunResult:
    from .gemamx.knime_v2 import step_math_formula_pct
    res = step_math_formula_pct(Path(ctx.settings.data_dir()))
    return RunResult(rows_in=res["total_rows"], rows_out=res["total_rows"], detail=res)


def _run_rule_engine_class(params: EmptyParams, ctx: RunContext) -> RunResult:
    from .gemamx.knime_v2 import step_rule_engine_class
    res = step_rule_engine_class(Path(ctx.settings.data_dir()))
    return RunResult(rows_in=res["total_rows"], rows_out=res["total_rows"], detail=res)


def _run_sorter_top_variants(params: EmptyParams, ctx: RunContext) -> RunResult:
    from .gemamx.knime_v2 import step_sorter_top_variants
    res = step_sorter_top_variants(Path(ctx.settings.data_dir()))
    return RunResult(rows_out=res["top_variants_count"], detail=res)


def _run_step_mutation_stats(params: EmptyParams, ctx: RunContext) -> RunResult:
    from .gemamx.knime_v2 import step_mutation_stats
    res = step_mutation_stats(Path(ctx.settings.data_dir()))
    return RunResult(rows_out=res["total_mutations"], detail=res)


def _run_step_variant_sequences(params: EmptyParams, ctx: RunContext) -> RunResult:
    from .gemamx.knime_v2 import step_variant_sequences
    res = step_variant_sequences(ctx.settings.gemamx_dir, Path(ctx.settings.data_dir()))
    return RunResult(rows_out=res["total_fasta_files"], detail=res)


# ------------------------------------------------------------- runners GEMAMX v1
def _run_fetch_mutations(params: EmptyParams, ctx: RunContext) -> RunResult:
    from .gemamx.v1 import fetch_mutations
    res = fetch_mutations(ctx.settings.gemamx_dir, Path(ctx.settings.data_dir()))
    return RunResult(rows_out=res["total_rows"], detail=res)


def _run_fetch_genes(params: EmptyParams, ctx: RunContext) -> RunResult:
    from .gemamx.v1 import fetch_genes
    res = fetch_genes(Path(ctx.settings.data_dir()))
    return RunResult(rows_out=res["total_genes"], detail=res)


def _run_fetch_uniprot_fasta(params: EmptyParams, ctx: RunContext) -> RunResult:
    from .gemamx.v1 import fetch_uniprot_fasta
    res = fetch_uniprot_fasta(ctx.settings.gemamx_dir, Path(ctx.settings.data_dir()))
    return RunResult(rows_out=res["wt_fasta_count"], detail=res)


def _run_normalize_v1_mutations(params: EmptyParams, ctx: RunContext) -> RunResult:
    from .gemamx.v1 import normalize_v1_mutations
    res = normalize_v1_mutations(Path(ctx.settings.data_dir()))
    return RunResult(rows_in=res["total_rows"], rows_out=res["total_rows"], detail=res)


def _run_wt_sequences(params: EmptyParams, ctx: RunContext) -> RunResult:
    from .gemamx.v1 import wt_sequences
    res = wt_sequences(Path(ctx.settings.data_dir()))
    return RunResult(rows_out=res["wt_sequences_count"], detail=res)


def _run_compute_v1_top_variants(params: EmptyParams, ctx: RunContext) -> RunResult:
    from .gemamx.v1 import compute_v1_top_variants
    res = compute_v1_top_variants(Path(ctx.settings.data_dir()))
    return RunResult(rows_out=res["top_variants_count"], detail=res)


def _run_compute_v1_mutation_stats(params: EmptyParams, ctx: RunContext) -> RunResult:
    from .gemamx.v1 import compute_v1_mutation_stats
    res = compute_v1_mutation_stats(Path(ctx.settings.data_dir()))
    return RunResult(rows_out=res["total_mutations"], detail=res)


def _run_generate_v1_fasta_variants(params: EmptyParams, ctx: RunContext) -> RunResult:
    from .gemamx.v1 import generate_v1_fasta_variants
    res = generate_v1_fasta_variants(Path(ctx.settings.data_dir()))
    return RunResult(rows_out=res["valid_variants"], detail=res)


# -------------------------------------------------------------------- catálogo
@dataclass(frozen=True)
class NodeType:
    key: str
    layer: Layer
    label: str
    description: str
    inputs: tuple[Port, ...]
    outputs: tuple[Port, ...]
    output_specs: tuple[OutputSpec, ...]
    param_model: type[BaseModel]
    run: Callable[[Any, RunContext], RunResult]
    long_running: bool = False


def _table(key: str, label: str, target: str) -> OutputSpec:
    return OutputSpec(key=key, label=label, kind=OutputKind.TABLE, target=target)


def _parquet(key: str, label: str, target: str) -> OutputSpec:
    return OutputSpec(key=key, label=label, kind=OutputKind.PARQUET, target=target)


def _json(key: str, label: str, target: str) -> OutputSpec:
    return OutputSpec(key=key, label=label, kind=OutputKind.JSON, target=target)


_IN_JSON = Port(name="entrada", kind=PortKind.JSON)
_IN_FILES = Port(name="archivos", kind=PortKind.FILES)
_IN_PARQUET = Port(name="datos", kind=PortKind.PARQUET)
_IN_TABLE = Port(name="tabla", kind=PortKind.TABLE)
_OUT_JSON = Port(name="manifiesto", kind=PortKind.JSON)
_OUT_FILES = Port(name="archivos", kind=PortKind.FILES)
_OUT_PARQUET = Port(name="parquet", kind=PortKind.PARQUET)
_OUT_TABLE = Port(name="tabla", kind=PortKind.TABLE)


def _run_read_moesm4_clinical(_params: Any, ctx: RunContext) -> RunResult:
    from .gemamx.v2_1 import read_moesm4_clinical

    gemamx_dir = Path(getattr(ctx.settings, "gemamx_data_dir", "D:/RespaldoLaptopHP/ProyectosIA/GEMAMX"))
    data_dir = ctx.settings.etl.data_dir
    res = read_moesm4_clinical(gemamx_dir, data_dir)
    return RunResult(
        rows_in=res["total_samples"],
        rows_out=res["total_samples"],
        outputs=[res["parquet_file"]],
        detail=res,
    )


def _run_normalize_clinical_metadata(_params: Any, ctx: RunContext) -> RunResult:
    from .gemamx.v2_1 import normalize_clinical_metadata

    data_dir = ctx.settings.etl.data_dir
    res = normalize_clinical_metadata(data_dir)
    return RunResult(
        rows_in=res["normalized_samples"],
        rows_out=res["normalized_samples"],
        outputs=[res["parquet_file"]],
        detail=res,
    )


def _run_compute_variant_clinical_summary(_params: Any, ctx: RunContext) -> RunResult:
    from .gemamx.v2_1 import compute_variant_clinical_summary

    data_dir = ctx.settings.etl.data_dir
    res = compute_variant_clinical_summary(data_dir)
    return RunResult(
        rows_in=res["variants_summarized"],
        rows_out=res["variants_summarized"],
        outputs=[res["parquet_file"], res["json_file"], res["csv_file"]],
        detail=res,
    )


NODE_TYPES: dict[str, NodeType] = {
    # ----------------------------------------------------------------- landing
    "extract_cases": NodeType(
        key="extract_cases",
        layer=Layer.LANDING,
        label="Extracción de casos",
        description="Descarga los casos clínicos paginados del GDC a landing.",
        inputs=(),
        outputs=(_OUT_JSON,),
        output_specs=(
            _json("pages", "Páginas de casos", "landing/cases/*/page_*.json"),
        ),
        param_model=ProjectParams,
        run=_run_extract_cases,
        long_running=True,
    ),
    "extract_files_manifest": NodeType(
        key="extract_files_manifest",
        layer=Layer.LANDING,
        label="Manifiesto de expresión",
        description="Crea el manifiesto de archivos de expresión (RNA-seq).",
        inputs=(),
        outputs=(_OUT_JSON,),
        output_specs=(
            _json("manifest", "Manifiesto", "landing/files/*/manifest.json"),
        ),
        param_model=ProjectParams,
        run=_run_extract_files_manifest,
    ),
    "extract_maf_manifest": NodeType(
        key="extract_maf_manifest",
        layer=Layer.LANDING,
        label="Manifiesto MAF",
        description="Crea el manifiesto de archivos MAF (mutaciones somáticas).",
        inputs=(),
        outputs=(_OUT_JSON,),
        output_specs=(
            _json("manifest", "Manifiesto MAF", "landing/mutations/*/maf_manifest.json"),
        ),
        param_model=ProjectParams,
        run=_run_extract_maf_manifest,
    ),
    "download_expressions": NodeType(
        key="download_expressions",
        layer=Layer.LANDING,
        label="Descarga de expresión",
        description="Descarga idempotente de TSV de expresión con validación md5.",
        inputs=(_IN_JSON,),
        outputs=(_OUT_FILES,),
        output_specs=(
            _table("control", "Control de descargas", "ops.download_control"),
            _json("files", "TSV descargados", "landing/expressions/*/*/*.tsv"),
        ),
        param_model=DownloadParams,
        run=_run_download_expressions,
        long_running=True,
    ),
    # ------------------------------------------------------------------ bronze
    "bronze_cases": NodeType(
        key="bronze_cases",
        layer=Layer.BRONZE,
        label="Bronze casos",
        description="Aplana los casos JSON a Parquet y a bronze.cases_raw (JSONB).",
        inputs=(_IN_JSON,),
        outputs=(_OUT_PARQUET, _OUT_TABLE),
        output_specs=(
            _table("table", "Tabla bronze.cases_raw", "bronze.cases_raw"),
            _parquet("parquet", "Parquet de casos", "bronze/cases/**/*.parquet"),
        ),
        param_model=ProjectParams,
        run=_run_bronze_cases,
    ),
    "bronze_files": NodeType(
        key="bronze_files",
        layer=Layer.BRONZE,
        label="Bronze archivos",
        description="Aplana el manifiesto de archivos a Parquet.",
        inputs=(_IN_JSON,),
        outputs=(_OUT_PARQUET,),
        output_specs=(
            _parquet("parquet", "Parquet de archivos", "bronze/files/**/files.parquet"),
        ),
        param_model=ProjectParams,
        run=_run_bronze_files,
    ),
    "bronze_expressions": NodeType(
        key="bronze_expressions",
        layer=Layer.BRONZE,
        label="Bronze expresión",
        description="Convierte los TSV de expresión a Parquet con DuckDB.",
        inputs=(_IN_FILES,),
        outputs=(_OUT_PARQUET,),
        output_specs=(
            _parquet("parquet", "Parquet de expresión", "bronze/expressions/**/*.parquet"),
        ),
        param_model=ProjectParams,
        run=_run_bronze_expressions,
        long_running=True,
    ),
    "bronze_mutations": NodeType(
        key="bronze_mutations",
        layer=Layer.BRONZE,
        label="Bronze mutaciones",
        description="Descarga y parsea los MAF a Parquet y a bronze.mutations.",
        inputs=(_IN_JSON,),
        outputs=(_OUT_PARQUET, _OUT_TABLE),
        output_specs=(
            _table("table", "Tabla bronze.mutations", "bronze.mutations"),
            _parquet("parquet", "Parquet de mutaciones", "bronze/mutations/**/*.parquet"),
        ),
        param_model=ProjectParams,
        run=_run_bronze_mutations,
        long_running=True,
    ),
    # ------------------------------------------------------------------ silver
    "silver_clinical": NodeType(
        key="silver_clinical",
        layer=Layer.SILVER,
        label="Silver clínica",
        description=(
            "Normaliza casos, demografía, diagnósticos, exposiciones, muestras y "
            "archivos en las tablas silver."
        ),
        inputs=(_IN_PARQUET,),
        outputs=(_OUT_TABLE,),
        output_specs=(
            _table("cases", "Tabla silver.cases", "silver.cases"),
            _table("samples", "Tabla silver.samples", "silver.samples"),
            _table("files", "Tabla silver.files", "silver.files"),
        ),
        param_model=ProjectParams,
        run=_run_silver_clinical,
    ),
    "silver_case_expressions": NodeType(
        key="silver_case_expressions",
        layer=Layer.SILVER,
        label="Silver expresión",
        description="Expresión narrow con case_id, muestra y tipo de muestra.",
        inputs=(_IN_PARQUET, _IN_FILES),
        outputs=(_OUT_PARQUET,),
        output_specs=(
            _parquet(
                "parquet",
                "Parquet de expresión silver",
                "silver/case_expressions/**/*.parquet",
            ),
        ),
        param_model=ProjectParams,
        run=_run_silver_case_expressions,
        long_running=True,
    ),
    "silver_mutations": NodeType(
        key="silver_mutations",
        layer=Layer.SILVER,
        label="Silver mutaciones",
        description="Mutaciones normalizadas (solo PASS, deduplicadas) en silver.mutations.",
        inputs=(_IN_PARQUET,),
        outputs=(_OUT_TABLE,),
        output_specs=(
            _table("table", "Tabla silver.mutations", "silver.mutations"),
        ),
        param_model=ProjectParams,
        run=_run_silver_mutations,
    ),
    # -------------------------------------------------------------------- gold
    "gold_top_20_diagnosis": NodeType(
        key="gold_top_20_diagnosis",
        layer=Layer.GOLD,
        label="Top 20 diagnósticos",
        description="Los 20 diagnósticos primarios más frecuentes de la cohorte.",
        inputs=(_IN_TABLE,),
        outputs=(_OUT_TABLE,),
        output_specs=(
            _table("table", "Tabla gold.top_20_diagnosis", "gold.top_20_diagnosis"),
        ),
        param_model=ProjectParams,
        run=_run_gold_top_20_diagnosis,
    ),
    "gold_sample_summary": NodeType(
        key="gold_sample_summary",
        layer=Layer.GOLD,
        label="Resumen de muestras",
        description="Conteo de muestras y casos por tipo, estado vital y estadio.",
        inputs=(_IN_TABLE,),
        outputs=(_OUT_TABLE,),
        output_specs=(
            _table("table", "Tabla gold.sample_summary", "gold.sample_summary"),
        ),
        param_model=ProjectParams,
        run=_run_gold_sample_summary,
    ),
    "gold_gene_stats": NodeType(
        key="gold_gene_stats",
        layer=Layer.GOLD,
        label="Estadísticas génicas",
        description="Media, varianza, desviación y número de muestras por gen y métrica.",
        inputs=(_IN_PARQUET,),
        outputs=(_OUT_TABLE, _OUT_PARQUET),
        output_specs=(
            _table("table", "Tabla gold.gene_stats", "gold.gene_stats"),
            _parquet("parquet", "Parquet de estadísticas", "gold/gene_stats/data.parquet"),
        ),
        param_model=ProjectParams,
        run=_run_gold_gene_stats,
        long_running=True,
    ),
    "gold_gene_dim": NodeType(
        key="gold_gene_dim",
        layer=Layer.GOLD,
        label="Dimensión de genes",
        description="Todos los genes distintos observados en la expresión silver.",
        inputs=(_IN_PARQUET,),
        outputs=(_OUT_TABLE,),
        output_specs=(
            _table("table", "Tabla gold.gene_dim", "gold.gene_dim"),
        ),
        param_model=ProjectParams,
        run=_run_gold_gene_dim,
    ),
    "gold_marker_genes": NodeType(
        key="gold_marker_genes",
        layer=Layer.GOLD,
        label="Genes marcadores",
        description=(
            "Siembra los 8 marcadores clínicos resolviendo su gene_id real desde "
            "gold.gene_dim."
        ),
        inputs=(_IN_TABLE,),
        outputs=(_OUT_TABLE,),
        output_specs=(
            _table("table", "Tabla gold.marker_genes", "gold.marker_genes"),
        ),
        param_model=EmptyParams,
        run=_run_gold_marker_genes,
    ),
    "gold_sample_marker_values": NodeType(
        key="gold_sample_marker_values",
        layer=Layer.GOLD,
        label="Valores de marcadores",
        description="TPM y percentil de cada marcador por muestra y tipo de muestra.",
        inputs=(_IN_PARQUET, _IN_TABLE),
        outputs=(_OUT_TABLE,),
        output_specs=(
            _table(
                "table",
                "Tabla gold.sample_marker_values",
                "gold.sample_marker_values",
            ),
        ),
        param_model=ProjectParams,
        run=_run_gold_sample_marker_values,
    ),
    "gold_cx_pivot": NodeType(
        key="gold_cx_pivot",
        layer=Layer.GOLD,
        label="Pivote de expresión",
        description="Varianza por gen y matriz ancha de expresión por muestra.",
        inputs=(_IN_PARQUET,),
        outputs=(_OUT_TABLE, _OUT_PARQUET),
        output_specs=(
            _table("table", "Tabla gold.cx_pivot", "gold.cx_pivot"),
            _parquet("parquet", "Parquet del pivote", "gold/cx_pivot/data.parquet"),
        ),
        param_model=CxPivotParams,
        run=_run_gold_cx_pivot,
        long_running=True,
    ),
    # ============================================================= GEMAMX v2 (granular KNIME)
    # Bloque 1 · Lectura y cruce
    "knime_v2_moesm8_reader": NodeType(
        key="knime_v2_moesm8_reader",
        layer=Layer.BRONZE,
        label="Excel Reader (MOESM8 MAF)",
        description="Lee la hoja 'HM maf' del suplementario MOESM8 (28 114 variantes).",
        inputs=(_IN_FILES,),
        outputs=(_OUT_PARQUET,),
        output_specs=(_parquet("parquet", "MOESM8 raw", "gemamx/v2/steps/moesm8_raw.parquet"),),
        param_model=EmptyParams,
        run=_run_moesm8_reader,
    ),
    "knime_v2_moesm8_key": NodeType(
        key="knime_v2_moesm8_key",
        layer=Layer.BRONZE,
        label="String Manip. (clave compuesta MAF)",
        description="Crea la clave de cruce gen#chr#pos#ref#alt en las mutaciones somáticas.",
        inputs=(_IN_PARQUET,),
        outputs=(_OUT_PARQUET,),
        output_specs=(_parquet("parquet", "MOESM8 keyed", "gemamx/v2/steps/moesm8_keyed.parquet"),),
        param_model=EmptyParams,
        run=_run_moesm8_key,
    ),
    "knime_v2_moesm9_reader": NodeType(
        key="knime_v2_moesm9_reader",
        layer=Layer.BRONZE,
        label="Excel Reader (MOESM9 drivers)",
        description="Lee la hoja 'Driver mutation in HM' de MOESM9 (127 variantes driver).",
        inputs=(_IN_FILES,),
        outputs=(_OUT_PARQUET,),
        output_specs=(_parquet("parquet", "MOESM9 raw", "gemamx/v2/steps/moesm9_raw.parquet"),),
        param_model=EmptyParams,
        run=_run_moesm9_reader,
    ),
    "knime_v2_moesm9_regex": NodeType(
        key="knime_v2_moesm9_regex",
        layer=Layer.BRONZE,
        label="Regex Extractor (chr·pos·ref·alt)",
        description="Extrae coordenadas y alelos desde la notación gdna (chrN:g.POSref>alt).",
        inputs=(_IN_PARQUET,),
        outputs=(_OUT_PARQUET,),
        output_specs=(_parquet("parquet", "MOESM9 parsed", "gemamx/v2/steps/moesm9_parsed.parquet"),),
        param_model=EmptyParams,
        run=_run_moesm9_regex_extract,
    ),
    "knime_v2_moesm9_key": NodeType(
        key="knime_v2_moesm9_key",
        layer=Layer.BRONZE,
        label="String Manip. (clave compuesta drivers)",
        description="Crea la clave de cruce gene#chr#pos#ref#alt en los drivers.",
        inputs=(_IN_PARQUET,),
        outputs=(_OUT_PARQUET,),
        output_specs=(_parquet("parquet", "MOESM9 keyed", "gemamx/v2/steps/moesm9_keyed.parquet"),),
        param_model=EmptyParams,
        run=_run_moesm9_key,
    ),
    "knime_v2_joiner": NodeType(
        key="knime_v2_joiner",
        layer=Layer.SILVER,
        label="Joiner (Left Outer Join)",
        description="Cruce MAF x drivers por clave compuesta; conserva las 28 114 variantes.",
        inputs=(_IN_PARQUET, _IN_PARQUET),
        outputs=(_OUT_PARQUET,),
        output_specs=(_parquet("parquet", "MAF joined", "gemamx/v2/steps/maf_joined.parquet"),),
        param_model=EmptyParams,
        run=_run_joiner_maf_drivers,
    ),
    # Bloque 2 · Normalización
    "knime_v2_regex_prot": NodeType(
        key="knime_v2_regex_prot",
        layer=Layer.SILVER,
        label="Regex Extractor (p.His1047Arg)",
        description="Extrae la cadena proteica cruda desde protein_hgvsp.",
        inputs=(_IN_PARQUET,),
        outputs=(_OUT_PARQUET,),
        output_specs=(_parquet("parquet", "Prot extracted", "gemamx/v2/steps/prot_extracted.parquet"),),
        param_model=EmptyParams,
        run=_run_regex_extractor_prot,
    ),
    "knime_v2_cell_replacer": NodeType(
        key="knime_v2_cell_replacer",
        layer=Layer.SILVER,
        label="Cell Replacer (3 → 1 letra aa)",
        description="Traduce nombres de 3 letras a su letra única (His->H, Arg->R).",
        inputs=(_IN_PARQUET,),
        outputs=(_OUT_PARQUET,),
        output_specs=(_parquet("parquet", "AA replaced", "gemamx/v2/steps/aa_replaced.parquet"),),
        param_model=EmptyParams,
        run=_run_cell_replacer_aa,
    ),
    "knime_v2_string_manip_pchange": NodeType(
        key="knime_v2_string_manip_pchange",
        layer=Layer.SILVER,
        label="String Manip. (H1047R)",
        description="Consolida protein_change normalizado y genera el silver unificado (GRCh37).",
        inputs=(_IN_PARQUET,),
        outputs=(_OUT_PARQUET,),
        output_specs=(_parquet("parquet", "Silver normalizado", "gemamx/v2/steps/silver_normalized.parquet"),),
        param_model=EmptyParams,
        run=_run_string_manip_pchange,
    ),
    # Bloque 3 · Priorización
    "knime_v2_ref_row_filter": NodeType(
        key="knime_v2_ref_row_filter",
        layer=Layer.GOLD,
        label="Ref. Row Filter (15 genes clave)",
        description="Filtra mutaciones que pertenezcan a los 15 genes diana priorizados.",
        inputs=(_IN_PARQUET,),
        outputs=(_OUT_PARQUET,),
        output_specs=(_parquet("parquet", "15 genes", "gemamx/v2/steps/filter_15_genes.parquet"),),
        param_model=EmptyParams,
        run=_run_ref_row_filter_genes,
    ),
    "knime_v2_row_filter_missense": NodeType(
        key="knime_v2_row_filter_missense",
        layer=Layer.GOLD,
        label="Row Filter (solo missense)",
        description="Conserva exclusivamente variantes de tipo Missense_Mutation.",
        inputs=(_IN_PARQUET,),
        outputs=(_OUT_PARQUET,),
        output_specs=(_parquet("parquet", "Missense", "gemamx/v2/steps/filter_missense.parquet"),),
        param_model=EmptyParams,
        run=_run_row_filter_missense,
    ),
    "knime_v2_row_filter_resolved": NodeType(
        key="knime_v2_row_filter_resolved",
        layer=Layer.GOLD,
        label="Row Filter (cambio resuelto)",
        description="Descarta variantes que carezcan de cambio proteico resuelto.",
        inputs=(_IN_PARQUET,),
        outputs=(_OUT_PARQUET,),
        output_specs=(_parquet("parquet", "Cambio resuelto", "gemamx/v2/steps/filter_resolved.parquet"),),
        param_model=EmptyParams,
        run=_run_row_filter_resolved,
    ),
    "knime_v2_groupby": NodeType(
        key="knime_v2_groupby",
        layer=Layer.GOLD,
        label="GroupBy (gen + cambio)",
        description="Agrupa por (hugo_symbol, protein_change) y cuenta pacientes únicos (freq).",
        inputs=(_IN_PARQUET,),
        outputs=(_OUT_PARQUET,),
        output_specs=(_parquet("parquet", "Agrupadas", "gemamx/v2/steps/grouped_variants.parquet"),),
        param_model=EmptyParams,
        run=_run_groupby_variants,
    ),
    "knime_v2_math_formula": NodeType(
        key="knime_v2_math_formula",
        layer=Layer.GOLD,
        label="Math Formula (÷ 134 × 100)",
        description="Calcula el porcentaje de cohorte sobre los 134 pacientes analizados.",
        inputs=(_IN_PARQUET,),
        outputs=(_OUT_PARQUET,),
        output_specs=(_parquet("parquet", "Porcentaje", "gemamx/v2/steps/math_pct.parquet"),),
        param_model=EmptyParams,
        run=_run_math_formula_pct,
    ),
    "knime_v2_rule_engine": NodeType(
        key="knime_v2_rule_engine",
        layer=Layer.GOLD,
        label="Rule Engine (5% · 1% · 0.5%)",
        description="Asigna categoría clínica: Patogénica, Probable Patogénica, VUS o Baja Frecuencia.",
        inputs=(_IN_PARQUET,),
        outputs=(_OUT_PARQUET,),
        output_specs=(_parquet("parquet", "Clasificadas", "gemamx/v2/steps/rule_classified.parquet"),),
        param_model=EmptyParams,
        run=_run_rule_engine_class,
    ),
    "knime_v2_sorter": NodeType(
        key="knime_v2_sorter",
        layer=Layer.GOLD,
        label="Sorter → CSV (top_variants · 49)",
        description="Ordena por frecuencia y orden en MAF, asigna rank y persiste top_variants.",
        inputs=(_IN_PARQUET,),
        outputs=(_OUT_PARQUET,),
        output_specs=(
            _parquet("parquet", "Top variantes", "gemamx/v2/gold/top_variants.parquet"),
            _json("json", "Top variantes JSON", "gemamx/v2/gold/top_variants.json"),
        ),
        param_model=EmptyParams,
        run=_run_sorter_top_variants,
    ),
    "knime_v2_mutation_stats": NodeType(
        key="knime_v2_mutation_stats",
        layer=Layer.GOLD,
        label="mutation_stats (distribución por gen)",
        description="Distribuciones por gen (top 20) y por tipo de variante (28 114 mutaciones).",
        inputs=(_IN_PARQUET,),
        outputs=(),
        output_specs=(_json("json", "Stats JSON", "gemamx/v2/gold/mutation_stats.json"),),
        param_model=EmptyParams,
        run=_run_step_mutation_stats,
    ),
    "knime_v2_variant_sequences": NodeType(
        key="knime_v2_variant_sequences",
        layer=Layer.GOLD,
        label="variant_sequences (WT y mutantes .fasta)",
        description="Genera 54 archivos FASTA (7 WT + 47 MUT) con cuarentena documentada.",
        inputs=(_IN_PARQUET,),
        outputs=(_OUT_FILES,),
        output_specs=(_json("json", "Índice FASTA", "gemamx/v2/gold/fasta_sequences.json"),),
        param_model=EmptyParams,
        run=_run_step_variant_sequences,
    ),
    # ============================================================= GEMAMX v1
    # ------------------------------------------------------------------ bronze
    "gemamx_v1_fetch_mutations": NodeType(
        key="gemamx_v1_fetch_mutations",
        layer=Layer.BRONZE,
        label="v1 Bronze Mutaciones cBioPortal",
        description="Carga mutaciones TCGA-BRCA desde cBioPortal (o fixture local).",
        inputs=(),
        outputs=(_OUT_PARQUET,),
        output_specs=(
            _parquet("parquet", "Mutaciones cBio", "gemamx/v1/bronze/cbio_mutations.parquet"),
        ),
        param_model=EmptyParams,
        run=_run_fetch_mutations,
    ),
    "gemamx_v1_fetch_genes": NodeType(
        key="gemamx_v1_fetch_genes",
        layer=Layer.BRONZE,
        label="v1 Bronze Genes clave",
        description="Registra los 15 genes clave con su tier de clasificación.",
        inputs=(),
        outputs=(_OUT_PARQUET,),
        output_specs=(
            _parquet("parquet", "Genes clave", "gemamx/v1/bronze/cbio_genes.parquet"),
        ),
        param_model=EmptyParams,
        run=_run_fetch_genes,
    ),
    "gemamx_v1_fetch_fasta": NodeType(
        key="gemamx_v1_fetch_fasta",
        layer=Layer.BRONZE,
        label="v1 Bronze FASTA UniProt",
        description="Carga secuencias canónicas WT de UniProt (o fixtures) para los 15 genes.",
        inputs=(),
        outputs=(_OUT_FILES,),
        output_specs=(),
        param_model=EmptyParams,
        run=_run_fetch_uniprot_fasta,
    ),
    # ------------------------------------------------------------------ silver
    "gemamx_v1_normalize": NodeType(
        key="gemamx_v1_normalize",
        layer=Layer.SILVER,
        label="v1 Silver Normalizar mutaciones",
        description="Normaliza mutaciones de cBioPortal con cohorte y ensamble GRCh37.",
        inputs=(_IN_PARQUET,),
        outputs=(_OUT_PARQUET,),
        output_specs=(
            _parquet(
                "parquet", "Mutaciones normalizadas",
                "gemamx/v1/silver/normalized_mutations.parquet",
            ),
        ),
        param_model=EmptyParams,
        run=_run_normalize_v1_mutations,
    ),
    "gemamx_v1_wt_sequences": NodeType(
        key="gemamx_v1_wt_sequences",
        layer=Layer.SILVER,
        label="v1 Silver Secuencias WT",
        description="Valida y copia secuencias WT a silver.",
        inputs=(_IN_FILES,),
        outputs=(_OUT_FILES,),
        output_specs=(),
        param_model=EmptyParams,
        run=_run_wt_sequences,
    ),
    # -------------------------------------------------------------------- gold
    "gemamx_v1_top_variants": NodeType(
        key="gemamx_v1_top_variants",
        layer=Layer.GOLD,
        label="v1 Gold Top variantes",
        description="Top-20 variantes Missense en 15 genes clave de TCGA-BRCA.",
        inputs=(_IN_PARQUET,),
        outputs=(_OUT_PARQUET,),
        output_specs=(
            _parquet("parquet", "Top variantes v1", "gemamx/v1/gold/top_variants.parquet"),
            _json("json", "Top variantes v1 JSON", "gemamx/v1/gold/top_variants.json"),
        ),
        param_model=EmptyParams,
        run=_run_compute_v1_top_variants,
    ),
    "gemamx_v1_mutation_stats": NodeType(
        key="gemamx_v1_mutation_stats",
        layer=Layer.GOLD,
        label="v1 Gold Estadísticas mutaciones",
        description="Distribuciones por gen y por tipo de variante de TCGA-BRCA.",
        inputs=(_IN_PARQUET,),
        outputs=(),
        output_specs=(
            _json("json", "Stats v1 JSON", "gemamx/v1/gold/mutation_stats.json"),
        ),
        param_model=EmptyParams,
        run=_run_compute_v1_mutation_stats,
    ),
    "gemamx_v1_fasta": NodeType(
        key="gemamx_v1_fasta",
        layer=Layer.GOLD,
        label="v1 Gold FASTA variantes",
        description="Mutantes de sustitución puntual con validación y cuarentena.",
        inputs=(_IN_PARQUET, _IN_FILES),
        outputs=(_OUT_FILES,),
        output_specs=(
            _json("json", "Índice FASTA v1", "gemamx/v1/gold/fasta_sequences.json"),
        ),
        param_model=EmptyParams,
        run=_run_generate_v1_fasta_variants,
    ),
    "knime_v2_1_moesm4_reader": NodeType(
        key="knime_v2_1_moesm4_reader",
        layer=Layer.BRONZE,
        label="Excel Reader (MOESM4 Clinical)",
        description="Lee la hoja 'Features' de MOESM4 con metadatos clínicos (204 muestras: edad, IHC, PAM50, grado SBR).",
        inputs=(),
        outputs=(_OUT_PARQUET,),
        output_specs=(
            _parquet("parquet", "MOESM4 Clinical Raw Parquet", "gemamx/v2_1/bronze/romero_clinical_raw.parquet"),
        ),
        param_model=EmptyParams,
        run=_run_read_moesm4_clinical,
    ),
    "knime_v2_1_clinical_normalize": NodeType(
        key="knime_v2_1_clinical_normalize",
        layer=Layer.SILVER,
        label="Clinical Normalizer (IHC · PAM50 · SBR)",
        description="Estandariza edad (<50 Younger, >=50 Older), subtipo IHC, PAM50 y grado histológico SBR.",
        inputs=(_IN_PARQUET,),
        outputs=(_OUT_PARQUET,),
        output_specs=(
            _parquet("parquet", "Clinical Metadata Parquet", "gemamx/v2_1/silver/clinical_metadata.parquet"),
        ),
        param_model=EmptyParams,
        run=_run_normalize_clinical_metadata,
    ),
    "knime_v2_1_variant_clinical_summary": NodeType(
        key="knime_v2_1_variant_clinical_summary",
        layer=Layer.GOLD,
        label="variant_clinical_summary (M5b Enriquecimiento)",
        description="Cruza mutaciones con metadatos clínicos generando el perfil clínico por variante priorizada.",
        inputs=(_IN_PARQUET,),
        outputs=(_OUT_PARQUET,),
        output_specs=(
            _parquet("parquet", "Variant Clinical Summary Parquet", "gemamx/v2_1/gold/variant_clinical_summary.parquet"),
            _json("json", "Variant Clinical Summary JSON", "gemamx/v2_1/gold/variant_clinical_summary.json"),
        ),
        param_model=EmptyParams,
        run=_run_compute_variant_clinical_summary,
    ),
}


def get_node_type(type_id: str) -> NodeType:
    try:
        return NODE_TYPES[type_id]
    except KeyError as exc:
        raise KeyError(f"Tipo de nodo no registrado: {type_id}") from exc


def catalog_payload() -> list[dict[str, Any]]:
    """Catálogo serializable para la UI (repositorio de nodos)."""
    items: list[dict[str, Any]] = []
    for node_type in NODE_TYPES.values():
        items.append(
            {
                "key": node_type.key,
                "label": node_type.label,
                "layer": node_type.layer.value,
                "description": node_type.description,
                "inputs": [p.model_dump() for p in node_type.inputs],
                "outputs": [p.model_dump() for p in node_type.outputs],
                "output_specs": [o.model_dump() for o in node_type.output_specs],
                "long_running": node_type.long_running,
                "params_schema": node_type.param_model.model_json_schema(),
            }
        )
    return items
