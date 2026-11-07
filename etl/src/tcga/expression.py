"""Parser de los TSV de expresión del GDC.

El archivo real trae una línea de comentario inicial (`# gene-model: ...`),
una cabecera y cuatro filas de control cuyo ``gene_id`` empieza por ``N_``.
Este módulo replica el mismo contrato que E05 aplica con DuckDB y se usa
como comprobación de calidad y en las pruebas unitarias.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Iterator

EXPRESSION_COLUMNS: tuple[str, ...] = (
    "gene_id",
    "gene_name",
    "gene_type",
    "unstranded",
    "stranded_first",
    "stranded_second",
    "tpm_unstranded",
    "fpkm_unstranded",
    "fpkm_uq_unstranded",
)


def iter_expression_rows(path: Path) -> Iterator[dict[str, Any]]:
    """Itera las filas de datos, ignorando comentarios y genes de control."""
    header: list[str] | None = None
    with Path(path).open("r", encoding="utf-8", errors="replace", newline="") as handle:
        for raw_line in handle:
            line = raw_line.rstrip("\r\n")
            if not line or line.startswith("#"):
                continue
            fields = line.split("\t")
            if header is None:
                header = [field.strip() for field in fields]
                if "gene_id" not in header:
                    raise ValueError(f"{path}: la cabecera no contiene gene_id")
                continue
            row = {
                column: (fields[index] if index < len(fields) else None)
                for index, column in enumerate(header)
            }
            gene_id = (row.get("gene_id") or "").strip()
            if not gene_id or gene_id.startswith("N_"):
                continue
            row["gene_id"] = gene_id
            yield row


def parse_expression_tsv(path: Path) -> list[dict[str, Any]]:
    """Devuelve las filas de datos de un TSV de expresión."""
    return list(iter_expression_rows(path))
