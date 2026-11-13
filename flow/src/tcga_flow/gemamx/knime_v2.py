"""Lógica granular estilo KNIME para GEMAMX v2 (Romero-Córdoba 2021 HM).

Desglosa el pipeline en pasos atómicos tal como en el diagrama KNIME:
Bloque 1 · Lectura y cruce:
- moesm8_reader: Lee MOESM8 hoja 'HM maf' (Excel Reader)
- moesm8_key: String Manipulator - genera clave compuesta (gene#chr#pos#ref#alt)
- moesm9_reader: Lee MOESM9 hoja 'Driver mutation in HM' (Excel Reader)
- moesm9_regex: Regex Extractor (chr·pos·ref·alt) desde gdna
- moesm9_key: String Manipulator - genera clave compuesta drivers
- joiner: Joiner (Left Outer Join) MAF x Drivers por clave compuesta

Bloque 2 · Normalización:
- regex_extractor_prot: Regex Extractor p.His1047Arg
- cell_replacer_aa: Cell Replacer (3 letras -> 1 letra aminoácido)
- string_manip_pchange: String Manipulator H1047R final

Bloque 3 · Priorización:
- ref_row_filter_genes: Ref. Row Filter (15 genes clave)
- row_filter_missense: Row Filter (solo missense)
- row_filter_resolved: Row Filter (cambio proteico resuelto / not null)
- groupby_variants: GroupBy (gen + cambio) -> conteo de pacientes únicos
- math_formula_pct: Math Formula (freq ÷ 134 × 100)
- rule_engine_class: Rule Engine (5% Patogénica, 1% Probable Patogénica, 0.5% VUS, Baja Frecuencia)
- sorter_top_variants: Sorter -> Parquet / JSON (49 top variants ordenadas)
- mutation_stats: Distribución de mutaciones por gen y tipo
- fasta_variant_sequences: variant_sequences WT y mutantes .fasta con cuarentena
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

from .v2 import resolve_romero_file

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


def _v2_path(data_dir: Path, step: str) -> Path:
    out = data_dir / "gemamx" / "v2" / "steps" / f"{step}.parquet"
    out.parent.mkdir(parents=True, exist_ok=True)
    return out


# =====================================================================
# BLOQUE 1 · LECTURA Y CRUCE
# =====================================================================

def step_moesm8_reader(gemamx_dir: Path, data_dir: Path) -> dict[str, Any]:
    """1.1 Excel Reader: MOESM8 (MAF)."""
    xlsx_path = resolve_romero_file("41467_2021_22478_MOESM8_ESM.xlsx", data_dir=data_dir, gemamx_dir=gemamx_dir)
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

        rows.append({
            "idx": i - 2,
            "hugo_symbol": str(r[0]).strip() if r[0] is not None else "Unknown",
            "center": str(r[1]).strip() if r[1] is not None else None,
            "ncbi_build": str(r[2]).strip() if r[2] is not None else "37",
            "chromosome": str(r[3]).strip() if r[3] is not None else None,
            "start_position": start_pos,
            "end_position": int(r[5]) if r[5] is not None and str(r[5]).isdigit() else None,
            "strand": str(r[6]).strip() if r[6] is not None else "+",
            "variant_classification": str(r[7]).strip() if r[7] is not None else "Unknown",
            "variant_type": str(r[8]).strip() if r[8] is not None else None,
            "reference_allele": str(r[9]).strip() if r[9] is not None else None,
            "tumor_seq_allele1": str(r[10]).strip() if r[10] is not None else None,
            "tumor_seq_allele2": str(r[11]).strip() if r[11] is not None else None,
            "sample_id": str(r[12]).strip() if r[12] is not None else None,
            "_source_file": "41467_2021_22478_MOESM8_ESM.xlsx",
            "_sha256": sha256,
            "_ingested_at": ingested_at,
        })

    out_file = _v2_path(data_dir, "moesm8_raw")
    pq.write_table(pa.Table.from_pylist(rows), out_file)
    # Guardar también en bronze estándar
    bronze_file = data_dir / "gemamx" / "v2" / "bronze" / "romero_maf.parquet"
    bronze_file.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(pa.Table.from_pylist(rows), bronze_file)

    return {"total_rows": len(rows), "out_file": str(out_file)}


def step_moesm8_key(data_dir: Path) -> dict[str, Any]:
    """1.2 String Manipulator: Clave compuesta en MOESM8 (gen#chr#pos#ref#alt)."""
    src = _v2_path(data_dir, "moesm8_raw")
    con = duckdb.connect()
    try:
        query = f"""
            SELECT *,
                CONCAT_WS('#', hugo_symbol, chromosome, CAST(start_position AS VARCHAR), reference_allele, tumor_seq_allele2) AS join_key
            FROM read_parquet('{src.as_posix()}')
        """
        df = con.execute(query).df()
    finally:
        con.close()

    out_file = _v2_path(data_dir, "moesm8_keyed")
    pq.write_table(pa.Table.from_pandas(df), out_file)
    return {"total_rows": len(df), "out_file": str(out_file)}


def step_moesm9_reader(gemamx_dir: Path, data_dir: Path) -> dict[str, Any]:
    """1.3 Excel Reader: MOESM9 (drivers)."""
    xlsx_path = resolve_romero_file("41467_2021_22478_MOESM9_ESM.xlsx", data_dir=data_dir, gemamx_dir=gemamx_dir)
    if not xlsx_path.exists():
        raise FileNotFoundError(f"No se encontró MOESM9 en {xlsx_path}")

    wb = openpyxl.load_workbook(xlsx_path, data_only=True)
    ws = wb["Driver mutation in HM"]
    raw_rows = list(ws.iter_rows(values_only=True))[2:]

    rows = []
    for r in raw_rows[1:]:
        if not r or not r[0]:
            continue
        rows.append({
            "gene": str(r[0]).strip(),
            "gdna": str(r[1]).strip() if r[1] is not None else "",
            "cdna": str(r[2]).strip() if r[2] is not None else None,
            "exon": int(r[3]) if r[3] is not None and str(r[3]).isdigit() else None,
            "protein": str(r[4]).strip() if r[4] is not None else None,
            "protein_change_raw": str(r[5]).strip() if r[5] is not None else None,
            "driver_status": str(r[6]).strip() if r[6] is not None else None,
            "consequence": str(r[7]).strip() if r[7] is not None else None,
            "gene_role_mutation": str(r[9]).strip() if r[9] is not None else None,
            "known_variant_reference": str(r[10]).strip() if r[10] is not None else None,
        })

    out_file = _v2_path(data_dir, "moesm9_raw")
    pq.write_table(pa.Table.from_pylist(rows), out_file)
    # Guardar también en bronze estándar
    bronze_file = data_dir / "gemamx" / "v2" / "bronze" / "romero_drivers.parquet"
    bronze_file.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(pa.Table.from_pylist(rows), bronze_file)

    return {"total_rows": len(rows), "out_file": str(out_file)}


def step_moesm9_regex_extract(data_dir: Path) -> dict[str, Any]:
    """1.4 Regex Extractor: chr·pos·ref·alt desde gdna en MOESM9."""
    src = _v2_path(data_dir, "moesm9_raw")
    con = duckdb.connect()
    try:
        rows = con.execute(f"SELECT * FROM read_parquet('{src.as_posix()}')").fetchall()
        cols = [desc[0] for desc in con.description]
    finally:
        con.close()

    parsed = []
    for r in rows:
        d = dict(zip(cols, r))
        gdna = d.get("gdna", "")
        m = re.match(r"chr(\w+):g\.(\d+)([A-Z]+)>([A-Z]+)", gdna)
        if m:
            chrom, pos, ref, alt = m.groups()
            d["chromosome"] = chrom
            d["start_position"] = int(pos)
            d["reference_allele"] = ref
            d["tumor_seq_allele2"] = alt
            parsed.append(d)

    out_file = _v2_path(data_dir, "moesm9_parsed")
    pq.write_table(pa.Table.from_pylist(parsed), out_file)
    return {"total_extracted": len(parsed), "out_file": str(out_file)}


def step_moesm9_key(data_dir: Path) -> dict[str, Any]:
    """1.5 String Manipulator: Clave compuesta en drivers (gene#chr#pos#ref#alt)."""
    src = _v2_path(data_dir, "moesm9_parsed")
    con = duckdb.connect()
    try:
        query = f"""
            SELECT *,
                CONCAT_WS('#', gene, chromosome, CAST(start_position AS VARCHAR), reference_allele, tumor_seq_allele2) AS join_key
            FROM read_parquet('{src.as_posix()}')
        """
        df = con.execute(query).df()
    finally:
        con.close()

    out_file = _v2_path(data_dir, "moesm9_keyed")
    pq.write_table(pa.Table.from_pandas(df), out_file)
    return {"total_rows": len(df), "out_file": str(out_file)}


def step_joiner_maf_drivers(data_dir: Path) -> dict[str, Any]:
    """1.6 Joiner: Left Outer Join (MAF × drivers por join_key)."""
    maf = _v2_path(data_dir, "moesm8_keyed")
    drv = _v2_path(data_dir, "moesm9_keyed")

    con = duckdb.connect()
    try:
        query = f"""
            SELECT
                m.idx,
                m.hugo_symbol,
                m.center AS source_center,
                m.chromosome,
                m.start_position,
                m.end_position,
                m.strand,
                m.variant_classification,
                m.variant_type,
                m.reference_allele,
                m.tumor_seq_allele1,
                m.tumor_seq_allele2,
                m.sample_id,
                d.protein AS protein_hgvsp,
                d.protein_change_raw,
                d.driver_status,
                d.consequence,
                d.gene_role_mutation,
                d.known_variant_reference,
                'Romero-Cordoba-2021' AS cohorte,
                'GRCh37' AS ensamble
            FROM read_parquet('{maf.as_posix()}') m
            LEFT JOIN read_parquet('{drv.as_posix()}') d
                ON m.join_key = d.join_key
            ORDER BY m.idx ASC
        """
        df = con.execute(query).df()
    finally:
        con.close()

    out_file = _v2_path(data_dir, "maf_joined")
    pq.write_table(pa.Table.from_pandas(df), out_file)

    drivers_matched = int((df["driver_status"].notna()).sum())
    return {"total_rows": len(df), "drivers_matched": drivers_matched, "out_file": str(out_file)}


# =====================================================================
# BLOQUE 2 · NORMALIZACIÓN
# =====================================================================

def step_regex_extractor_prot(data_dir: Path) -> dict[str, Any]:
    """2.1 Regex Extractor: Extrae notación p.His1047Arg desde protein_hgvsp."""
    src = _v2_path(data_dir, "maf_joined")
    con = duckdb.connect()
    try:
        query = f"""
            SELECT *,
                REGEXP_EXTRACT(protein_hgvsp, '^p\\.(.*)$', 1) AS raw_prot_change
            FROM read_parquet('{src.as_posix()}')
        """
        df = con.execute(query).df()
    finally:
        con.close()

    out_file = _v2_path(data_dir, "prot_extracted")
    pq.write_table(pa.Table.from_pandas(df), out_file)
    return {"total_rows": len(df), "out_file": str(out_file)}


def step_cell_replacer_aa(data_dir: Path) -> dict[str, Any]:
    """2.2 Cell Replacer: Reemplaza códigos de 3 letras a 1 letra (His->H, Arg->R)."""
    src = _v2_path(data_dir, "prot_extracted")
    con = duckdb.connect()
    try:
        rows = con.execute(f"SELECT * FROM read_parquet('{src.as_posix()}')").fetchall()
        cols = [desc[0] for desc in con.description]
    finally:
        con.close()

    replaced = []
    for r in rows:
        d = dict(zip(cols, r))
        val = d.get("raw_prot_change")
        if val:
            s = str(val).strip()
            for three, one in AA_MAP.items():
                s = s.replace(three, one)
            d["protein_change_1letter"] = s
        else:
            d["protein_change_1letter"] = None
        replaced.append(d)

    out_file = _v2_path(data_dir, "aa_replaced")
    pq.write_table(pa.Table.from_pylist(replaced), out_file)
    return {"total_rows": len(replaced), "out_file": str(out_file)}


def step_string_manip_pchange(data_dir: Path) -> dict[str, Any]:
    """2.3 String Manipulator: protein_change limpio (e.g. H1047R) y consolida silver."""
    src = _v2_path(data_dir, "aa_replaced")
    con = duckdb.connect()
    try:
        query = f"""
            SELECT
                hugo_symbol,
                variant_classification,
                variant_type,
                sample_id,
                chromosome,
                start_position,
                end_position,
                strand,
                reference_allele,
                tumor_seq_allele1,
                tumor_seq_allele2,
                source_center,
                protein_hgvsp,
                protein_change_raw,
                protein_change_1letter AS protein_change,
                driver_status,
                consequence,
                gene_role_mutation,
                known_variant_reference,
                cohorte,
                ensamble,
                idx
            FROM read_parquet('{src.as_posix()}')
            ORDER BY idx ASC
        """
        df = con.execute(query).df()
    finally:
        con.close()

    out_file = _v2_path(data_dir, "silver_normalized")
    pq.write_table(pa.Table.from_pandas(df), out_file)

    # Copiar a silver unificado y a silver estándar
    silver_std = data_dir / "gemamx" / "v2" / "silver" / "normalized_mutations.parquet"
    silver_std.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(pa.Table.from_pandas(df), silver_std)

    unified = data_dir / "gemamx" / "silver" / "mutations" / "romero_mutations.parquet"
    unified.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(pa.Table.from_pandas(df), unified)

    return {"total_rows": len(df), "out_file": str(out_file)}


# =====================================================================
# BLOQUE 3 · PRIORIZACIÓN
# =====================================================================

def step_ref_row_filter_genes(data_dir: Path) -> dict[str, Any]:
    """3.1 Ref. Row Filter: Filtra por los 15 genes clave."""
    src = _v2_path(data_dir, "silver_normalized")
    con = duckdb.connect()
    try:
        query = f"""
            SELECT * FROM read_parquet('{src.as_posix()}')
            WHERE hugo_symbol IN {tuple(TARGET_15_GENES)}
        """
        df = con.execute(query).df()
    finally:
        con.close()

    out_file = _v2_path(data_dir, "filter_15_genes")
    pq.write_table(pa.Table.from_pandas(df), out_file)
    return {"retained_rows": len(df), "out_file": str(out_file)}


def step_row_filter_missense(data_dir: Path) -> dict[str, Any]:
    """3.2 Row Filter: Solo variantes missense (Missense_Mutation)."""
    src = _v2_path(data_dir, "filter_15_genes")
    con = duckdb.connect()
    try:
        query = f"""
            SELECT * FROM read_parquet('{src.as_posix()}')
            WHERE variant_classification = 'Missense_Mutation'
        """
        df = con.execute(query).df()
    finally:
        con.close()

    out_file = _v2_path(data_dir, "filter_missense")
    pq.write_table(pa.Table.from_pandas(df), out_file)
    return {"retained_rows": len(df), "out_file": str(out_file)}


def step_row_filter_resolved(data_dir: Path) -> dict[str, Any]:
    """3.3 Row Filter: Cambio proteico resuelto (protein_change IS NOT NULL)."""
    src = _v2_path(data_dir, "filter_missense")
    con = duckdb.connect()
    try:
        query = f"""
            SELECT * FROM read_parquet('{src.as_posix()}')
            WHERE protein_change IS NOT NULL AND TRIM(protein_change) != ''
        """
        df = con.execute(query).df()
    finally:
        con.close()

    out_file = _v2_path(data_dir, "filter_resolved")
    pq.write_table(pa.Table.from_pandas(df), out_file)
    return {"retained_rows": len(df), "out_file": str(out_file)}


def step_groupby_variants(data_dir: Path) -> dict[str, Any]:
    """3.4 GroupBy: gen + cambio proteico -> conteo de pacientes únicos y primer avistamiento."""
    src = _v2_path(data_dir, "filter_resolved")
    con = duckdb.connect()
    try:
        query = f"""
            SELECT
                hugo_symbol,
                protein_change,
                FIRST(chromosome) AS chromosome,
                FIRST(start_position) AS start_position,
                COUNT(DISTINCT sample_id) AS frequency,
                MIN(idx) AS first_seen
            FROM read_parquet('{src.as_posix()}')
            GROUP BY hugo_symbol, protein_change
            ORDER BY frequency DESC, first_seen ASC
        """
        df = con.execute(query).df()
    finally:
        con.close()

    out_file = _v2_path(data_dir, "grouped_variants")
    pq.write_table(pa.Table.from_pandas(df), out_file)
    return {"unique_variants": len(df), "out_file": str(out_file)}


def step_math_formula_pct(data_dir: Path) -> dict[str, Any]:
    """3.5 Math Formula: (frequency ÷ 134 × 100)."""
    src = _v2_path(data_dir, "grouped_variants")
    con = duckdb.connect()
    try:
        query = f"""
            SELECT
                *,
                (CAST(frequency AS DOUBLE) / 134.0) * 100.0 AS cohort_percentage
            FROM read_parquet('{src.as_posix()}')
        """
        df = con.execute(query).df()
    finally:
        con.close()

    out_file = _v2_path(data_dir, "math_pct")
    pq.write_table(pa.Table.from_pandas(df), out_file)
    return {"total_rows": len(df), "out_file": str(out_file)}


def step_rule_engine_class(data_dir: Path) -> dict[str, Any]:
    """3.6 Rule Engine: Clasificación (≥5% Patogénica, ≥1% Prob. Patogénica, ≥0.5% VUS, <0.5% Baja Frecuencia)."""
    src = _v2_path(data_dir, "math_pct")
    con = duckdb.connect()
    try:
        query = f"""
            SELECT
                *,
                CASE
                    WHEN cohort_percentage >= 5.0 THEN 'Patogénica'
                    WHEN cohort_percentage >= 1.0 THEN 'Probable Patogénica'
                    WHEN cohort_percentage >= 0.5 THEN 'VUS'
                    ELSE 'Baja Frecuencia'
                END AS classification
            FROM read_parquet('{src.as_posix()}')
        """
        df = con.execute(query).df()
    finally:
        con.close()

    out_file = _v2_path(data_dir, "rule_classified")
    pq.write_table(pa.Table.from_pandas(df), out_file)
    return {"total_rows": len(df), "out_file": str(out_file)}


def step_sorter_top_variants(data_dir: Path) -> dict[str, Any]:
    """3.7 Sorter -> CSV / Parquet: top_variants · 49 ordenadas con rango asignado."""
    src = _v2_path(data_dir, "rule_classified")
    con = duckdb.connect()
    try:
        query = f"""
            SELECT
                hugo_symbol,
                protein_change,
                chromosome,
                start_position,
                frequency,
                cohort_percentage,
                classification,
                'Romero-Cordoba 2021 HM' AS dataset,
                ROW_NUMBER() OVER (ORDER BY frequency DESC, first_seen ASC) AS rank
            FROM read_parquet('{src.as_posix()}')
            ORDER BY rank ASC
        """
        df = con.execute(query).df()
    finally:
        con.close()

    # Guardar en gold
    out_dir = data_dir / "gemamx" / "v2" / "gold"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_pq = out_dir / "top_variants.parquet"
    out_json = out_dir / "top_variants.json"

    pq.write_table(pa.Table.from_pandas(df), out_pq)
    records = df.to_dict(orient="records")
    out_json.write_text(json.dumps(records, indent=2, ensure_ascii=False), encoding="utf-8")

    return {
        "top_variants_count": len(df),
        "rank_1": records[0] if records else None,
        "rank_2": records[1] if len(records) > 1 else None,
        "out_file": str(out_pq),
    }


def step_mutation_stats(data_dir: Path) -> dict[str, Any]:
    """3.8 mutation_stats: Distribuciones por gen y por tipo de variante en el MAF completo."""
    from .v2 import compute_mutation_stats
    return compute_mutation_stats(data_dir)


def step_variant_sequences(gemamx_dir: Path, data_dir: Path) -> dict[str, Any]:
    """3.9 variant_sequences: WT y mutantes .fasta validados con cuarentena."""
    from .v2 import generate_fasta_variants
    return generate_fasta_variants(gemamx_dir, data_dir)

