.PHONY: dev test test-fast lint format typecheck check migrate migration secrets-scan up down logs

# --- Development ---------------------------------------------------------

dev:
	cd backend && uv run uvicorn webhook_service.main:app --reload

# --- Tests ---------------------------------------------------------------

test:
	cd backend && uv run pytest --cov --cov-report=term-missing

# Skips the Docker-backed tests — useful for a tight edit/run loop.
test-fast:
	cd backend && uv run pytest -m "not integration"

# --- Quality gates (mirrors CI) ------------------------------------------

lint:
	cd backend && uv run ruff check src tests

format:
	cd backend && uv run ruff format src tests && uv run ruff check src tests --fix

typecheck:
	cd backend && uv run mypy src

secrets-scan:
	gitleaks detect --config .gitleaks.toml --redact --verbose

# Run everything CI runs, before pushing.
check: lint typecheck test secrets-scan

# --- Database ------------------------------------------------------------

migrate:
	cd backend && uv run alembic upgrade head

# Usage: make migration m="add subscriptions table"
migration:
	cd backend && uv run alembic revision --autogenerate -m "$(m)"

# --- Local stack ---------------------------------------------------------

up:
	docker compose up --build

down:
	docker compose down -v

logs:
	docker compose logs -f backend
