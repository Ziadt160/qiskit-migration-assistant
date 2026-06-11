.PHONY: install dev test lint fmt typecheck eval build-store ingest serve worker up down \
        sandbox-image sandbox-legacy-image equivalence

install:
	pip install -e .

dev:
	pip install -e ".[dev]"

test:
	pytest -q

lint:
	ruff check .
	ruff format --check .

fmt:
	ruff format .
	ruff check --fix .

typecheck:
	mypy src

eval:
	python -m src.eval.run_eval --seed-only --min-recall 0.9

build-store:
	python -m src.migration.cli --build-store

ingest:
	python -m scripts.run_ingestion

serve:
	uvicorn src.api.main:app --reload --port 8000

worker:
	python -m src.worker.run

up:
	docker compose up -d --build

down:
	docker compose down

# Build the two sandbox images used by the behavioral-equivalence check.
sandbox-image:
	docker build -f Dockerfile.sandbox -t qiskit-migration-sandbox:latest .

sandbox-legacy-image:
	docker build -f Dockerfile.sandbox-legacy -t qiskit-migration-sandbox-legacy:latest .

# Behavioral-equivalence eval over the golden set (needs both sandbox images built).
equivalence: sandbox-image sandbox-legacy-image
	python -m src.eval.run_eval --equivalence
