from pathlib import Path

from tcga.expression import EXPRESSION_COLUMNS, iter_expression_rows, parse_expression_tsv

FIXTURE = Path(__file__).parent / "fixtures" / "expression_sample.tsv"


def test_parser_ignores_comment_and_control_rows() -> None:
    rows = parse_expression_tsv(FIXTURE)

    assert [row["gene_id"] for row in rows] == [
        "ENSG00000000003.15",
        "ENSG00000000005.6",
        "ENSG00000000419.13",
    ]
    assert all(not row["gene_id"].startswith("N_") for row in rows)
    assert rows[0]["gene_name"] == "TSPAN6"
    assert rows[0]["fpkm_unstranded"] == "17.2434"


def test_parser_keeps_the_nine_original_columns() -> None:
    first = next(iter_expression_rows(FIXTURE))

    assert tuple(first.keys()) == EXPRESSION_COLUMNS


def test_parser_rejects_a_file_without_gene_id_header(tmp_path: Path) -> None:
    invalid = tmp_path / "invalid.tsv"
    invalid.write_text("a\tb\n1\t2\n", encoding="utf-8")

    try:
        list(iter_expression_rows(invalid))
    except ValueError as exc:
        assert "gene_id" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("Se esperaba ValueError")
