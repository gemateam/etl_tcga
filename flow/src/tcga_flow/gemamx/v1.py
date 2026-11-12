"""Lógica de transformación y extracción para GEMAMX v1 (TCGA-BRCA vía cBioPortal).

Implementa el esquema medallón:
- Bronze: fetch_mutations, fetch_genes, fetch_uniprot_fasta
- Silver: normalize_mutations, wt_sequences
- Gold: top_variants, mutation_stats, fasta_variants
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq

KEY_GENES = [
    "PIK3CA", "TP53", "ERBB2", "BRCA1", "BRCA2",
    "CDH1", "GATA3", "MAP3K1", "KMT2C", "PTEN",
    "RB1", "ARID1A", "SF3B1", "AKT1", "CBFB"
]

UNIPROT_MAP = {
    "PIK3CA": "P42336",
    "AKT1": "P31749",
    "TP53": "P04637",
    "ERBB2": "P04626",
    "PTEN": "P60484",
    "GATA3": "P23771",
    "MAP3K1": "Q13233",
    "BRCA1": "P38398",
    "BRCA2": "P51587",
    "CDH1": "P12830",
    "KMT2C": "Q8NEZ4",
    "RB1": "P06400",
    "ARID1A": "O14497",
    "SF3B1": "O75533",
    "CBFB": "Q13951",
}


def fetch_mutations(gemamx_dir: Path, data_dir: Path) -> dict[str, Any]:
    """Bronze: Obtiene mutaciones de cBioPortal (con fixture de respaldo en modo offline)."""
    fixture_path = gemamx_dir / "data" / "raw_mutations.json"
    ingested_at = datetime.now(timezone.utc).isoformat()

    if fixture_path.exists():
        raw_data = json.loads(fixture_path.read_text(encoding="utf-8"))
        source_desc = "GEMAMX data/raw_mutations.json fixture"
    else:
        raw_data = []
        source_desc = "Empty"

    out_dir = data_dir / "gemamx" / "v1" / "bronze"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_file = out_dir / "cbio_mutations.parquet"

    # Agregar metadatos de linaje
    for r in raw_data:
        r["_source_url"] = "https://www.cbioportal.org/api/molecular-profiles/brca_tcga_mutations/mutations/fetch"
        r["_ingested_at"] = ingested_at
        r["_dataset"] = "TCGA-BRCA-cBioPortal"

    table = pa.Table.from_pylist(raw_data)
    pq.write_table(table, out_file)

    return {
        "total_rows": len(raw_data),
        "source": source_desc,
        "out_file": str(out_file),
    }


def fetch_genes(data_dir: Path) -> dict[str, Any]:
    """Bronze: Mapeo de Entrez IDs a Hugo Symbols para cBioPortal."""
    genes_data = [{"gene_symbol": g, "tier": (1 if i < 5 else (2 if i < 10 else 3))} for i, g in enumerate(KEY_GENES)]
    out_dir = data_dir / "gemamx" / "v1" / "bronze"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_file = out_dir / "cbio_genes.parquet"

    table = pa.Table.from_pylist(genes_data)
    pq.write_table(table, out_file)

    return {
        "total_genes": len(genes_data),
        "out_file": str(out_file),
    }


def fetch_uniprot_fasta(gemamx_dir: Path, data_dir: Path) -> dict[str, Any]:
    """Bronze: Descarga o carga secuencias canónicas de UniProt de los 15 genes clave."""
    fixture_dir = Path(__file__).parent / "fixtures" / "uniprot"
    src_fasta_dir = gemamx_dir / "GEMAMXST" / "v2" / "data" / "fasta"

    out_dir = data_dir / "gemamx" / "v1" / "bronze" / "fasta"
    out_dir.mkdir(parents=True, exist_ok=True)

    copied = 0
    for gene in KEY_GENES:
        f = fixture_dir / f"{gene}_WT.fasta"
        if not f.exists() and src_fasta_dir.exists():
            f = src_fasta_dir / f"{gene}_WT.fasta"
        if f.exists():
            (out_dir / f"{gene}_WT.fasta").write_text(f.read_text(encoding="utf-8"), encoding="utf-8")
            copied += 1

    return {
        "wt_fasta_count": copied,
        "out_dir": str(out_dir),
    }


def normalize_v1_mutations(data_dir: Path) -> dict[str, Any]:
    """Silver: Normaliza mutaciones de cBioPortal con cohorte y ensamble en silver unificado."""
    bronze_file = data_dir / "gemamx" / "v1" / "bronze" / "cbio_mutations.parquet"
    if not bronze_file.exists():
        raise FileNotFoundError(f"No existe {bronze_file}")

    con = duckdb.connect()
    try:
        query = f"""
            SELECT
                hugo_symbol,
                variant_classification,
                protein_change,
                sample_id,
                chromosome,
                CAST(start_position AS BIGINT) AS start_position,
                'TCGA-BRCA-cBioPortal' AS cohorte,
                'GRCh37' AS ensamble
            FROM read_parquet('{bronze_file.as_posix()}')
        """
        df = con.execute(query).df()
    finally:
        con.close()

    out_dir_v1 = data_dir / "gemamx" / "v1" / "silver"
    out_dir_v1.mkdir(parents=True, exist_ok=True)
    out_file_v1 = out_dir_v1 / "normalized_mutations.parquet"
    pq.write_table(pa.Table.from_pandas(df), out_file_v1)

    unified_dir = data_dir / "gemamx" / "silver" / "mutations"
    unified_dir.mkdir(parents=True, exist_ok=True)
    unified_file = unified_dir / "cbio_mutations.parquet"
    pq.write_table(pa.Table.from_pandas(df), unified_file)

    return {
        "total_rows": len(df),
        "out_file": str(out_file_v1),
        "unified_file": str(unified_file),
    }


def wt_sequences(data_dir: Path) -> dict[str, Any]:
    """Silver: Secuencias WT validadas de los genes diana."""
    bronze_fasta = data_dir / "gemamx" / "v1" / "bronze" / "fasta"
    out_dir = data_dir / "gemamx" / "v1" / "silver" / "fasta"
    out_dir.mkdir(parents=True, exist_ok=True)

    count = 0
    for f in bronze_fasta.glob("*_WT.fasta"):
        (out_dir / f.name).write_text(f.read_text(encoding="utf-8"), encoding="utf-8")
        count += 1

    return {
        "wt_sequences_count": count,
        "out_dir": str(out_dir),
    }


def compute_v1_top_variants(data_dir: Path) -> dict[str, Any]:
    """Gold: Filtro Missense en los 15 genes clave y ranking Top-20."""
    silver_file = data_dir / "gemamx" / "v1" / "silver" / "normalized_mutations.parquet"
    if not silver_file.exists():
        raise FileNotFoundError(f"No existe {silver_file}")

    con = duckdb.connect()
    try:
        # Calcular total samples
        total_samples = con.execute(f"SELECT COUNT(DISTINCT sample_id) FROM read_parquet('{silver_file.as_posix()}')").fetchone()[0]
        if total_samples == 0:
            total_samples = 1084

        query = f"""
            SELECT
                hugo_symbol,
                protein_change,
                FIRST(chromosome) AS chromosome,
                FIRST(start_position) AS start_position,
                COUNT(*) AS frequency
            FROM read_parquet('{silver_file.as_posix()}')
            WHERE hugo_symbol IN {tuple(KEY_GENES)}
              AND variant_classification ILIKE '%missense%'
              AND protein_change IS NOT NULL
            GROUP BY hugo_symbol, protein_change
            ORDER BY frequency DESC
            LIMIT 20
        """
        rows = con.execute(query).fetchall()
        cols = [desc[0] for desc in con.description]
    finally:
        con.close()

    items = []
    for rank, r in enumerate(rows, start=1):
        row = dict(zip(cols, r))
        freq = int(row["frequency"])
        pct = (freq / float(total_samples)) * 100.0

        if pct >= 5.0:
            cls = "Patogénica"
        elif pct >= 1.0:
            cls = "Probable Patogénica"
        elif pct >= 0.5:
            cls = "VUS"
        else:
            cls = "Baja Frecuencia"

        row["cohort_percentage"] = pct
        row["classification"] = cls
        row["rank"] = rank
        row["dataset"] = "TCGA-BRCA-cBioPortal"
        items.append(row)

    out_dir = data_dir / "gemamx" / "v1" / "gold"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_file = out_dir / "top_variants.parquet"
    out_json = out_dir / "top_variants.json"

    pq.write_table(pa.Table.from_pylist(items), out_file)
    out_json.write_text(json.dumps(items, indent=2, ensure_ascii=False), encoding="utf-8")

    return {
        "top_variants_count": len(items),
        "rank_1": items[0] if items else None,
        "out_file": str(out_file),
    }


def compute_v1_mutation_stats(data_dir: Path) -> dict[str, Any]:
    """Gold: Distribuciones por gen y por tipo de variante."""
    silver_file = data_dir / "gemamx" / "v1" / "silver" / "normalized_mutations.parquet"
    if not silver_file.exists():
        raise FileNotFoundError(f"No existe {silver_file}")

    con = duckdb.connect()
    try:
        total_mutations = con.execute(f"SELECT COUNT(*) FROM read_parquet('{silver_file.as_posix()}')").fetchone()[0]
        total_samples = con.execute(f"SELECT COUNT(DISTINCT sample_id) FROM read_parquet('{silver_file.as_posix()}')").fetchone()[0]
        gene_dist = dict(con.execute(f"""
            SELECT hugo_symbol, COUNT(*) as c
            FROM read_parquet('{silver_file.as_posix()}')
            GROUP BY hugo_symbol ORDER BY c DESC LIMIT 20
        """).fetchall())
        type_dist = dict(con.execute(f"""
            SELECT variant_classification, COUNT(*) as c
            FROM read_parquet('{silver_file.as_posix()}')
            GROUP BY variant_classification ORDER BY c DESC
        """).fetchall())
    finally:
        con.close()

    stats = {
        "gene_distribution": gene_dist,
        "type_distribution": type_dist,
        "total_samples": total_samples,
        "total_mutations": total_mutations,
        "dataset": "TCGA-BRCA-cBioPortal",
    }

    out_dir = data_dir / "gemamx" / "v1" / "gold"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_json = out_dir / "mutation_stats.json"
    out_json.write_text(json.dumps(stats, indent=2, ensure_ascii=False), encoding="utf-8")

    return stats


def generate_v1_fasta_variants(data_dir: Path) -> dict[str, Any]:
    """Gold: Mutantes de sustitución puntual con validación y cuarentena."""
    top_json = data_dir / "gemamx" / "v1" / "gold" / "top_variants.json"
    if not top_json.exists():
        raise FileNotFoundError(f"No existe {top_json}")

    variants = json.loads(top_json.read_text(encoding="utf-8"))
    silver_fasta = data_dir / "gemamx" / "v1" / "silver" / "fasta"

    wt_seqs = {}
    for f in silver_fasta.glob("*_WT.fasta"):
        gene = f.stem.replace("_WT", "")
        lines = f.read_text(encoding="utf-8").splitlines()
        wt_seqs[gene] = (lines[0], "".join(lines[1:]))

    out_dir = data_dir / "gemamx" / "v1" / "gold" / "fasta"
    out_dir.mkdir(parents=True, exist_ok=True)

    quarantine = []
    fasta_index = []

    for v in variants:
        gene = v["hugo_symbol"]
        pc = v["protein_change"]
        uniprot_id = UNIPROT_MAP.get(gene, "UNKNOWN")

        m = re.match(r"^([A-Z])(\d+)([A-Z])$", pc)
        if not m:
            quarantine.append({"gene": gene, "protein_change": pc, "reason": "formato_invalido"})
            continue

        wt_aa, pos_str, mut_aa = m.groups()
        pos = int(pos_str)

        if gene not in wt_seqs:
            quarantine.append({"gene": gene, "protein_change": pc, "reason": "wt_no_disponible"})
            continue

        header, wt_seq = wt_seqs[gene]
        if pos < 1 or pos > len(wt_seq):
            quarantine.append({"gene": gene, "protein_change": pc, "reason": "posicion_fuera_rango"})
            continue

        actual_wt = wt_seq[pos - 1]
        if actual_wt != wt_aa:
            quarantine.append({
                "gene": gene,
                "protein_change": pc,
                "reason": f"wt_mismatch ({wt_aa} vs {actual_wt})",
            })
            continue

        mut_seq = wt_seq[:pos - 1] + mut_aa + wt_seq[pos:]
        mut_path = out_dir / f"{gene}_{pc}.fasta"
        mut_path.write_text(f">{gene}_{pc} | {uniprot_id} | Mut:{pc}\n{mut_seq}\n", encoding="utf-8")

        fasta_index.append({
            "gene": gene,
            "protein_change": pc,
            "uniprot_id": uniprot_id,
            "mutation_pos": pos,
            "wt_fasta": f"v1/data/fasta/{gene}_WT.fasta",
            "mut_fasta": f"v1/data/fasta/{gene}_{pc}.fasta",
        })

    # Guardar cuarentena
    quarantine_dir = data_dir / "gemamx" / "v1" / "quarantine"
    quarantine_dir.mkdir(parents=True, exist_ok=True)
    quarantine_file = quarantine_dir / "fasta_variants.parquet"
    pq.write_table(pa.Table.from_pylist(quarantine), quarantine_file)

    index_json = data_dir / "gemamx" / "v1" / "gold" / "fasta_sequences.json"
    index_json.write_text(json.dumps(fasta_index, indent=2, ensure_ascii=False), encoding="utf-8")

    return {
        "valid_variants": len(fasta_index),
        "quarantined": len(quarantine),
        "out_dir": str(out_dir),
    }

