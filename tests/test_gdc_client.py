from __future__ import annotations

import io
import tarfile
from pathlib import Path
from typing import Any

import pytest

from tcga.config import Settings
from tcga.gdc.client import GDCClient, GDCError, extract_download_safely, is_tar_gz


def settings(tmp_path: Path, **overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "data_dir": tmp_path,
        "gdc_api_url": "https://api.test",
        "projects": ("TCGA-BRCA",),
        "database_url": "postgresql://unused",
        "duckdb_memory_limit": "1GB",
        "duckdb_threads": 1,
        "download_workers": 1,
        "download_batch_bytes": 1_000_000,
        "http_timeout": 1,
        "http_retries": 3,
        "http_backoff": 1,
        "log_level": "INFO",
    }
    values.update(overrides)
    return Settings(**values)


class FakeResponse:
    def __init__(self, status_code: int, payload: Any = None, chunks: list[bytes] | None = None):
        self.status_code = status_code
        self._payload = payload
        self._chunks = chunks or []
        self.closed = False

    def json(self) -> Any:
        return self._payload

    def iter_content(self, chunk_size: int = 1024) -> Any:
        for chunk in self._chunks:
            yield chunk

    @property
    def text(self) -> str:
        return str(self._payload)

    def close(self) -> None:
        self.closed = True


class FakeSession:
    def __init__(self, responses: list[FakeResponse]):
        self.responses = responses
        self.calls: list[dict[str, Any]] = []

    def request(self, method: str, url: str, **kwargs: Any) -> FakeResponse:
        self.calls.append({"method": method, "url": url, **kwargs})
        if not self.responses:
            raise AssertionError("llamada inesperada")
        return self.responses.pop(0)


def test_case_pagination_uses_from_and_size(tmp_path: Path) -> None:
    class PagedSession(FakeSession):
        def request(self, method: str, url: str, **kwargs: Any) -> FakeResponse:
            params = kwargs["params"]
            start = int(params["from"])
            size = int(params["size"])
            total = 3
            hits = [
                {"case_id": f"case-{i}"}
                for i in range(start, min(start + size, total))
            ]
            return FakeResponse(
                200,
                {"data": {"hits": hits, "pagination": {"total": total}}},
            )

    session = PagedSession([])
    client = GDCClient(settings(tmp_path), session=session)  # type: ignore[arg-type]

    pages = list(client.case_pages("TCGA-BRCA", page_size=2))

    assert [page for page, _ in pages] == [1, 2]
    assert [len(payload["data"]["hits"]) for _, payload in pages] == [2, 1]


def test_retry_on_503_then_success(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("tcga.gdc.client.time.sleep", lambda _seconds: None)
    session = FakeSession(
        [
            FakeResponse(503),
            FakeResponse(200, {"data_release": "Data Release 46.0"}),
        ]
    )
    client = GDCClient(settings(tmp_path), session=session)  # type: ignore[arg-type]

    assert client.status()["data_release"] == "Data Release 46.0"
    assert len(session.calls) == 2


def test_non_retryable_error_fails_fast(tmp_path: Path) -> None:
    session = FakeSession([FakeResponse(400, "bad request")])
    client = GDCClient(settings(tmp_path), session=session)  # type: ignore[arg-type]

    with pytest.raises(GDCError):
        client.status()
    assert len(session.calls) == 1


def test_download_batch_writes_bytes(tmp_path: Path) -> None:
    session = FakeSession([FakeResponse(200, chunks=[b"gene_id\tvalue\n", b"ENSG\t1\n"])])
    client = GDCClient(settings(tmp_path), session=session)  # type: ignore[arg-type]
    target = tmp_path / "download.tsv"

    written = client.download_batch(["file-1"], target)

    assert written == len(b"gene_id\tvalue\nENSG\t1\n")
    assert target.read_bytes() == b"gene_id\tvalue\nENSG\t1\n"


def test_extract_tar_and_detect_gzip(tmp_path: Path) -> None:
    archive = tmp_path / "sample.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        payload = b"gene_id\tvalue\nENSG\t1\n"
        info = tarfile.TarInfo("sample.tsv")
        info.size = len(payload)
        tar.addfile(info, io.BytesIO(payload))

    extracted = extract_download_safely(archive, tmp_path / "staging")

    assert is_tar_gz(archive) is True
    assert extracted["sample.tsv"].read_bytes() == payload


def test_extract_rejects_path_traversal(tmp_path: Path) -> None:
    archive = tmp_path / "evil.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        info = tarfile.TarInfo("../evil.tsv")
        info.size = 3
        tar.addfile(info, io.BytesIO(b"bad"))

    with pytest.raises(GDCError):
        extract_download_safely(archive, tmp_path / "staging")
