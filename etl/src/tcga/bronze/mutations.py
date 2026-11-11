"""B03: descarga y conversión de archivos MAF a Parquet (capa bronze)."""

from __future__ import annotations

import gzip
import hashlib
import shutil
from datetime import datetime, timezone
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq

from ..config import Settings, get_settings
from ..db import connect, copy_rows
from ..gdc import GDCClient
from ..landing.mutations import read_maf_manifest
from ..logging_utils import get_logger
from ..ops.runs import pipeline_step, record_dq, recorded_release

log = get_logger(__name__)

# Columnas que se guardan en la tabla bronze y el Parquet
_BRONZE_COLS: tuple[str, ...] = (
    "mutation_id",
    "file_id",
    "case_id",
    "case_submitter_id",
    "tumor_sample_barcode",
    "project_id",
    "hugo_symbol",
    "entrez_gene_id",
    "chromosome",
    "start_position",
    "end_position",
    "strand",
    "variant_classification",
    "variant_type",
    "reference_allele",
    "tumor_seq_allele2",
    "hgvsc",
    "hgvsp_short",
    "transcript_id",
    "exon_number",
    "t_depth",
    "t_ref_count",
    "t_alt_count",
    "n_depth",
    "filter_status",
    "gnomad_af",
    "impact",
    "sift",
    "polyphen",
    "clin_sig",
    "somatic",
    "_gdc_data_release",
    "_ingested_at",
)

MAF_SCHEMA = pa.schema([
    pa.field("mutation_id",           pa.string()),
    pa.field("file_id",               pa.string()),
    pa.field("case_id",               pa.string()),
    pa.field("case_submitter_id",     pa.string()),
    pa.field("tumor_sample_barcode",  pa.string()),
    pa.field("project_id",            pa.string()),
    pa.field("hugo_symbol",           pa.string()),
    pa.field("entrez_gene_id",        pa.string()),
    pa.field("chromosome",            pa.string()),
    pa.field("start_position",        pa.int64()),
    pa.field("end_position",          pa.int64()),
    pa.field("strand",                pa.string()),
    pa.field("variant_classification",pa.string()),
    pa.field("variant_type",          pa.string()),
    pa.field("reference_allele",      pa.string()),
    pa.field("tumor_seq_allele2",     pa.string()),
    pa.field("hgvsc",                 pa.string()),
    pa.field("hgvsp_short",           pa.string()),
    pa.field("transcript_id",         pa.string()),
    pa.field("exon_number",           pa.string()),
    pa.field("t_depth",               pa.int32()),
    pa.field("t_ref_count",           pa.int32()),
    pa.field("t_alt_count",           pa.int32()),
    pa.field("n_depth",               pa.int32()),
    pa.field("filter_status",         pa.string()),
    pa.field("gnomad_af",             pa.float64()),
    pa.field("impact",                pa.string()),
    pa.field("sift",                  pa.string()),
    pa.field("polyphen",              pa.string()),
    pa.field("clin_sig",              pa.string()),
    pa.field("somatic",               pa.string()),
    pa.field("_gdc_data_release",     pa.string()),
    pa.field("_ingested_at",          pa.timestamp("us", tz="UTC")),
])


def _int(v: Any) -> int | None:
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _float(v: Any) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _none_if_empty(v: Any) -> str | None:
    if v is None:
        return None
    s = str(v).strip()
    return None if s in ("", ".", "NA", "N/A") else s


def _sample_barcode(v: Any) -> str | None:
    """TCGA-XX-XXXX-01A-... -> TCGA-XX-XXXX-01A (barcode de muestra)."""
    s = _none_if_empty(v)
    if not s:
        return None
    parts = s.split("-")
    if len(parts) < 4:
        return None
    return "-".join(parts[:4])


def _mutation_id(file_id: str, case_id: str, chrom: str, start: Any, ref: str, alt: str) -> str:
    """ID determinista: file_id + posición genómica."""
    key = f"{file_id}:{case_id}:{chrom}:{start}:{ref}:{alt}"
    return hashlib.md5(key.encode()).hexdigest()


def _parse_maf_lines(
    lines: list[str],
    file_id: str,
    case_id: str,
    case_submitter_id: str,
    project_id: str,
    release: str | None,
) -> list[dict[str, Any]]:
    """Parsea las líneas de un MAF y devuelve filas listas para insertar."""
    header: list[str] = []
    rows: list[dict[str, Any]] = []
    now = datetime.now(timezone.utc)

    for line in lines:
        if line.startswith("#"):
            continue
        if not header:
            header = line.rstrip("\n").split("\t")
            continue
        parts = line.rstrip("\n").split("\t")
        if len(parts) < len(header):
            parts += [""] * (len(header) - len(parts))
        raw = dict(zip(header, parts))

        chrom = _none_if_empty(raw.get("Chromosome", ""))
        start = _int(raw.get("Start_Position"))
        ref = _none_if_empty(raw.get("Reference_Allele", "")) or ""
        alt = _none_if_empty(raw.get("Tumor_Seq_Allele2", "")) or ""

        rows.append({
            "mutation_id":            _mutation_id(file_id, case_id, chrom or "", start, ref, alt),
            "file_id":                file_id,
            "case_id":                case_id,
            "case_submitter_id":      case_submitter_id,
            "tumor_sample_barcode":   _sample_barcode(raw.get("Tumor_Sample_Barcode")),
            "project_id":             project_id,
            "hugo_symbol":            _none_if_empty(raw.get("Hugo_Symbol")),
            "entrez_gene_id":         _none_if_empty(raw.get("Entrez_Gene_Id")),
            "chromosome":             chrom,
            "start_position":         start,
            "end_position":           _int(raw.get("End_Position")),
            "strand":                 _none_if_empty(raw.get("Strand")),
            "variant_classification": _none_if_empty(raw.get("Variant_Classification")),
            "variant_type":           _none_if_empty(raw.get("Variant_Type")),
            "reference_allele":       ref or None,
            "tumor_seq_allele2":      alt or None,
            "hgvsc":                  _none_if_empty(raw.get("HGVSc")),
            "hgvsp_short":            _none_if_empty(raw.get("HGVSp_Short")),
            "transcript_id":          _none_if_empty(raw.get("Transcript_ID")),
            "exon_number":            _none_if_empty(raw.get("Exon_Number")),
            "t_depth":                _int(raw.get("t_depth")),
            "t_ref_count":            _int(raw.get("t_ref_count")),
            "t_alt_count":            _int(raw.get("t_alt_count")),
            "n_depth":                _int(raw.get("n_depth")),
            "filter_status":          _none_if_empty(raw.get("FILTER")),
            "gnomad_af":              _float(raw.get("gnomAD_AF")),
            "impact":                 _none_if_empty(raw.get("IMPACT")),
            "sift":                   _none_if_empty(raw.get("SIFT")),
            "polyphen":               _none_if_empty(raw.get("PolyPhen")),
            "clin_sig":               _none_if_empty(raw.get("CLIN_SIG")),
            "somatic":                _none_if_empty(raw.get("SOMATIC")),
            "_gdc_data_release":      release,
            "_ingested_at":           now,
        })
    return rows


def bronze_mutations(
    projects: list[str],
    settings: Settings | None = None,
) -> dict[str, int]:
    """Descarga archivos MAF del GDC, parsea y guarda en Parquet + PostgreSQL."""
    settings = settings or get_settings()
    client = GDCClient(settings)
    release = recorded_release()
    result: dict[str, int] = {}

    for project in projects:
        manifest = read_maf_manifest(project, settings)
        hits = manifest.get("hits", [])

        with pipeline_step("bronze_mutations", project) as run:
            out_dir = settings.bronze_root() / "mutations" / project
            shutil.rmtree(out_dir, ignore_errors=True)
            out_dir.mkdir(parents=True, exist_ok=True)

            files_ok = 0
            total_rows = 0
            parquet_path = out_dir / "mutations.parquet"

            with connect() as conn:
                conn.execute(
                    "DELETE FROM bronze.mutations WHERE project_id = %s", (project,)
                )

            writer: pq.ParquetWriter | None = None
            try:
                for hit in hits:
                    file_id = hit.get("file_id")
                    if not file_id:
                        continue
                    cases = hit.get("cases") or []
                    case = cases[0] if cases else {}
                    case_id = case.get("case_id") or ""
                    case_submitter_id = case.get("submitter_id") or ""
                    project_id = (case.get("project") or {}).get("project_id") or project

                    try:
                        tmp = out_dir / f"{file_id}.tmp"
                        client.download_batch([file_id], tmp)

                        with tmp.open("rb") as _fh:
                            _magic = _fh.read(2)
                        if _magic == b"\x1f\x8b":
                            with gzip.open(tmp, "rt", encoding="utf-8", errors="replace") as fh:
                                lines = fh.readlines()
                        else:
                            lines = tmp.read_text(encoding="utf-8", errors="replace").splitlines(keepends=True)

                        tmp.unlink(missing_ok=True)
                        rows = _parse_maf_lines(
                            lines, file_id, case_id, case_submitter_id, project_id, release
                        )
                    except Exception as exc:  # noqa: BLE001
                        log.warning(
                            "Error procesando MAF %s: %s",
                            file_id,
                            exc,
                            extra={"event": "maf_file_error", "file_id": file_id},
                        )
                        continue

                    if rows:
                        table = pa.Table.from_pylist(rows, schema=MAF_SCHEMA)
                        if writer is None:
                            writer = pq.ParquetWriter(
                                parquet_path, MAF_SCHEMA, compression="zstd"
                            )
                        writer.write_table(table)
                        with connect() as conn:
                            copy_rows(
                                conn,
                                "bronze.mutations",
                                list(_BRONZE_COLS),
                                (tuple(row[column] for column in _BRONZE_COLS) for row in rows),
                            )
                        total_rows += len(rows)

                    files_ok += 1
                    log.info(
                        "MAF procesado",
                        extra={"event": "maf_file_ok", "file_id": file_id, "mutations": len(rows)},
                    )
            finally:
                if writer is not None:
                    writer.close()

            if writer is None:
                pq.write_table(MAF_SCHEMA.empty_table(), parquet_path, compression="zstd")

            run.count(rows_in=len(hits), rows_out=total_rows)

            record_dq(
                run.run_id, run.step, project,
                "maf_bronze_not_empty", "warning",
                total_rows > 0,
                f"mutaciones={total_rows} archivos_ok={files_ok}/{len(hits)}",
            )
            record_dq(
                run.run_id, run.step, project,
                "maf_all_files_processed", "warning",
                files_ok == len(hits),
                f"ok={files_ok} total={len(hits)}",
            )

            result[project] = total_rows
            log.info(
                "Bronze de mutaciones",
                extra={
                    "event": "bronze_mutations",
                    "rows": total_rows,
                    "files": files_ok,
                },
            )

    return result
