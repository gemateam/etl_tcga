SHELL := /bin/sh

COMPOSE ?= docker compose
ETL ?= $(COMPOSE) run --rm etl
PROJECT ?= TCGA-BRCA

.PHONY: help up down build ps logs init-db run-brca smoke test lint status psql reset flow-up flow-down flow-logs flow-test flow-lint flow-frontend

help:
	@echo "Objetivos disponibles:"
	@echo "  make up         Construye imágenes y levanta postgres + web"
	@echo "  make down       Detiene los servicios (conserva datos)"
	@echo "  make init-db    Aplica el DDL con el rol etl_rw"
	@echo "  make run-brca   Pipeline completo para TCGA-BRCA"
	@echo "  make smoke      Pipeline con --limit 5 + prueba de humo real"
	@echo "  make test       Pruebas unitarias (sin internet)"
	@echo "  make lint       Ruff sobre ETL y compilación de la web"
	@echo "  make status     Estado de ejecuciones y conteos"
	@echo "  make reset      Borra contenedores, datos y volumen de PostgreSQL"
	@echo "  make flow-up    Levanta el ejecutor visual en 127.0.0.1:$${FLOW_PORT:-8100}"
	@echo "  make flow-test  Pruebas del motor y la API (sin red)"
	@echo "  make flow-lint  Ruff del servicio flow"

up:
	$(COMPOSE) up -d --build
	@echo "Web: http://localhost:$${WEB_PORT:-8000}  (espera unos segundos al healthcheck)"

down:
	$(COMPOSE) down

build:
	$(COMPOSE) build

ps:
	$(COMPOSE) ps

logs:
	$(COMPOSE) logs -f --tail=200

init-db:
	$(ETL) tcga init-db

run-brca:
	$(ETL) tcga run --project $(PROJECT)

smoke:
	$(ETL) tcga run --project $(PROJECT) --limit 5
	$(ETL) python -m pytest /app/tests -m smoke

test:
	$(COMPOSE) run --rm -e TCGA_INTEGRATION=1 etl \
		python -m pytest /app/tests -m "not smoke" --ignore=/app/tests/test_web.py
	$(COMPOSE) run --rm web python -m pytest /app/tests/test_web.py

lint:
	$(ETL) ruff check /app/src /app/tests
	$(COMPOSE) run --rm web ruff check /app/app

status:
	$(ETL) tcga status

psql:
	$(COMPOSE) exec postgres sh -c 'psql -U "$$POSTGRES_USER" -d "$$POSTGRES_DB"'

reset:
	$(COMPOSE) down -v
	@echo "Datos de ./data no se borran automáticamente; elimínalos a mano si lo necesitas."

# ----------------------------------------------------------- ejecutor visual
flow-up:
	$(COMPOSE) --profile flow up -d --build flow
	@echo "Flow: http://127.0.0.1:$${FLOW_PORT:-8100}"

flow-down:
	$(COMPOSE) --profile flow stop flow

flow-logs:
	$(COMPOSE) --profile flow logs -f --tail=200 flow

flow-test:
	cd flow && uv run pytest

flow-lint:
	cd flow && uv run ruff check .

flow-frontend:
	cd flow/frontend && npm ci && npm run build
