"""
export_contract_v3.py — Generador del contrato de datos Versionado V3.
Filtra estrictamente:
  1. Cáncer de Mama (TCGA-BRCA)
  2. Pacientes con etnicidad autorreportada Hispana o Latina

Genera:
  - manifest.json (con hashes SHA-256)
  - somatic_mutations.json
  - clinical_attributes.json
"""

import argparse
import hashlib
import json
import os
import sys
import urllib.parse
import urllib.request
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

# Panel de genes prioritarios en cáncer de mama para GEMAMX
TARGET_GENES = {
    "PIK3CA", "TP53", "AKT1", "CDH1", "PTEN", "ESR1", "GATA3", "MAP3K1", "ERBB2", "BRCA1", "BRCA2"
}

def sha256_file(filepath: Path) -> str:
    h = hashlib.sha256()
    with open(filepath, "rb") as f:
        while chunk := f.read(8192):
            h.update(chunk)
    return h.hexdigest()

def get_hispanic_cases_from_db(db_cursor) -> list[dict]:
    query = """
    SELECT 
        c.case_id,
        c.submitter_id AS case_code,
        c.project_id,
        c.primary_site,
        d.race,
        d.ethnicity,
        d.age_at_index,
        dg.primary_diagnosis,
        dg.ajcc_pathologic_stage,
        dg.tissue_or_organ_of_origin
    FROM silver.cases c
    JOIN silver.cases_demographics d ON c.case_id = d.case_id
    LEFT JOIN silver.cases_diagnoses dg ON c.case_id = dg.case_id
    WHERE c.project_id = 'TCGA-BRCA'
      AND LOWER(TRIM(d.ethnicity)) = 'hispanic or latino'
    """
    db_cursor.execute(query)
    rows = db_cursor.fetchall()
    
    # Consolidar por case_code (evitar duplicados si hay múltiples diagnósticos)
    cases_dict = {}
    for r in rows:
        code = r["case_code"]
        if code not in cases_dict:
            cases_dict[code] = {
                "case_id": r["case_id"],
                "case_code": code,
                "project_id": r["project_id"],
                "primary_site": r["primary_site"] or "Breast",
                "race": (r["race"] or "UNKNOWN").upper(),
                "ethnicity": "HISPANIC OR LATINO",
                "age": r["age_at_index"],
                "histology": r["primary_diagnosis"] or "Infiltrating duct carcinoma, NOS",
                "stage": r["ajcc_pathologic_stage"] or "Not Reported"
            }
    return list(cases_dict.values())

def fetch_gdc_ssms(case_codes: list[str]) -> list[dict]:
    print(f"Consultando mutaciones somáticas abiertas en GDC para {len(case_codes)} casos...")
    url = "https://api.gdc.cancer.gov/ssms"
    filters = {
        "op": "and",
        "content": [
            {
                "op": "in",
                "content": {
                    "field": "occurrence.case.submitter_id",
                    "value": case_codes
                }
            },
            {
                "op": "in",
                "content": {
                    "field": "consequence.transcript.consequence_type",
                    "value": ["missense_variant"]
                }
            }
        ]
    }
    params = {
        "filters": json.dumps(filters),
        "fields": "ssm_id,genomic_dna_change,consequence.transcript.gene.symbol,consequence.transcript.aa_change,occurrence.case.submitter_id,occurrence.case.samples.submitter_id",
        "size": 3000
    }
    
    req = urllib.request.Request(url + "?" + urllib.parse.urlencode(params))
    with urllib.request.urlopen(req, timeout=45) as resp:
        res = json.loads(resp.read().decode())
    
    hits = res.get("data", {}).get("hits", [])
    print(f"Recibidas {len(hits)} mutaciones somáticas missense desde GDC.")
    return hits

def parse_mutations(hits: list[dict], target_cases: set[str]) -> list[dict]:
    mutations = []
    for h in hits:
        # Extraer gen
        gene = None
        aa_change = None
        for c in h.get("consequence", []):
            t = c.get("transcript", {})
            g = t.get("gene", {}).get("symbol")
            aa = t.get("aa_change")
            if g:
                gene = g
            if aa:
                aa_change = aa
            if gene in TARGET_GENES and aa_change:
                break
        
        if not gene or not aa_change:
            continue
            
        # Parsear coordenadas de genomic_dna_change (ej. chr3:g.179234297A>T)
        g_dna = h.get("genomic_dna_change", "")
        chrom = "?"
        pos = 0
        if ":" in g_dna and "g." in g_dna:
            parts = g_dna.split(":")
            chrom = parts[0].replace("chr", "")
            pos_str = parts[1].replace("g.", "")
            digits = "".join(filter(str.isdigit, pos_str))
            if digits:
                pos = int(digits)

        # Extraer casos de ocurrencia
        for occ in h.get("occurrence", []):
            case_id = occ.get("case", {}).get("submitter_id")
            if case_id not in target_cases:
                continue
            
            samples = occ.get("case", {}).get("samples", [])
            sample_id = samples[0].get("submitter_id") if samples else f"{case_id}-01A"
            
            # Normalizar aa_change (remover prefijo p. si viene)
            clean_aa = aa_change.replace("p.", "")
            
            mutations.append({
                "hugo_symbol": gene,
                "variant_classification": "Missense_Mutation",
                "protein_change": clean_aa,
                "sample_id": sample_id,
                "patient_id": case_id,
                "chromosome": chrom,
                "start_position": pos,
                "genomic_dna_change": g_dna,
                "mutation_status": "Somatic"
            })
    return mutations

def build_clinical_attributes(cases: list[dict]) -> list[dict]:
    clinical = []
    for c in cases:
        pid = c["case_code"]
        study = "TCGA-BRCA-HISPANIC"
        clinical.append({"studyId": study, "patientId": pid, "clinicalAttributeId": "PRIMARY_SITE", "value": c["primary_site"]})
        clinical.append({"studyId": study, "patientId": pid, "clinicalAttributeId": "ETHNICITY", "value": c["ethnicity"]})
        clinical.append({"studyId": study, "patientId": pid, "clinicalAttributeId": "RACE", "value": c["race"]})
        if c["age"] is not None:
            clinical.append({"studyId": study, "patientId": pid, "clinicalAttributeId": "AGE", "value": str(c["age"])})
        if c["histology"]:
            clinical.append({"studyId": study, "patientId": pid, "clinicalAttributeId": "HISTOLOGY", "value": c["histology"]})
        if c["stage"]:
            clinical.append({"studyId": study, "patientId": pid, "clinicalAttributeId": "STAGE", "value": c["stage"]})
    return clinical

def export_contract(cases: list[dict], mutations: list[dict], clinical: list[dict], out_dir: Path):
    out_dir.mkdir(parents=True, exist_ok=True)
    
    mut_file = out_dir / "somatic_mutations.json"
    clin_file = out_dir / "clinical_attributes.json"
    cases_file = out_dir / "cohort_cases.json"
    manifest_file = out_dir / "manifest.json"
    
    mut_file.write_text(json.dumps(mutations, indent=2, ensure_ascii=False), encoding="utf-8")
    clin_file.write_text(json.dumps(clinical, indent=2, ensure_ascii=False), encoding="utf-8")
    cases_file.write_text(json.dumps(cases, indent=2, ensure_ascii=False), encoding="utf-8")
    
    manifest = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "contract_version": "1.2.0",
        "project_id": "TCGA-BRCA",
        "cohort_filters": {
            "disease_type": {
                "project_id": "TCGA-BRCA",
                "primary_site": "Breast",
                "description": "Exclusivamente casos de cáncer de mama (TCGA-BRCA)"
            },
            "ethnicity": {
                "attribute": "ethnicity",
                "values_included": ["HISPANIC OR LATINO"],
                "description": "Exclusivamente pacientes con etnicidad hispana o latina autorreportada"
            }
        },
        "gdc_data_release": "Data Release 41.0 - GDC Open Access",
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "total_cases": len(cases),
        "total_mutations": len(mutations),
        "files": {
            "somatic_mutations.json": {
                "sha256": sha256_file(mut_file),
                "records": len(mutations)
            },
            "clinical_attributes.json": {
                "sha256": sha256_file(clin_file),
                "records": len(clinical)
            },
            "cohort_cases.json": {
                "sha256": sha256_file(cases_file),
                "records": len(cases)
            }
        }
    }
    manifest_file.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nContrato V3 exportado exitosamente en: {out_dir}")
    print(f"  - Casos: {len(cases)}")
    print(f"  - Mutaciones: {len(mutations)}")
    print(f"  - Atributos Clínicos: {len(clinical)}")

