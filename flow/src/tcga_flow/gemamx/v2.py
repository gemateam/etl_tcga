"""Lógica de transformación y extracción para GEMAMX v2 (cohorte Romero-Córdoba 2021).

Implementa el esquema medallón para MOESM8 y MOESM9 en DuckDB / Parquet:
- Bronze: read_moesm8_maf, read_moesm9_drivers
- Silver: build_driver_lookup, normalize_romero_mutations
- Gold: top_variants, mutation_stats, fasta_variants
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import duckdb
import openpyxl
import pyarrow as pa
import pyarrow.parquet as pq

TARGET_15_GENES = {
    "PIK3CA", "TP53", "ERBB2", "BRCA1", "BRCA2",
    "CDH1", "GATA3", "MAP3K1", "KMT2C", "PTEN",
    "RB1", "ARID1A", "SF3B1", "AKT1", "CBFB"
}

AA_MAP = {
    "Ala": "A", "Arg": "R", "Asn": "N", "Asp": "D", "Cys": "C",
    "Gln": "Q", "Glu": "E", "Gly": "G", "His": "H", "Ile": "I",
    "Leu": "L", "Lys": "K", "Met": "M", "Phe": "F", "Pro": "P",
    "Ser": "S", "Thr": "T", "Trp": "W", "Tyr": "Y", "Val": "V",
}

UNIPROT_MAP = {
    "PIK3CA": "P42336",
    "AKT1": "P31749",
    "TP53": "P04637",
    "ERBB2": "P04626",
    "PTEN": "P60484",
    "GATA3": "P23771",
    "MAP3K1": "Q13233",
}


def _norm_pchange(hgvsp: Any, pchange_raw: Any) -> str | None:
    if hgvsp:
        s = str(hgvsp).strip()
        if s.startswith("p."):
            s = s[2:]
        for three, one in AA_MAP.items():
            s = s.replace(three, one)
        return s
    return None


def _classify_variant(pct: float) -> str:
    if pct >= 5.0:
        return "Patogénica"
    if pct >= 1.0:
        return "Probable Patogénica"
    if pct >= 0.5:
        return "VUS"
    return "Baja Frecuencia"


def read_moesm8_maf(gemamx_dir: Path, data_dir: Path) -> dict[str, Any]:
    """Bronze: Lee la hoja 'HM maf' de MOESM8 y la guarda en bronze/romero_maf.parquet."""
    xlsx_path = gemamx_dir / "data" / "romero" / "41467_2021_22478_MOESM8_ESM.xlsx"
    if not xlsx_path.exists():
        raise FileNotFoundError(f"No se encontró MOESM8 en {xlsx_path}")

    sha256 = hashlib.sha256(xlsx_path.read_bytes()).hexdigest()
    ingested_at = datetime.now(timezone.utc).isoformat()

    wb = openpyxl.load_workbook(xlsx_path, read_only=True)
    ws = wb["HM maf"]

    rows = []
    for i, r in enumerate(ws.iter_rows(values_only=True)):
        if i < 2:
            continue
        try:
            start_pos = int(r[4]) if r[4] is not None else None
        except (ValueError, TypeError):
            start_pos = None

        try:
            end_pos = int(r[5]) if r[5] is not None else None
        except (ValueError, TypeError):
            end_pos = None

        rows.append({
            "hugo_symbol": str(r[0]).strip() if r[0] is not None else "Unknown",
            "center": str(r[1]).strip() if r[1] is not None else None,
            "ncbi_build": str(r[2]).strip() if r[2] is not None else "37",
            "chromosome": str(r[3]).strip() if r[3] is not None else None,
            "start_position": start_pos,
            "end_position": end_pos,
            "strand": str(r[6]).strip() if r[6] is not None else "+",
            "variant_classification": str(r[7]).strip() if r[7] is not None else "Unknown",
            "variant_type": str(r[8]).strip() if r[8] is not None else None,
            "reference_allele": str(r[9]).strip() if r[9] is not None else None,
            "tumor_seq_allele1": str(r[10]).strip() if r[10] is not None else None,
            "tumor_seq_allele2": str(r[11]).strip() if r[11] is not None else None,
            "sample_id": str(r[12]).strip() if r[12] is not None else None,
            "_source_file": "41467_2021_22478_MOESM8_ESM.xlsx",
            "_source_sheet": "HM maf",
            "_sha256": sha256,
            "_ingested_at": ingested_at,
            "_doi": "10.1038/s41467-021-22478-y",
        })

    out_dir = data_dir / "gemamx" / "v2" / "bronze"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_file = out_dir / "romero_maf.parquet"

    table = pa.Table.from_pylist(rows)
    pq.write_table(table, out_file)

    return {
        "total_rows": len(rows),
        "source_file": str(xlsx_path),
        "out_file": str(out_file),
    }


def read_moesm9_drivers(gemamx_dir: Path, data_dir: Path) -> dict[str, Any]:
    """Bronze: Lee la hoja 'Driver mutation in HM' de MOESM9 y la guarda en bronze/romero_drivers.parquet."""
    xlsx_path = gemamx_dir / "data" / "romero" / "41467_2021_22478_MOESM9_ESM.xlsx"
    if not xlsx_path.exists():
        raise FileNotFoundError(f"No se encontró MOESM9 en {xlsx_path}")

    sha256 = hashlib.sha256(xlsx_path.read_bytes()).hexdigest()
    ingested_at = datetime.now(timezone.utc).isoformat()

    wb = openpyxl.load_workbook(xlsx_path, data_only=True)
    ws = wb["Driver mutation in HM"]
    raw_rows = list(ws.iter_rows(values_only=True))[2:]  # Fila 2 es cabecera

    rows = []
    for r in raw_rows[1:]:
        if not r or not r[0]:
            continue
        gene = str(r[0]).strip()
        gdna = str(r[1]).strip() if r[1] is not None else ""
        m = re.match(r"chr(\w+):g\.(\d+)([A-Z]+)>([A-Z]+)", gdna)
        if m:
            chrom, pos, ref, alt = m.groups()
            pos_int = int(pos)
        else:
            chrom, pos_int, ref, alt = None, None, None, None

        rows.append({
            "gene": gene,
            "gdna": gdna,
            "chromosome": chrom,
            "start_position": pos_int,
            "ref": ref,
            "alt": alt,
            "cdna": str(r[2]).strip() if r[2] is not None else None,
            "exon": int(r[3]) if r[3] is not None and str(r[3]).isdigit() else None,
            "protein": str(r[4]).strip() if r[4] is not None else None,
            "protein_change_raw": str(r[5]).strip() if r[5] is not None else None,
            "driver_status": str(r[6]).strip() if r[6] is not None else None,
            "consequence": str(r[7]).strip() if r[7] is not None else None,
            "disrupting_driver_prediction": str(r[8]).strip() if r[8] is not None else None,
            "gene_role_mutation": str(r[9]).strip() if r[9] is not None else None,
            "known_variant_reference": str(r[10]).strip() if r[10] is not None else None,
            "_source_file": "41467_2021_22478_MOESM9_ESM.xlsx",
            "_source_sheet": "Driver mutation in HM",
            "_sha256": sha256,
            "_ingested_at": ingested_at,
            "_doi": "10.1038/s41467-021-22478-y",
        })

    out_dir = data_dir / "gemamx" / "v2" / "bronze"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_file = out_dir / "romero_drivers.parquet"

    table = pa.Table.from_pylist(rows)
    pq.write_table(table, out_file)

    return {
        "total_rows": len(rows),
        "source_file": str(xlsx_path),
        "out_file": str(out_file),
    }


def build_driver_lookup(data_dir: Path) -> dict[str, Any]:
    """Silver: Construye el índice de búsqueda de drivers por (gene, chromosome, start_position, ref, alt)."""
    bronze_drivers = data_dir / "gemamx" / "v2" / "bronze" / "romero_drivers.parquet"
    if not bronze_drivers.exists():
        raise FileNotFoundError(f"No existe {bronze_drivers}")

    con = duckdb.connect()
    try:
        query = f"""
            SELECT
                gene,
                chromosome,
                start_position,
                ref,
                alt,
                protein AS protein_hgvsp,
                protein_change_raw,
                driver_status,
                consequence,
                gene_role_mutation,
                known_variant_reference
            FROM read_parquet('{bronze_drivers.as_posix()}')
            WHERE chromosome IS NOT NULL AND start_position IS NOT NULL
        """
        df = con.execute(query).df()
    finally:
        con.close()

    out_dir = data_dir / "gemamx" / "v2" / "silver"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_file = out_dir / "driver_lookup.parquet"

    table = pa.Table.from_pandas(df)
    pq.write_table(table, out_file)

    return {
        "total_drivers": len(df),
        "out_file": str(out_file),
    }


def normalize_romero_mutations(data_dir: Path) -> dict[str, Any]:
    """Silver: Cruza el MAF bronze con el driver lookup y genera silver unificado con ensamble GRCh37."""
    bronze_maf = data_dir / "gemamx" / "v2" / "bronze" / "romero_maf.parquet"
    driver_lookup = data_dir / "gemamx" / "v2" / "silver" / "driver_lookup.parquet"

    if not bronze_maf.exists() or not driver_lookup.exists():
        raise FileNotFoundError("Faltan archivos bronze_maf o driver_lookup")

    con = duckdb.connect()
    try:
        # Cruce exacto preservando todas las 28,114 filas del MAF
        query = f"""
            SELECT
                m.hugo_symbol,
                m.variant_classification,
                m.variant_type,
                m.sample_id,
                m.chromosome,
                m.start_position,
                m.end_position,
                m.strand,
                m.reference_allele,
                m.tumor_seq_allele1,
                m.tumor_seq_allele2,
                m.center AS source_center,
                d.protein_hgvsp,
                d.protein_change_raw,
                d.driver_status,
                d.consequence,
                d.gene_role_mutation,
                d.known_variant_reference,
                'Romero-Cordoba-2021' AS cohorte,
                'GRCh37' AS ensamble
            FROM read_parquet('{bronze_maf.as_posix()}') m
            LEFT JOIN read_parquet('{driver_lookup.as_posix()}') d
                ON m.hugo_symbol = d.gene
               AND m.chromosome = d.chromosome
               AND m.start_position = d.start_position
               AND m.reference_allele = d.ref
               AND m.tumor_seq_allele2 = d.alt
        """
        rows = con.execute(query).fetchall()
        cols = [desc[0] for desc in con.description]
    finally:
        con.close()

    normalized_rows = []
    drivers_matched = 0
    for r in rows:
        row_dict = dict(zip(cols, r))
        # Normalizar protein_change a formato de una letra si viene en protein_hgvsp
        hgvsp = row_dict.get("protein_hgvsp")
        raw = row_dict.get("protein_change_raw")
        norm_pc = _norm_pchange(hgvsp, raw)
        row_dict["protein_change"] = norm_pc
        if norm_pc or row_dict.get("driver_status"):
            drivers_matched += 1
        normalized_rows.append(row_dict)

    # 1. Guardar en v2 silver
    out_dir_v2 = data_dir / "gemamx" / "v2" / "silver"
    out_dir_v2.mkdir(parents=True, exist_ok=True)
    out_file_v2 = out_dir_v2 / "normalized_mutations.parquet"
    pq.write_table(pa.Table.from_pylist(normalized_rows), out_file_v2)

    # 2. Guardar en silver unificado data/gemamx/silver/mutations/
    unified_dir = data_dir / "gemamx" / "silver" / "mutations"
    unified_dir.mkdir(parents=True, exist_ok=True)
    unified_file = unified_dir / "romero_mutations.parquet"
    pq.write_table(pa.Table.from_pylist(normalized_rows), unified_file)

    return {
        "total_rows": len(normalized_rows),
        "drivers_matched": drivers_matched,
        "out_file": str(out_file_v2),
        "unified_file": str(unified_file),
    }


def compute_top_variants(data_dir: Path) -> dict[str, Any]:
    """Gold: Filtra los 15 genes clave, Missense, con protein_change presente y calcula top_variants."""
    silver_file = data_dir / "gemamx" / "v2" / "silver" / "normalized_mutations.parquet"
    if not silver_file.exists():
        raise FileNotFoundError(f"No existe {silver_file}")

    con = duckdb.connect()
    try:
        # Extraer filas del silver
        query = f"""
            SELECT
                hugo_symbol,
                variant_classification,
                protein_change,
                chromosome,
                start_position,
                sample_id
            FROM read_parquet('{silver_file.as_posix()}')
        """
        all_rows = con.execute(query).fetchall()
        cols = [desc[0] for desc in con.description]
    finally:
        con.close()

    # Total pacientes únicos del MAF completo = 134
    total_patients = len(set(r[cols.index("sample_id")] for r in all_rows if r[cols.index("sample_id")]))

    var_patients: dict[tuple[str, str, str, int], set[str]] = {}
    first_seen: dict[tuple[str, str, str, int], int] = {}

    for i, r in enumerate(all_rows):
        row = dict(zip(cols, r))
        gene = str(row["hugo_symbol"]).strip()
        vc = str(row["variant_classification"]).strip()
        pc = row["protein_change"]
        chrom = str(row["chromosome"]).strip()
        pos = int(row["start_position"]) if row["start_position"] is not None else 0
        sid = str(row["sample_id"]).strip()

        if gene in TARGET_15_GENES and vc == "Missense_Mutation" and pc:
            key = (gene, pc, chrom, pos)
            if key not in var_patients:
                var_patients[key] = set()
                first_seen[key] = i
            var_patients[key].add(sid)

    items = []
    for (gene, pc, chrom, pos), sids in var_patients.items():
        freq = len(sids)
        pct = (freq / float(total_patients)) * 100.0
        items.append({
            "hugo_symbol": gene,
            "protein_change": pc,
            "chromosome": chrom,
            "start_position": pos,
            "frequency": freq,
            "cohort_percentage": pct,
            "classification": _classify_variant(pct),
            "first_seen": first_seen[(gene, pc, chrom, pos)],
            "dataset": "Romero-Cordoba 2021 HM",
        })

    # Ordenar por frecuencia descendente y por orden de aparición en el MAF
    items.sort(key=lambda x: (-x["frequency"], x["first_seen"]))

    for rank, item in enumerate(items, start=1):
        item["rank"] = rank
        del item["first_seen"]

    out_dir = data_dir / "gemamx" / "v2" / "gold"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_file = out_dir / "top_variants.parquet"
    out_json = out_dir / "top_variants.json"

    pq.write_table(pa.Table.from_pylist(items), out_file)
    out_json.write_text(json.dumps(items, indent=2, ensure_ascii=False), encoding="utf-8")

    return {
        "top_variants_count": len(items),
        "rank_1": items[0] if items else None,
        "rank_2": items[1] if len(items) > 1 else None,
        "out_file": str(out_file),
    }


def compute_mutation_stats(data_dir: Path) -> dict[str, Any]:
    """Gold: Distribución de variantes por tipo y top 20 genes en el MAF completo."""
    silver_file = data_dir / "gemamx" / "v2" / "silver" / "normalized_mutations.parquet"
    if not silver_file.exists():
        raise FileNotFoundError(f"No existe {silver_file}")

    con = duckdb.connect()
    try:
        query = f"""
            SELECT
                hugo_symbol,
                variant_classification,
                sample_id
            FROM read_parquet('{silver_file.as_posix()}')
        """
        rows = con.execute(query).fetchall()
    finally:
        con.close()

    gene_counts: dict[str, int] = {}
    type_counts: dict[str, int] = {}
    samples: set[str] = set()

    for gene, vc, sid in rows:
        g = str(gene).strip() if gene is not None else "Unknown"
        v = str(vc).strip() if vc is not None else "Unknown"
        s = str(sid).strip() if sid is not None else "Unknown"
        samples.add(s)
        gene_counts[g] = gene_counts.get(g, 0) + 1
        type_counts[v] = type_counts.get(v, 0) + 1

    top_genes = dict(sorted(gene_counts.items(), key=lambda x: -x[1])[:20])
    sorted_types = dict(sorted(type_counts.items(), key=lambda x: -x[1]))

    stats = {
        "gene_distribution": top_genes,
        "type_distribution": sorted_types,
        "total_samples": len(samples),
        "total_mutations": len(rows),
        "dataset": "Romero-Cordoba 2021 HM",
    }

    out_dir = data_dir / "gemamx" / "v2" / "gold"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_json = out_dir / "mutation_stats.json"
    out_json.write_text(json.dumps(stats, indent=2, ensure_ascii=False), encoding="utf-8")

    return stats


def generate_fasta_variants(gemamx_dir: Path, data_dir: Path) -> dict[str, Any]:
    """Gold: Genera los archivos FASTA de WT y MUT validados con cuarentena."""
    top_json = data_dir / "gemamx" / "v2" / "gold" / "top_variants.json"
    if not top_json.exists():
        raise FileNotFoundError(f"No existe {top_json}")

    variants = json.loads(top_json.read_text(encoding="utf-8"))

    # Intentar cargar WT desde fixtures internos o desde GEMAMX_DIR
    fixture_dir = Path(__file__).parent / "fixtures" / "uniprot"
    src_fasta_dir = gemamx_dir / "GEMAMXST" / "v2" / "data" / "fasta"

    wt_seqs: dict[str, tuple[str, str]] = {}  # gene -> (header, sequence)

    for gene, uniprot_id in UNIPROT_MAP.items():
        wt_file = fixture_dir / f"{gene}_WT.fasta"
        if not wt_file.exists() and src_fasta_dir.exists():
            wt_file = src_fasta_dir / f"{gene}_WT.fasta"

        if wt_file.exists():
            lines = wt_file.read_text(encoding="utf-8").splitlines()
            header = lines[0]
            seq = "".join(lines[1:])
            wt_seqs[gene] = (header, seq)

    out_fasta_dir = data_dir / "gemamx" / "v2" / "gold" / "fasta"
    out_fasta_dir.mkdir(parents=True, exist_ok=True)

    quarantine_rows = []
    fasta_index = []

    # Escribir cada WT una sola vez
    written_wts: set[str] = set()
    for gene, (header, seq) in wt_seqs.items():
        wt_path = out_fasta_dir / f"{gene}_WT.fasta"
        wt_path.write_text(f"{header}\n{seq}\n", encoding="utf-8")
        written_wts.add(gene)

    for v in variants:
        gene = v["hugo_symbol"]
        pc = v["protein_change"]
        uniprot_id = UNIPROT_MAP.get(gene, "UNKNOWN")

        m = re.match(r"^([A-Z])(\d+)([A-Z])$", pc)
        if not m:
            quarantine_rows.append({
                "gene": gene,
                "protein_change": pc,
                "reason": "formato_invalido",
                "variant": v,
            })
            continue

        wt_aa, pos_str, mut_aa = m.groups()
        pos = int(pos_str)

        if gene not in wt_seqs:
            quarantine_rows.append({
                "gene": gene,
                "protein_change": pc,
                "reason": "wt_no_disponible",
                "variant": v,
            })
            continue

        header, wt_seq = wt_seqs[gene]
        if pos < 1 or pos > len(wt_seq):
            quarantine_rows.append({
                "gene": gene,
                "protein_change": pc,
                "reason": "posicion_fuera_de_rango",
                "variant": v,
            })
            continue

        actual_wt = wt_seq[pos - 1]
        if actual_wt != wt_aa:
            quarantine_rows.append({
                "gene": gene,
                "protein_change": pc,
                "reason": f"wt_mismatch (esperado {wt_aa}, encontrado {actual_wt})",
                "variant": v,
            })
            continue

        # Generar mutante
        mut_seq = wt_seq[:pos - 1] + mut_aa + wt_seq[pos:]
        mut_filename = f"{gene}_{pc}.fasta"
        mut_path = out_fasta_dir / mut_filename
        mut_header = f">{gene}_{pc} | {uniprot_id} | Mut:{pc}"
        mut_path.write_text(f"{mut_header}\n{mut_seq}\n", encoding="utf-8")

        fasta_index.append({
            "gene": gene,
            "protein_change": pc,
            "uniprot_id": uniprot_id,
            "mutation_pos": pos,
            "wt_fasta": f"v2/data/fasta/{gene}_WT.fasta",
            "mut_fasta": f"v2/data/fasta/{mut_filename}",
        })

    # Guardar índice de FASTA
    index_json = data_dir / "gemamx" / "v2" / "gold" / "fasta_sequences.json"
    index_json.write_text(json.dumps(fasta_index, indent=2, ensure_ascii=False), encoding="utf-8")

    # Guardar cuarentena
    quarantine_dir = data_dir / "gemamx" / "v2" / "quarantine"
    quarantine_dir.mkdir(parents=True, exist_ok=True)
    quarantine_file = quarantine_dir / "fasta_variants.parquet"
    pq.write_table(pa.Table.from_pylist(quarantine_rows), quarantine_file)

    total_files = len(list(out_fasta_dir.glob("*.fasta")))

    return {
        "total_fasta_files": total_files,
        "wt_files": len(written_wts),
        "mut_files": len(fasta_index),
        "quarantined": len(quarantine_rows),
        "index_file": str(index_json),
    }

