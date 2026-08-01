.PHONY: install db-up db-down migrate run test lint gwlint fmt

install:
	pip install -e ".[dev]"

db-up:
	docker compose up -d postgres
	@echo "waiting for postgres..."
	@until docker compose exec -T postgres pg_isready -U gateway -d gateway >/dev/null 2>&1; do sleep 1; done

db-down:
	docker compose down

migrate: db-up
	PGPASSWORD=devpassword psql -h localhost -U gateway -d gateway -f migrations/0001_init.sql

run:
	PYTHONPATH=src uvicorn gateway.main:app --reload --port 8080

test:
	PYTHONPATH=src pytest

lint:
	ruff check src tests
	mypy src

# D6's config linter (docs/02-decisions.md). Fails the build on any L0xx
# safety-rule violation gwlint can actually check from this repo alone —
# see src/gateway/gwlint.py's module docstring for what it can't and why.
gwlint:
	PYTHONPATH=src python -m gateway.gwlint config/apps.yaml

fmt:
	ruff format src tests
