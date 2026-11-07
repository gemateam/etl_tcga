"""Cliente de la API pública del GDC: paginación, reintentos y descargas."""

from __future__ import annotations

import random
import tarfile
import time
from collections.abc import Iterator, Sequence
from pathlib import Path
from typing import Any

import requests

from ..config import Settings
from ..logging_utils import get_logger
from .fields import CASE_FIELDS, FILE_FIELDS, case_filters, expression_filters

log = get_logger(__name__)

RETRY_STATUS = {429, 500, 502, 503, 504}


class GDCError(RuntimeError):
    pass


class _Retryable(Exception):
    pass


class GDCClient:
    def __init__(self, settings: Settings, session: requests.Session | None = None) -> None:
        self.settings = settings
        self.session = session or requests.Session()
        self.session.headers.update(
            {"User-Agent": "tcga-medallion/1.0 (+https://gdc.cancer.gov)"}
        )

    # ------------------------------------------------------------- plumbing
    def _url(self, path: str) -> str:
        return f"{self.settings.gdc_api_url}/{path.lstrip('/')}"

    def _request(self, method: str, path: str, *, stream: bool = False, **kwargs: Any) -> requests.Response:
        url = self._url(path)
        last: Exception | None = None
        for attempt in range(1, self.settings.http_retries + 1):
            try:
                resp = self.session.request(
                    method,
                    url,
                    timeout=self.settings.http_timeout,
                    stream=stream,
                    **kwargs,
                )
                if resp.status_code in RETRY_STATUS:
                    resp.close()
                    raise _Retryable(f"HTTP {resp.status_code}")
                if resp.status_code >= 400:
                    body = ""
                    try:
                        body = resp.text[:500]
                    finally:
                        resp.close()
                    raise GDCError(f"GDC respondió {resp.status_code} en {path}: {body}")
                return resp
            except (_Retryable, requests.RequestException) as exc:
                last = exc
                if attempt >= self.settings.http_retries:
                    break
                wait = (self.settings.http_backoff ** (attempt - 1)) + random.uniform(0, 0.5)
                log.warning(
                    "Reintento %s/%s de %s en %.1fs: %s",
                    attempt,
                    self.settings.http_retries,
                    path,
                    wait,
                    exc,
                    extra={"event": "gdc_retry"},
                )
                time.sleep(wait)
        raise GDCError(f"No se pudo completar {path} tras {self.settings.http_retries} intentos: {last}")

    def _post_json(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        resp = self._request("POST", path, json=body)
        try:
            return resp.json()
        finally:
            resp.close()

    # ----------------------------------------------------------------- API
    def status(self) -> dict[str, Any]:
        resp = self._request("GET", "status")
        try:
            return resp.json()
        finally:
            resp.close()

    def case_pages(self, project: str, page_size: int = 1000) -> Iterator[tuple[int, dict[str, Any]]]:
        """Pagina /cases con from/size hasta agotar el total informado.

        Se usa GET porque el POST de /cases no expande los campos anidados
        (demographic, diagnoses, exposures, samples); está verificado contra
        la API real.
        """
        import json

        offset = 0
        page = 1
        total: int | None = None
        while True:
            params = {
                "filters": json.dumps(case_filters(project), separators=(",", ":")),
                "fields": ",".join(CASE_FIELDS),
                "format": "JSON",
                "size": page_size,
                "from": offset,
                "sort": "case_id:asc",
            }
            resp = self._request("GET", "cases", params=params)
            try:
                payload = resp.json()
            finally:
                resp.close()
            data = payload.get("data", {})
            hits = data.get("hits", [])
            pagination = data.get("pagination", {})
            total = pagination.get("total", total)
            if not hits:
                break
            yield page, payload
            offset += len(hits)
            if total is not None and offset >= total:
                break
            page += 1
        log.info(
            "Casos extraídos para %s: %s (total API %s)",
            project,
            offset,
            total,
            extra={"event": "cases_extracted", "rows": offset},
        )

    def file_hits(self, project: str, page_size: int = 2000) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        """Devuelve todos los archivos del manifiesto y la paginación final."""
        hits: list[dict[str, Any]] = []
        offset = 0
        pagination: dict[str, Any] = {}
        while True:
            body = {
                "filters": expression_filters(project),
                "fields": ",".join(FILE_FIELDS),
                "format": "JSON",
                "size": page_size,
                "from": offset,
                "sort": "file_id:asc",
            }
            payload = self._post_json("files", body)
            data = payload.get("data", {})
            batch = data.get("hits", [])
            pagination = data.get("pagination", {})
            hits.extend(batch)
            if not batch:
                break
            offset += len(batch)
            total = pagination.get("total")
            if total is not None and offset >= total:
                break
        return hits, pagination

    # ----------------------------------------------------------- descargas
    def download_batch(self, file_ids: Sequence[str], destination: Path) -> int:
        """Descarga un lote por POST /data y devuelve los bytes escritos.

        Para un único id el GDC devuelve el TSV directamente; para varios
        devuelve un .tar.gz. El llamador distingue ambos casos por la firma.
        """
        destination.parent.mkdir(parents=True, exist_ok=True)
        part = destination.with_suffix(destination.suffix + ".part")
        last: Exception | None = None
        for attempt in range(1, self.settings.http_retries + 1):
            try:
                resp = self._request(
                    "POST",
                    "data",
                    stream=True,
                    json={"ids": list(file_ids)},
                )
                size = 0
                try:
                    with part.open("wb") as fh:
                        for chunk in resp.iter_content(chunk_size=1024 * 1024):
                            if chunk:
                                fh.write(chunk)
                                size += len(chunk)
                finally:
                    resp.close()
                if size == 0:
                    raise _Retryable("respuesta vacía")
                part.replace(destination)
                return size
            except (_Retryable, requests.RequestException, OSError) as exc:
                last = exc
                part.unlink(missing_ok=True)
                if attempt >= self.settings.http_retries:
                    break
                wait = (self.settings.http_backoff ** (attempt - 1)) + random.uniform(0, 0.5)
                log.warning(
                    "Reintento de descarga %s/%s en %.1fs: %s",
                    attempt,
                    self.settings.http_retries,
                    wait,
                    exc,
                    extra={"event": "download_retry"},
                )
                time.sleep(wait)
        raise GDCError(f"Descarga fallida tras {self.settings.http_retries} intentos: {last}")


def is_gzip(path: Path) -> bool:
    with path.open("rb") as fh:
        return fh.read(2) == b"\x1f\x8b"


def extract_download_safely(source: Path, staging: Path) -> dict[str, Path]:
    """Extrae un .tar.gz del GDC sin permitir rutas maliciosas.

    Devuelve un mapa nombre_base -> ruta extraída.
    """
    staging.mkdir(parents=True, exist_ok=True)
    extracted: dict[str, Path] = {}
    with tarfile.open(source, mode="r:gz") as tar:
        for member in tar.getmembers():
            if not member.isfile():
                continue
            member_path = Path(member.name)
            if member_path.is_absolute() or ".." in member_path.parts:
                raise GDCError(f"Ruta insegura en el tar del GDC: {member.name}")
            target = staging / member_path.name
            with tar.extractfile(member) as src, target.open("wb") as dst:
                if src is None:
                    continue
                while True:
                    chunk = src.read(1024 * 1024)
                    if not chunk:
                        break
                    dst.write(chunk)
            extracted[member_path.name] = target
    return extracted


def is_tar_gz(path: Path) -> bool:
    if not is_gzip(path):
        return False
    try:
        with tarfile.open(path, mode="r:gz"):
            return True
    except tarfile.TarError:
        return False
