# syntax=docker/dockerfile:1.7
# ------------------------------------------------------------------ frontend
FROM node:22-bookworm-slim AS frontend

WORKDIR /frontend

COPY flow/frontend/package.json flow/frontend/package-lock.json ./
RUN npm ci

COPY flow/frontend/ ./
RUN npm run build

# ------------------------------------------------------------------- builder
FROM ghcr.io/astral-sh/uv:0.13.6 AS uv

FROM python:3.12-slim-bookworm AS builder

COPY --from=uv /uv /uvx /bin/

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

# El paquete flow depende de tcga-medallion-etl con la ruta relativa ../etl.
COPY etl/pyproject.toml etl/uv.lock /etl/
COPY etl/src /etl/src
COPY flow/pyproject.toml flow/uv.lock /app/

RUN uv sync --frozen --no-install-project --extra dev

COPY flow/src /app/src
RUN uv sync --frozen --no-editable --extra dev

# ------------------------------------------------------------------ runtime
FROM python:3.12-slim-bookworm AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:$PATH" \
    FLOW_WORKFLOWS_DIR=/app/workflows \
    FLOW_FRONTEND_DIST=/app/frontend/dist \
    DATA_DIR=/data \
    SQL_DIR=/sql

RUN groupadd --gid 10001 app \
    && useradd --uid 10001 --gid app --create-home --home-dir /home/app app \
    && mkdir -p /data /sql /app/workflows /app/frontend /gemamx \
    && chown -R app:app /data /sql /app /gemamx

WORKDIR /app

COPY --from=builder --chown=app:app /app/.venv /app/.venv
COPY --chown=app:app flow/workflows /app/workflows
COPY --chown=app:app flow/src /app/src
COPY --chown=app:app sql /sql
COPY --from=frontend --chown=app:app /frontend/dist /app/frontend/dist

USER app

EXPOSE 8100

HEALTHCHECK --interval=20s --timeout=5s --start-period=15s --retries=5 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8100/health', timeout=3)"

CMD ["uvicorn", "tcga_flow.main:app", "--host", "0.0.0.0", "--port", "8100"]
