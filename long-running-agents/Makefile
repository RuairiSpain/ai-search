.PHONY: install db-up db-down migrate run test lint fmt

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

fmt:
	ruff format src tests
