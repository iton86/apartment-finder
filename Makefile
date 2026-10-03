# Run `make help` to see this list. Every command here is safe to run
# repeatedly — none of them touch the real Azure database.

.PHONY: help install install-deps up down db-reset migrate test test-unit test-integration lint format check training-export

help:
	@echo "install          - install locked deps + the Chromium binary Playwright drives"
	@echo "install-deps     - system libs Chromium needs (sudo; once per machine)"
	@echo "up               - start local Postgres (docker compose)"
	@echo "down             - stop local Postgres"
	@echo "db-reset         - delete local DB data, recreate Postgres, and apply migrations"
	@echo "migrate          - apply all pending migrations to local Postgres"
	@echo "test             - run unit + integration tests (needs 'make up' first)"
	@echo "test-unit        - run only unit tests (no DB needed)"
	@echo "test-integration - run only integration tests (needs local Postgres)"
	@echo "lint             - check code style with ruff"
	@echo "format           - auto-fix code style with ruff"
	@echo "check            - lint + full test suite (run before pushing)"
	@echo "training-export  - dump cleaned, de-duplicated descriptions to data/raw.jsonl"

# The playwright package and the browser it drives install separately: uv sync
# gets the Python client, `playwright install` downloads the pinned Chromium
# build into ~/.cache/ms-playwright. Re-run this after any playwright bump, or
# the scraper fails with "Executable doesn't exist".
install:
	uv sync
	uv run playwright install chromium
	@ldd ~/.cache/ms-playwright/chromium_headless_shell-*/chrome-headless-shell-linux64/chrome-headless-shell 2>/dev/null | grep -q "not found" \
		&& printf "\nWARNING: Chromium is missing system libraries. Run 'make install-deps'\n         or the scraper dies with TargetClosedError on browser launch.\n" \
		|| true

# Separate from `install` because this is the one command here that needs root,
# and it only matters on a bare Linux image (fresh container, clean WSL) where
# Chromium's shared libraries were never installed. Skipping it doesn't fail at
# install time — it fails later, at p.chromium.launch(), as a confusing
# TargetClosedError whose real cause is "libnspr4.so: cannot open shared object
# file" buried in the browser logs.
install-deps:
	sudo .venv/bin/playwright install-deps chromium

up:
	docker compose up -d
	@echo "Waiting for Postgres to be ready..."
	@until docker compose exec -T postgres pg_isready -U apartment_finder > /dev/null 2>&1; do sleep 1; done
	@echo "Postgres is up. Run 'make migrate' to apply the schema."

down:
	docker compose down

# Local development only. Removing the Compose volume permanently deletes
# every row and Alembic revision recorded in the local Postgres instance.
db-reset:
	docker compose down --volumes --remove-orphans
	$(MAKE) up
	$(MAKE) migrate

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

# Read-only (the export runs in a READ ONLY transaction), but it follows
# APP_ENV like everything else — set APP_ENV=cloud to export production ads.
training-export:
	uv run python -m training.export_descriptions
