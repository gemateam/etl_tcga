"""Lógica de transformación y extracción para GEMAMX v2.1 (Romero-Córdoba 2021 HM con enriquecimiento clínico M5).

Insumos iniciales cubiertos:
- Bronze:
  - moesm8_maf: MAF somático (28 114 variantes, 134 pacientes)
  - moesm9_drivers: Drivers mutacionales (127 variantes)
  - moesm4_clinical: Metadatos clínicos (204 pacientes, edad, subtipo IHC, PAM50, grado SBR)
- Silver:
  - mutations: Cruce MAF x Drivers con nomenclatura de 1 letra normalizada
  - clinical_metadata: Tabla normalizada de metadatos clínicos por paciente
- Gold:
  - mutation_stats: Distribución de variantes por gen y tipo
  - top_variants: 49 variantes missense priorizadas con ranking
  - variant_clinical_summary: Perfiles clínicos por variante (M5b de gemamx_nf)
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import duckdb
import openpyxl
import pyarrow as pa
import pyarrow.parquet as pq

from tcga_flow.gemamx.v2 import (
    AA_MAP,
    TARGET_15_GENES,
)


def _clean_cell(val: Any) -> str | None:
    if val is None:
        return None
    s = str(val).strip()
    if s in {"", "-", ".", "NA", "N/A", "—", "None", "nan"}:
        return None
    return s


def _parse_int(val: Any) -> int | None:
    s = _clean_cell(val)
    if s is None:
        return None
    try:
        return int(float(s))
    except (ValueError, TypeError):
        return None


def _parse_float(val: Any) -> float | None:
    s = _clean_cell(val)
    if s is None:
        return None
    try:
        return float(s)
    except (ValueError, TypeError):
        return None


# ─────────────────────────────────────────────────────────────────────────────
# 1. Bronze: Lector MOESM4 (Features Clínicas)
# ─────────────────────────────────────────────────────────────────────────────

def read_moesm4_clinical(gemamx_dir: Path, data_dir: Path) -> dict[str, Any]:
    moesm4_path = gemamx_dir / "data" / "romero" / "41467_2021_22478_MOESM4_ESM.xlsx"
    if not moesm4_path.exists():
        raise FileNotFoundError(f"No se encontró MOESM4 en {moesm4_path}")

    wb = openpyxl.load_workbook(moesm4_path, data_only=True)
    ws = wb["Features"]

    # Fila 3 contiene los encabezados reales
    headers = [ws.cell(row=3, column=col).value for col in range(1, ws.max_column + 1)]
    header_map = {str(h).strip(): idx + 1 for idx, h in enumerate(headers) if h is not None}

    rows: list[dict[str, Any]] = []
    for row_idx in range(4, ws.max_row + 1):
        sample_col = header_map.get("Tumor Sample Barcode")
        if not sample_col:
            continue
        sample_id = _clean_cell(ws.cell(row=row_idx, column=sample_col).value)
        if not sample_id:
            continue

        def get_val(key: str) -> Any:
            c = header_map.get(key)
            return ws.cell(row=row_idx, column=c).value if c else None

        row_dict = {
            "sample_id": sample_id,
            "mutation_wes": _clean_cell(get_val("Mutation WES")),
            "er_ihc": _clean_cell(get_val("ER  (IHC)")),
            "pr_ihc": _clean_cell(get_val("PR (IHC)")),
            "her2_ihc": _clean_cell(get_val("Her2 (IHC)")),
            "class_ihc": _clean_cell(get_val("Class IHC")),
            "ihc_subtype": _clean_cell(get_val("IHC subtype")),
            "pam50": _clean_cell(get_val("Pam50")),
            "age": _parse_int(get_val("Age")),
            "age_class": _clean_cell(get_val("Age class")),
            "histology": _clean_cell(get_val("Histology")),
            "grade_sbr": _clean_cell(get_val("Grade (SBR)")),
            "tumor_size_mm": _parse_float(get_val("Tumor Size (mm) ")),
            "tnm": _clean_cell(get_val("TNM")),
            "menopause": _clean_cell(get_val("Menopause")),
            "bmi": _parse_float(get_val("BMI")),
            "source": _clean_cell(get_val("Source")),
        }
        row_dict["_sha256"] = hashlib.sha256(json.dumps(row_dict, sort_keys=True).encode()).hexdigest()
        rows.append(row_dict)

    out_dir = data_dir / "gemamx" / "v2_1" / "bronze"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_pq = out_dir / "romero_clinical_raw.parquet"
    table = pa.Table.from_pylist(rows)
    pq.write_table(table, out_pq)

    return {
        "total_samples": len(rows),
        "parquet_file": str(out_pq),
        "columns": table.column_names,
    }


# ─────────────────────────────────────────────────────────────────────────────
# 2. Silver: Normalización de Metadatos Clínicos
# ─────────────────────────────────────────────────────────────────────────────

def normalize_clinical_metadata(data_dir: Path) -> dict[str, Any]:
    in_pq = data_dir / "gemamx" / "v2_1" / "bronze" / "romero_clinical_raw.parquet"
    if not in_pq.exists():
        raise FileNotFoundError(f"No existe {in_pq}")

    con = duckdb.connect()
    con.execute(f"CREATE TABLE raw_clinical AS SELECT * FROM read_parquet('{in_pq.as_posix()}')")

    query = """
    SELECT
        sample_id,
        CASE
            WHEN age IS NOT NULL AND age < 50 THEN 'Younger'
            WHEN age IS NOT NULL AND age >= 50 THEN 'Older'
            ELSE age_class
        END AS age_class,
        age,
        COALESCE(ihc_subtype, 'Unknown') AS ihc_subtype,
        COALESCE(class_ihc, 'Unknown') AS class_ihc,
        COALESCE(pam50, 'Unknown') AS pam50,
        COALESCE(grade_sbr, 'Unknown') AS grade_sbr,
        COALESCE(histology, 'Unknown') AS histology,
        tnm,
        tumor_size_mm,
        menopause,
        bmi,
        'Romero-Cordoba 2021 HM' AS cohorte
    FROM raw_clinical
    ORDER BY sample_id
    """
    df = con.execute(query).df()
    con.close()

    out_dir = data_dir / "gemamx" / "v2_1" / "silver"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_pq = out_dir / "clinical_metadata.parquet"
    table = pa.Table.from_pandas(df)
    pq.write_table(table, out_pq)

    return {
        "normalized_samples": len(df),
        "parquet_file": str(out_pq),
        "columns": table.column_names,
    }


# ─────────────────────────────────────────────────────────────────────────────
# 3. Gold: Perfiles Clínicos por Variante (M5b)
# ─────────────────────────────────────────────────────────────────────────────

def compute_variant_clinical_summary(data_dir: Path) -> dict[str, Any]:
    top_pq = data_dir / "gemamx" / "v2" / "gold" / "top_variants.parquet"
    maf_pq = data_dir / "gemamx" / "silver" / "mutations" / "romero_mutations.parquet"
    clin_pq = data_dir / "gemamx" / "v2_1" / "silver" / "clinical_metadata.parquet"

    if not top_pq.exists() or not maf_pq.exists() or not clin_pq.exists():
        raise FileNotFoundError("Faltan insumos previos (top_variants, romero_mutations o clinical_metadata)")

    top_df = pq.read_table(top_pq).to_pandas()
    maf_df = pq.read_table(maf_pq).to_pandas()
    clin_df = pq.read_table(clin_pq).to_pandas()

    clin_map = clin_df.set_index("sample_id").to_dict(orient="index")

    summaries: list[dict[str, Any]] = []
    flat_rows: list[dict[str, Any]] = []

    for _, row in top_df.iterrows():
        gene = str(row["hugo_symbol"])
        pc = str(row["protein_change"])
        rank = int(row["rank"])
        freq = int(row["frequency"])
        cohort_pct = float(row["cohort_percentage"])
        classification = str(row["classification"])
        vid = f"{gene}_{pc}"

        # Muestras que portan esta variante
        sub = maf_df[(maf_df["hugo_symbol"] == gene) & (maf_df["protein_change"] == pc)]
        samples = sorted(sub["sample_id"].dropna().unique().tolist())

        ages: list[int] = []
        age_classes: Counter[str] = Counter()
        ihc_subtypes: Counter[str] = Counter()
        class_ihc: Counter[str] = Counter()
        pam50: Counter[str] = Counter()
        grades: Counter[str] = Counter()
        histology: Counter[str] = Counter()

        matched_count = 0
        for s in samples:
            meta = clin_map.get(s)
            if not meta:
                continue
            matched_count += 1
            if meta.get("age") is not None:
                ages.append(int(meta["age"]))
            if meta.get("age_class") and meta["age_class"] != "Unknown":
                age_classes[str(meta["age_class"])] += 1
            if meta.get("ihc_subtype") and meta["ihc_subtype"] != "Unknown":
                ihc_subtypes[str(meta["ihc_subtype"])] += 1
            if meta.get("class_ihc") and meta["class_ihc"] != "Unknown":
                class_ihc[str(meta["class_ihc"])] += 1
            if meta.get("pam50") and meta["pam50"] != "Unknown":
                pam50[str(meta["pam50"])] += 1
            if meta.get("grade_sbr") and meta["grade_sbr"] != "Unknown":
                grades[str(meta["grade_sbr"])] += 1
            if meta.get("histology") and meta["histology"] != "Unknown":
                histology[str(meta["histology"])] += 1

        mean_age = round(sum(ages) / len(ages), 2) if ages else None
        top_ihc = ihc_subtypes.most_common(1)[0][0] if ihc_subtypes else "N/A"
        top_pam = pam50.most_common(1)[0][0] if pam50 else "N/A"
        top_grade = grades.most_common(1)[0][0] if grades else "N/A"
        top_age_class = age_classes.most_common(1)[0][0] if age_classes else "N/A"

        summary_entry = {
            "variant_id": vid,
            "hugo_symbol": gene,
            "protein_change": pc,
            "rank": rank,
            "frequency": freq,
            "cohort_percentage": cohort_pct,
            "classification": classification,
            "sample_count": len(samples),
            "matched_clinical_count": matched_count,
            "age": {
                "mean": mean_age,
                "min": min(ages) if ages else None,
                "max": max(ages) if ages else None,
            },
            "highlights": {
                "top_age_class": top_age_class,
                "top_ihc_subtype": top_ihc,
                "top_pam50": top_pam,
                "top_grade": top_grade,
            },
            "samples": samples,
        }
        summaries.append(summary_entry)

        # Fila plana tabular para DuckDB / visualizador KNIME
        flat_rows.append({
            "rank": rank,
            "hugo_symbol": gene,
            "protein_change": pc,
            "frequency": freq,
            "cohort_percentage": cohort_pct,
            "classification": classification,
            "sample_count": len(samples),
            "matched_samples": matched_count,
            "mean_age": mean_age,
            "top_age_class": top_age_class,
            "top_ihc_subtype": top_ihc,
            "top_pam50": top_pam,
            "top_grade_sbr": top_grade,
        })

    out_dir = data_dir / "gemamx" / "v2_1" / "gold"
    out_dir.mkdir(parents=True, exist_ok=True)

    # 1. JSON estructurado compatible con gemamx_nf M5b
    out_json = out_dir / "variant_clinical_summary.json"
    out_json.write_text(json.dumps(summaries, indent=2, ensure_ascii=False), encoding="utf-8")

    # 2. Parquet tabular para DuckDB / RightPanel preview
    out_pq = out_dir / "variant_clinical_summary.parquet"
    pq.write_table(pa.Table.from_pylist(flat_rows), out_pq)

    # 3. CSV exportable
    out_csv = out_dir / "variant_clinical_summary.csv"
    pq.read_table(out_pq).to_pandas().to_csv(out_csv, index=False)

    return {
        "variants_summarized": len(summaries),
        "json_file": str(out_json),
        "parquet_file": str(out_pq),
        "csv_file": str(out_csv),
    }
