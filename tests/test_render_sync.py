"""Pruebas de las utilidades de sincronizacion local -> Render (sin red)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.render_sync import (
    DEFAULT_ITEMS,
    SyncError,
    human_bytes,
    load_env_file,
    mask_url,
    parse_pg_env,
    select_items,
)


def test_parse_pg_env_basic():
    env = parse_pg_env("postgresql://tcga_admin:clave@dpg-abc-a.oregon-postgres.render.com:5432/tcga")
    assert env["PGHOST"] == "dpg-abc-a.oregon-postgres.render.com"
    assert env["PGPORT"] == "5432"
    assert env["PGUSER"] == "tcga_admin"
    assert env["PGPASSWORD"] == "clave"
    assert env["PGDATABASE"] == "tcga"
    assert env["PGSSLMODE"] == "require"


def test_parse_pg_env_decodes_password_and_defaults():
    env = parse_pg_env("postgres://user:p%40ss%3Aword@localhost/tcga", sslmode="disable")
    assert env["PGPASSWORD"] == "p@ss:word"
    assert env["PGPORT"] == "5432"
    assert env["PGSSLMODE"] == "disable"


@pytest.mark.parametrize(
    "url",
    [
        "http://localhost/tcga",
        "postgresql://localhost",
        "postgresql://user@localhost",
    ],
)
def test_parse_pg_env_rejects_invalid(url):
    with pytest.raises(SyncError):
        parse_pg_env(url)


def test_mask_url_hides_password():
    masked = mask_url("postgresql://tcga_admin:secreta@host:5432/tcga")
    assert "secreta" not in masked
    assert masked == "postgresql://tcga_admin:***@host:5432/tcga"


def test_select_items_defaults_and_order():
    assert select_items(None) == list(DEFAULT_ITEMS)
    assert select_items(["case_reports"]) == ["case_reports"]
    assert select_items(["case_reports", "case_reports", "/silver/case_expressions/"]) == [
        "case_reports",
        "silver/case_expressions",
    ]


def test_select_items_rejects_unknown():
    with pytest.raises(SyncError):
        select_items(["bronze"])


def test_load_env_file(tmp_path: Path):
    path = tmp_path / ".env.render"
    path.write_text(
        "# comentario\nRENDER_SSH_HOST=srv-abc@ssh.oregon.render.com\n\n"
        "RENDER_DATABASE_URL='postgresql://u:p@h:5432/db'\n",
        encoding="utf-8",
    )
    values = load_env_file(path)
    assert values["RENDER_SSH_HOST"] == "srv-abc@ssh.oregon.render.com"
    assert values["RENDER_DATABASE_URL"] == "postgresql://u:p@h:5432/db"
    assert load_env_file(tmp_path / "no-existe") == {}


def test_human_bytes():
    assert human_bytes(512) == "512 B"
    assert human_bytes(2 * 1024**3) == "2.0 GB"
