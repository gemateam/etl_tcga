#!/usr/bin/env python3
"""Sincroniza los datos procesados en local con el reporteador de Render.

El ETL y el ejecutor visual `flow` se ejecutan en el PC; en Render solo vive
`tcga-web`. Este script sube a Render lo que la web necesita:

    db     vuelca PostgreSQL en local y lo restaura en la base de Render.
    files  sube por SSH los Parquet de expresion y los reportes al disco /data.
    all    ambas cosas, en ese orden.

Configuracion (variables de entorno o un fichero `.env.render` en la raiz):

    RENDER_SSH_HOST       srv-xxxxxxxx@ssh.<region>.render.com
    RENDER_DATABASE_URL   URL *externa* de PostgreSQL de Render
    RENDER_PG_SSLMODE     sslmode de libpq (por defecto: require)
    RENDER_DATA_DIR       destino en el disco (por defecto: /data)

Ejemplos:

    python scripts/render_sync.py db --dry-run
    python scripts/render_sync.py db
    python scripts/render_sync.py files
    python scripts/render_sync.py all --only case_reports
    python scripts/render_sync.py files --dry-run

En el plan free de Render solo se puede usar `db` (no hay disco ni SSH). El
modo `files` esta pensado para un plan pagado con disco persistente.

Requisitos en el PC: Docker Desktop con el contenedor `tcga-postgres`; el
cliente OpenSSH (`ssh`, `tar`) solo hace falta para `files`.
"""

from __future__ import annotations

import argparse
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import unquote, urlsplit

REPO_ROOT = Path(__file__).resolve().parents[1]

DEFAULT_DATA_DIR = REPO_ROOT / "data"
DEFAULT_REMOTE_DIR = "/data"
DEFAULT_CONTAINER = "tcga-postgres"
DEFAULT_LOCAL_DB = "tcga"
DEFAULT_SSLMODE = "require"
# Cliente de pg_restore. Render crea PostgreSQL 18; el cliente 18 restaura
# volcados hechos con pg_dump 16 (el PostgreSQL local).
PG_IMAGE = "postgres:18"

# Solo lo que consume `tcga-web` desde el disco (no se suben landing, bronze
# ni los Parquet de gold/GEMAMX: la web los consulta en PostgreSQL).
DEFAULT_ITEMS = (
    "silver/case_expressions",
    "case_reports",
)

# Tablas que se comparan tras restaurar la base de datos.
COUNT_QUERIES = (
    ("silver.cases", "SELECT count(*) FROM silver.cases"),
    ("silver.samples", "SELECT count(*) FROM silver.samples"),
    ("silver.mutations", "SELECT count(*) FROM silver.mutations"),
    ("gold.cx_pivot", "SELECT count(*) FROM gold.cx_pivot"),
    ("ops.pipeline_runs", "SELECT count(*) FROM ops.pipeline_runs"),
)


class SyncError(RuntimeError):
    """Fallo controlado: se muestra el mensaje y se sale con codigo 1."""


# --------------------------------------------------------------------- util


def info(message: str) -> None:
    print(f"[..] {message}", flush=True)


def ok(message: str) -> None:
    print(f"[ok] {message}", flush=True)


def warn(message: str) -> None:
    print(f"[!!] {message}", flush=True)


def fail(message: str) -> None:
    print(f"[xx] {message}", flush=True)


def human_bytes(value: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or unit == "TB":
            return f"{value:.1f} {unit}" if unit != "B" else f"{int(value)} B"
        value /= 1024
    return f"{value:.1f} TB"


def dir_size(path: Path) -> int:
    if path.is_file():
        return path.stat().st_size
    total = 0
    for item in path.rglob("*"):
        if item.is_file():
            total += item.stat().st_size
    return total


def mask_url(url: str) -> str:
    """Devuelve la URL sin contrasena, para poder imprimirla."""
    try:
        parts = urlsplit(url)
    except ValueError:
        return "<url invalida>"
    user = unquote(parts.username or "")
    host = parts.hostname or "?"
    port = f":{parts.port}" if parts.port else ""
    database = parts.path.lstrip("/")
    credentials = f"{user}:***@" if user else ""
    return f"{parts.scheme}://{credentials}{host}{port}/{database}"


def parse_pg_env(url: str, sslmode: str = DEFAULT_SSLMODE) -> dict[str, str]:
    """Convierte una URL de PostgreSQL en variables libpq (PGHOST, ...)."""
    parts = urlsplit(url)
    if parts.scheme not in ("postgres", "postgresql"):
        raise SyncError(f"URL de PostgreSQL no valida: esquema {parts.scheme!r}")
    if not parts.hostname:
        raise SyncError("La URL de PostgreSQL no incluye host")
    database = parts.path.lstrip("/")
    if not database:
        raise SyncError("La URL de PostgreSQL no incluye nombre de base de datos")
    return {
        "PGHOST": parts.hostname,
        "PGPORT": str(parts.port or 5432),
        "PGUSER": unquote(parts.username or ""),
        "PGPASSWORD": unquote(parts.password or ""),
        "PGDATABASE": database,
        "PGSSLMODE": sslmode,
        "PGCONNECT_TIMEOUT": "30",
    }


def select_items(requested: list[str] | None) -> list[str]:
    """Normaliza `--only` y valida que no se suba nada fuera del catalogo."""
    if not requested:
        return list(DEFAULT_ITEMS)
    selected: list[str] = []
    for raw in requested:
        item = raw.replace("\\", "/").strip("/ ")
        if item not in DEFAULT_ITEMS:
            raise SyncError(
                f"Elemento no permitido: {raw!r}. Opciones: {', '.join(DEFAULT_ITEMS)}"
            )
        if item not in selected:
            selected.append(item)
    return selected


def load_env_file(path: Path) -> dict[str, str]:
    """Lee un fichero KEY=VALUE simple (sin expansion de variables)."""
    if not path.exists():
        return {}
    values: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def run(cmd: list[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
    """Ejecuta un comando mostrando su salida en la consola."""
    result = subprocess.run(cmd, check=False, text=True, encoding="utf-8", errors="replace")
    if check and result.returncode != 0:
        raise SyncError(f"Fallo el comando: {' '.join(cmd)} (codigo {result.returncode})")
    return result


def capture(cmd: list[str], *, check: bool = True) -> str:
    result = subprocess.run(
        cmd,
        check=False,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
    )
    if check and result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        raise SyncError(f"Fallo el comando: {' '.join(cmd)}\n{detail}")
    return result.stdout.strip()


def require_binary(name: str) -> None:
    if shutil.which(name) is None:
        raise SyncError(f"No se encontro `{name}` en el PATH")


# --------------------------------------------------------------- configuracion


@dataclass
class Config:
    ssh_host: str | None = None
    database_url: str | None = None
    remote_dir: str = DEFAULT_REMOTE_DIR
    data_dir: Path = DEFAULT_DATA_DIR
    container: str = DEFAULT_CONTAINER
    local_db: str = DEFAULT_LOCAL_DB
    local_user: str | None = None
    sslmode: str = DEFAULT_SSLMODE
    dry_run: bool = False
    assume_yes: bool = False
    items: list[str] = field(default_factory=lambda: list(DEFAULT_ITEMS))
    keep_dump: Path | None = None

    def ssh_options(self) -> list[str]:
        # accept-new evita el prompt de host desconocido; la huella de Render
        # esta publicada en https://render.com/docs/ssh
        return ["-o", "StrictHostKeyChecking=accept-new"]


def build_config(args: argparse.Namespace) -> Config:
    env_file = Path(args.env_file).expanduser() if args.env_file else REPO_ROOT / ".env.render"
    values = load_env_file(env_file)

    def pick(cli_value: str | None, env_key: str) -> str | None:
        return cli_value or os.getenv(env_key) or values.get(env_key)

    return Config(
        ssh_host=pick(args.ssh_host, "RENDER_SSH_HOST"),
        database_url=pick(args.database_url, "RENDER_DATABASE_URL"),
        remote_dir=(pick(args.remote_dir, "RENDER_DATA_DIR") or DEFAULT_REMOTE_DIR).rstrip("/"),
        data_dir=Path(args.data_dir).expanduser().resolve() if args.data_dir else DEFAULT_DATA_DIR,
        container=args.container,
        local_db=args.local_db,
        local_user=args.local_user,
        sslmode=pick(args.sslmode, "RENDER_PG_SSLMODE") or DEFAULT_SSLMODE,
        dry_run=args.dry_run,
        assume_yes=args.yes,
        items=select_items(args.only),
        keep_dump=Path(args.keep_dump).expanduser().resolve() if args.keep_dump else None,
    )


def confirm(question: str, assume_yes: bool) -> None:
    if assume_yes:
        return
    try:
        answer = input(f"{question} [s/N]: ").strip().lower()
    except EOFError as exc:
        raise SyncError("Se requiere confirmacion; repite con --yes") from exc
    if answer not in ("s", "si", "y", "yes"):
        raise SyncError("Cancelado por el usuario")


# ------------------------------------------------------------- base de datos


def _container_env(container: str, key: str) -> str | None:
    result = subprocess.run(
        ["docker", "exec", container, "printenv", key],
        check=False,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
    )
    if result.returncode != 0:
        return None
    value = result.stdout.strip()
    return value or None


def _counts_local(cfg: Config) -> dict[str, int]:
    user = cfg.local_user or _container_env(cfg.container, "POSTGRES_USER") or "tcga_admin"
    rows = capture(
        [
            "docker",
            "exec",
            cfg.container,
            "psql",
            "-U",
            user,
            "-d",
            cfg.local_db,
            "-tA",
            "-F",
            "|",
            "-c",
            " UNION ALL ".join(
                f"SELECT '{name}', count(*)::text FROM {name}" for name, _ in COUNT_QUERIES
            ),
        ]
    )
    counts: dict[str, int] = {}
    for line in rows.splitlines():
        name, _, value = line.partition("|")
        if name:
            counts[name] = int(value or 0)
    return counts


def _psql_remote(env_file: Path, sql: str) -> str:
    return capture(
        ["docker", "run", "--rm", "--env-file", str(env_file), PG_IMAGE, "psql", "-tA", "-F", "|", "-c", sql]
    )


def _counts_remote(env_file: Path) -> dict[str, int]:
    rows = _psql_remote(
        env_file,
        " UNION ALL ".join(
            f"SELECT '{name}', count(*)::text FROM {name}" for name, _ in COUNT_QUERIES
        ),
    )
    counts: dict[str, int] = {}
    for line in rows.splitlines():
        name, _, value = line.partition("|")
        if name:
            counts[name] = int(value or 0)
    return counts


def sync_database(cfg: Config) -> None:
    if not cfg.database_url:
        raise SyncError(
            "Falta RENDER_DATABASE_URL (URL externa de PostgreSQL de Render). "
            "Definela en el entorno o en .env.render"
        )
    require_binary("docker")
    pg_env = parse_pg_env(cfg.database_url, cfg.sslmode)

    info(f"Origen : contenedor {cfg.container} / base {cfg.local_db}")
    info(f"Destino: {mask_url(cfg.database_url)}")
    try:
        local_counts = _counts_local(cfg)
    except SyncError:
        # En dry-run sirve para revisar la configuracion sin el contenedor local.
        if not cfg.dry_run:
            raise
        warn("No se pudieron leer los recuentos locales (contenedor parado?)")
        local_counts = {}
    if local_counts:
        resumen = ", ".join(f"{name}={value}" for name, value in local_counts.items())
        info(f"Filas locales: {resumen}")

    if cfg.dry_run:
        info("[dry-run] no se vuelca ni se restaura nada")
        return

    confirm(
        "Se van a reemplazar los esquemas bronze/silver/gold/ops en Render. Continuar?",
        cfg.assume_yes,
    )

    with tempfile.TemporaryDirectory(prefix="render_sync_") as tmp:
        tmp_dir = Path(tmp)
        dump = tmp_dir / "tcga.dump"
        dump_inside = f"/tmp/render_sync_{os.getpid()}.dump"
        env_file = tmp_dir / "pg.env"

        # El fichero de entorno evita pasar la contrasena en la linea de comandos.
        lines = [f"{key}={value}" for key, value in pg_env.items()]
        fd = os.open(env_file, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write("\n".join(lines) + "\n")

        started = time.monotonic()
        info("Volcando la base local (pg_dump)...")
        user = cfg.local_user or _container_env(cfg.container, "POSTGRES_USER") or "tcga_admin"
        try:
            run(
                [
                    "docker",
                    "exec",
                    cfg.container,
                    "pg_dump",
                    "-U",
                    user,
                    "-d",
                    cfg.local_db,
                    "--format=custom",
                    "--no-owner",
                    "--no-privileges",
                    "--file",
                    dump_inside,
                ]
            )
            run(["docker", "cp", f"{cfg.container}:{dump_inside}", str(dump)])
        finally:
            subprocess.run(
                ["docker", "exec", cfg.container, "rm", "-f", dump_inside],
                check=False,
                capture_output=True,
            )

        size = dump.stat().st_size
        ok(f"Volcado listo: {human_bytes(size)} en {time.monotonic() - started:.0f} s")

        info("Restaurando en Render (--clean --if-exists)...")
        restore_started = time.monotonic()
        run(
            [
                "docker",
                "run",
                "--rm",
                "--env-file",
                str(env_file),
                "-v",
                f"{tmp_dir}:/dump:ro",
                PG_IMAGE,
                "pg_restore",
                "--clean",
                "--if-exists",
                "--no-owner",
                "--no-privileges",
                "--single-transaction",
                f"--dbname={pg_env['PGDATABASE']}",
                "/dump/tcga.dump",
            ]
        )
        ok(f"Restauracion completada en {time.monotonic() - restore_started:.0f} s")

        if cfg.keep_dump:
            cfg.keep_dump.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(dump, cfg.keep_dump)
            ok(f"Copia del volcado en {cfg.keep_dump}")

        remote_counts = _counts_remote(env_file)
        resumen = ", ".join(f"{name}={value}" for name, value in remote_counts.items())
        ok(f"Filas en Render: {resumen}")

        differences = {
            name: (local_counts.get(name), remote_counts.get(name))
            for name, _ in COUNT_QUERIES
            if local_counts.get(name) != remote_counts.get(name)
        }
        if differences:
            for name, (local, remote) in differences.items():
                warn(f"Recuento distinto en {name}: local={local} remoto={remote}")
            raise SyncError("La restauracion no coincide con el origen")
        ok("Recuentos verificados: la base de Render coincide con la local")


# ------------------------------------------------------------------- ficheros


def _remote_free_kb(cfg: Config) -> int | None:
    output = capture(
        ["ssh", *cfg.ssh_options(), cfg.ssh_host, f"df -Pk {shlex.quote(cfg.remote_dir)} | tail -1 | awk '{{print $4}}'"]
    )
    try:
        return int(output.split()[0])
    except (ValueError, IndexError):
        return None


def _upload_item(cfg: Config, item: str, size: int) -> None:
    remote = f"{cfg.remote_dir}/{item}"
    parent = os.path.dirname(remote)

    info(f"Subiendo {item} ({human_bytes(size)})...")
    started = time.monotonic()

    tar = subprocess.Popen(
        ["tar", "-cf", "-", "-C", str(cfg.data_dir), item],
        stdout=subprocess.PIPE,
    )
    try:
        ssh = subprocess.Popen(
            [
                "ssh",
                *cfg.ssh_options(),
                cfg.ssh_host,
                f"mkdir -p {shlex.quote(parent)} && tar -xf - -C {shlex.quote(cfg.remote_dir)}",
            ],
            stdin=tar.stdout,
        )
        assert tar.stdout is not None
        tar.stdout.close()
        ssh_rc = ssh.wait()
        tar_rc = tar.wait()
    finally:
        if tar.poll() is None:
            tar.kill()

    if tar_rc != 0 or ssh_rc != 0:
        raise SyncError(f"Fallo la subida de {item} (tar={tar_rc}, ssh={ssh_rc})")

    elapsed = max(time.monotonic() - started, 0.001)
    rate = size / elapsed
    ok(f"{item} subido en {elapsed:.0f} s ({human_bytes(rate)}/s)")

    # El contenedor sirve la web como UID 10001; se ajusta por si el upload
    # se hizo como root (lo habitual al entrar por SSH). Se compara el tamano
    # aparente remoto con el local como comprobacion final.
    try:
        remote_size = capture(
            [
                "ssh",
                *cfg.ssh_options(),
                cfg.ssh_host,
                (
                    f"chown -R 10001:10001 {shlex.quote(remote)} 2>/dev/null; "
                    f"chmod -R a+rX {shlex.quote(remote)} 2>/dev/null; "
                    f"du -sb {shlex.quote(remote)} | cut -f1"
                ),
            ]
        )
    except SyncError as exc:
        warn(f"No se pudo verificar {item}: {exc}")
        return

    try:
        uploaded = int(remote_size.splitlines()[-1])
    except (ValueError, IndexError):
        warn(f"No se pudo interpretar el tamano remoto de {item}: {remote_size!r}")
        return
    if abs(uploaded - size) > max(1024, size // 100):
        warn(f"Tamano distinto en {item}: local={size} remoto={uploaded}")
    else:
        ok(f"{item} verificado ({human_bytes(uploaded)})")


def sync_files(cfg: Config) -> None:
    if not cfg.ssh_host:
        raise SyncError(
            "Falta RENDER_SSH_HOST (por ejemplo srv-xxxx@ssh.oregon.render.com). "
            "Definelo en el entorno o en .env.render"
        )
    require_binary("ssh")
    require_binary("tar")
    if not cfg.data_dir.is_dir():
        raise SyncError(f"No existe el directorio de datos local: {cfg.data_dir}")

    missing = [item for item in cfg.items if not (cfg.data_dir / item).exists()]
    if missing:
        raise SyncError(f"Faltan rutas locales: {', '.join(missing)}")

    sizes = {item: dir_size(cfg.data_dir / item) for item in cfg.items}
    total = sum(sizes.values())
    info(f"Destino: {cfg.ssh_host}:{cfg.remote_dir}")
    for item, size in sizes.items():
        info(f"  {item:<32} {human_bytes(size)}")
    info(f"  {'total':<32} {human_bytes(total)}")

    if cfg.dry_run:
        info("[dry-run] no se sube nada")
        return

    free_kb = _remote_free_kb(cfg)
    if free_kb is not None:
        free_bytes = free_kb * 1024
        info(f"Espacio libre en {cfg.remote_dir}: {human_bytes(free_bytes)}")
        if free_bytes < total * 1.05:
            raise SyncError(
                "No hay espacio suficiente en el disco de Render. "
                "Aumenta el tamano del disco desde el Dashboard."
            )

    run(
        [
            "ssh",
            *cfg.ssh_options(),
            cfg.ssh_host,
            f"mkdir -p {shlex.quote(cfg.remote_dir)} && test -w {shlex.quote(cfg.remote_dir)}",
        ]
    )

    started = time.monotonic()
    for item in cfg.items:
        _upload_item(cfg, item, sizes[item])
    ok(f"Datos sincronizados en {time.monotonic() - started:.0f} s")
    ok("La web lee los ficheros de /data al vuelo; no hace falta reiniciar el servicio")


# ----------------------------------------------------------------------- CLI


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="render_sync.py",
        description="Sube los datos procesados en local al reporteador de Render.",
    )
    parser.add_argument("command", choices=("db", "files", "all"))
    parser.add_argument("--ssh-host", help="usuario@ssh.<region>.render.com")
    parser.add_argument("--database-url", help="URL externa de PostgreSQL de Render")
    parser.add_argument("--remote-dir", help="directorio del disco en Render (default /data)")
    parser.add_argument("--data-dir", help="directorio de datos local (default ./data)")
    parser.add_argument("--container", default=DEFAULT_CONTAINER, help="contenedor PostgreSQL local")
    parser.add_argument("--local-db", default=DEFAULT_LOCAL_DB, help="base de datos local")
    parser.add_argument("--local-user", help="usuario de PostgreSQL local (default: el del contenedor)")
    parser.add_argument("--sslmode", help="sslmode de libpq para Render (default: require)")
    parser.add_argument(
        "--only",
        action="append",
        metavar="RUTA",
        help=f"sube solo esta ruta; repetible ({', '.join(DEFAULT_ITEMS)})",
    )
    parser.add_argument("--env-file", help="fichero KEY=VALUE (default: .env.render)")
    parser.add_argument("--keep-dump", help="guarda una copia del volcado en esta ruta")
    parser.add_argument("--dry-run", action="store_true", help="muestra el plan sin ejecutar")
    parser.add_argument("-y", "--yes", action="store_true", help="no pedir confirmacion")
    return parser


def main(argv: list[str] | None = None) -> int:
    # En consolas Windows (cp1252) evita UnicodeEncodeError al imprimir.
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except (ValueError, OSError):
                pass

    args = build_parser().parse_args(argv)
    try:
        cfg = build_config(args)
        if args.command in ("db", "all"):
            sync_database(cfg)
        if args.command in ("files", "all"):
            sync_files(cfg)
    except SyncError as exc:
        fail(str(exc))
        return 1
    except KeyboardInterrupt:
        fail("Interrumpido")
        return 130
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

