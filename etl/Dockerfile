# syntax=docker/dockerfile:1.7
# ------------------------------------------------------------------ builder
FROM ghcr.io/astral-sh/uv:0.13.6 AS uv

FROM python:3.12-slim-bookworm AS builder

COPY --from=uv /uv /uvx /bin/

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

# Dependencias primero para aprovechar la caché de capas.
COPY etl/pyproject.toml etl/uv.lock ./
RUN uv sync --frozen --no-install-project --extra dev

COPY etl/src ./src
RUN uv sync --frozen --no-editable --extra dev

# ------------------------------------------------------------------ runtime
FROM python:3.12-slim-bookworm AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:$PATH"

RUN groupadd --gid 10001 app \
    && useradd --uid 10001 --gid app --create-home --home-dir /home/app app \
    && mkdir -p /data /sql /app \
    && chown -R app:app /data /sql /app

WORKDIR /app

COPY --from=builder --chown=app:app /app/.venv /app/.venv
COPY --chown=app:app etl/pyproject.toml /app/pyproject.toml
COPY --chown=app:app etl/src /app/src
COPY --chown=app:app sql /sql
COPY --chown=app:app tests /app/tests
COPY --chown=app:app etl/scheduler.sh /opt/scheduler.sh
RUN chmod 0555 /opt/scheduler.sh

USER app

CMD ["tcga", "--help"]
