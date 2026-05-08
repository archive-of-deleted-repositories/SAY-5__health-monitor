.PHONY: dev migrate seed test test-int lint typecheck run simulate up down clean

PY ?= python3
VENV ?= .venv
BIN := $(VENV)/bin
PIP := $(BIN)/pip
PYTEST := $(BIN)/pytest

dev:
	$(PY) -m venv $(VENV)
	$(PIP) install -U pip
	$(PIP) install -e ".[dev]"

migrate:
	$(BIN)/alembic upgrade head

seed:
	$(BIN)/health-monitor add-endpoint --name local-api --url http://localhost:8080/healthz --threshold 3

test:
	$(PYTEST) tests/unit -q --cov=src/health_monitor --cov-report=term-missing --cov-fail-under=80

test-int:
	RUN_INTEGRATION=1 $(PYTEST) tests/integration -q

lint:
	$(BIN)/ruff check src tests
	$(BIN)/black --check src tests

format:
	$(BIN)/ruff check --fix src tests
	$(BIN)/black src tests

typecheck:
	$(BIN)/mypy

run:
	$(BIN)/health-monitor run --config config/monitor.example.yaml

simulate:
	$(BIN)/health-monitor simulate --endpoint api --failures 3

up:
	docker compose up --build

down:
	docker compose down -v

clean:
	rm -rf $(VENV) build dist *.egg-info .pytest_cache .mypy_cache .ruff_cache
	find . -type d -name __pycache__ -exec rm -rf {} +
	rm -f *.db
