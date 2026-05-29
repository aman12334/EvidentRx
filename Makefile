## EvidentRx — platform shortcuts
## Usage: make <target>
##
##   make setup      First-time setup (venv + DB + migrations + seed data)
##   make start      Start API (port 8000) + Dashboard (port 3000)
##   make api        Start API server only
##   make ui         Start Next.js dashboard only
##   make seed       Re-seed demo investigation data
##   make migrate    Run Alembic migrations
##   make rules      Run deterministic compliance rules engine
##   make status     Quick health check (DB counts + server status)
##   make reset      Wipe all data and re-seed (dev only)
##   make lint       Run ruff + mypy
##   make test       Run pytest unit tests
##   make clean      Remove .venv, __pycache__, .next, node_modules

PYTHON := python3
CLI    := $(PYTHON) evidentrx.py

.PHONY: help setup start api ui seed migrate rules status reset lint test clean

# Default target — print help
help:
	@echo ""
	@echo "  EvidentRx 340B Compliance Platform"
	@echo ""
	@echo "  make setup      First-time setup (run this first)"
	@echo "  make start      Start API + Dashboard"
	@echo "  make api        Start API only (port 8000)"
	@echo "  make ui         Start Dashboard only (port 3000)"
	@echo "  make seed       Re-seed demo data"
	@echo "  make migrate    Run database migrations"
	@echo "  make rules      Run compliance rules engine"
	@echo "  make status     System health + data stats"
	@echo "  make reset      Wipe + reseed (dev only)"
	@echo "  make lint       Ruff + mypy"
	@echo "  make test       Pytest unit tests"
	@echo "  make clean      Remove generated artefacts"
	@echo ""

setup:
	$(CLI) setup

start:
	$(CLI) start

api:
	$(CLI) start --api-only

ui:
	$(CLI) start --ui-only

seed:
	$(CLI) seed

migrate:
	.venv/bin/python -m alembic upgrade head

rules:
	$(CLI) rules

status:
	$(CLI) status

reset:
	$(CLI) reset

lint:
	.venv/bin/ruff check .
	.venv/bin/mypy . --ignore-missing-imports --no-error-summary

test:
	.venv/bin/pytest tests/ -v --tb=short

clean:
	rm -rf .venv __pycache__ .pytest_cache coverage.xml
	find . -type d -name "__pycache__" -not -path "./.venv/*" -exec rm -rf {} + 2>/dev/null || true
	rm -rf frontend/.next frontend/node_modules
	@echo "Cleaned."
