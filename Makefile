SHELL := /bin/bash

PYTHON ?= python
PIP ?= $(PYTHON) -m pip
COMPOSE_FILE ?= infra/docker-compose.yml
DC ?= docker compose -f $(COMPOSE_FILE)
ALEMBIC_CONFIG ?= app/db/alembic.ini
ALEMBIC_DATABASE_URL ?= postgresql+asyncpg://bn:bn@localhost:5432/bestiary
ALEMBIC ?= ALEMBIC_DATABASE_URL=$(ALEMBIC_DATABASE_URL) $(PYTHON) -m alembic -c $(ALEMBIC_CONFIG)

.DEFAULT_GOAL := help

.PHONY: help
help:
	@echo "BestiaryNavigator_bot commands"
	@echo ""
	@echo "Local tooling:"
	@echo "  make install       Install runtime package"
	@echo "  make install-dev   Install runtime + dev dependencies"
	@echo "  make lint          Run ruff"
	@echo "  make test          Run pytest"
	@echo "  make check         Run lint, tests and docker compose -f infra/docker-compose.yml config"
	@echo ""
	@echo "Docker:"
	@echo "  make docker-config Validate docker compose -f infra/docker-compose.yml config"
	@echo "  make docker-build  Build application images"
	@echo "  make docker-down   Stop and remove compose services"
	@echo ""
	@echo "Database:"
	@echo "  make db-upgrade    Run alembic upgrade head"
	@echo "  make db-downgrade  Run alembic downgrade base"
	@echo "  make db-current    Show current database revision"
	@echo "  make db-reset      Downgrade to base and upgrade to head"
	@echo "  make postgres-wait Wait until PostgreSQL is ready"
	@echo ""
	@echo "PostgreSQL:"
	@echo "  make postgres-up   Start PostgreSQL"
	@echo "  make postgres-logs Show PostgreSQL logs"
	@echo "  make postgres-ps   Show compose services"
	@echo "  make postgres-stop Stop PostgreSQL"
	@echo "  make postgres-clean Stop services and remove volumes"

.PHONY: install
install:
	$(PIP) install -e .

.PHONY: install-dev
install-dev:
	$(PIP) install -e ".[dev]"

.PHONY: lint
lint:
	$(PYTHON) -m ruff check .

.PHONY: test
test:
	$(PYTHON) -m pytest

.PHONY: check
check: lint test docker-config

.PHONY: docker-config
docker-config:
	$(DC) config

.PHONY: docker-build
docker-build:
	$(DC) build

.PHONY: docker-down
docker-down:
	$(DC) down

.PHONY: db-upgrade
db-upgrade:
	$(ALEMBIC) upgrade head

.PHONY: db-downgrade
db-downgrade:
	$(ALEMBIC) downgrade base

.PHONY: db-current
db-current:
	$(ALEMBIC) current

.PHONY: db-reset
db-reset: db-downgrade db-upgrade

.PHONY: postgres-up
postgres-up:
	$(DC) up -d postgres

.PHONY: postgres-wait
postgres-wait:
	@until $(DC) exec -T postgres pg_isready -U bn -d bestiary; do \
		sleep 1; \
	done

.PHONY: postgres-logs
postgres-logs:
	$(DC) logs postgres --tail=50

.PHONY: postgres-ps
postgres-ps:
	$(DC) ps

.PHONY: postgres-stop
postgres-stop:
	$(DC) stop postgres

.PHONY: postgres-clean
postgres-clean:
	$(DC) down -v
