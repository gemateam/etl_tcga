from tcga.landing.expressions import ControlRow, chunk_by_size


def _row(file_id: str, size: int) -> ControlRow:
    return ControlRow(
        file_id=file_id,
        project_id="TCGA-BRCA",
        file_name=f"{file_id}.tsv",
        file_size=size,
        expected_md5="0" * 32,
    )


def test_chunk_by_declared_size() -> None:
    rows = [_row("a", 400), _row("b", 400), _row("c", 800)]

    batches = chunk_by_size(rows, max_bytes=1000)

    assert [[r.file_id for r in batch] for batch in batches] == [["a", "b"], ["c"]]


def test_a_file_larger_than_the_limit_gets_its_own_batch() -> None:
    rows = [_row("small", 10), _row("huge", 5000), _row("other", 10)]

    batches = chunk_by_size(rows, max_bytes=1000)

    assert [[r.file_id for r in batch] for batch in batches] == [
        ["small"],
        ["huge"],
        ["other"],
    ]


def test_empty_input_returns_no_batches() -> None:
    assert chunk_by_size([], max_bytes=1000) == []
