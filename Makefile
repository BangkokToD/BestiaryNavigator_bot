SHELL := /bin/bash

PYTHON ?= python
PIP ?= $(PYTHON) -m pip
COMPOSE_FILE ?= infra/docker-compose.yml
DC ?= docker compose -f $(COMPOSE_FILE)

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

.PHONY: postgres-up
postgres-up:
	$(DC) up -d postgres

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
