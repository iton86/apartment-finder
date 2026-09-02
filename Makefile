# Run `make help` to see this list. Every command here is safe to run
# repeatedly — none of them touch the real Azure database.

.PHONY: help up down migrate test test-unit test-integration lint format check

help:
	@echo "up               - start local Postgres (docker compose)"
	@echo "down             - stop local Postgres"
	@echo "migrate          - apply all pending migrations to local Postgres"
	@echo "test             - run unit + integration tests (needs 'make up' first)"
	@echo "test-unit        - run only unit tests (no DB needed)"
	@echo "test-integration - run only integration tests (needs local Postgres)"
	@echo "lint             - check code style with ruff"
	@echo "format           - auto-fix code style with ruff"
	@echo "check            - lint + full test suite (run before pushing)"

up:
	docker compose up -d
	@echo "Waiting for Postgres to be ready..."
	@until docker compose exec -T postgres pg_isready -U apartment_finder > /dev/null 2>&1; do sleep 1; done
	@echo "Postgres is up. Run 'make migrate' to apply the schema."

down:
	docker compose down

migrate:
	APP_ENV=local uv run alembic upgrade head

test-unit:
	uv run pytest tests/unit -v

test-integration:
	APP_ENV=local uv run pytest tests/integration -v

test: test-unit test-integration

lint:
	uv run ruff check .

format:
	uv run ruff format .
	uv run ruff check --fix .

check: lint test
