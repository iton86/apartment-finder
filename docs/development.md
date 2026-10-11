# Development guide

## Prerequisites

Install Python 3.12 or later, uv, Make, and Docker with the Compose plugin.
Linux or WSL is the reference environment for the Makefile and Temporal worker.
The worker's subprocess cleanup uses process groups.

## Local setup

1. Install dependencies and Chromium:

   ```bash
   make install
   ```

2. If Chromium reports missing shared libraries on Linux, install them:

   ```bash
   make install-deps
   ```

   This command uses sudo to install operating-system packages.

3. Create local configuration, preserving any existing `.env`:

   ```bash
   cp .env.example .env
   ```

   Review the [configuration reference](configuration.md). The example selects
   local PostgreSQL and also supplies the storage account setting currently
   required by CLI construction.

4. Start PostgreSQL and apply the migrations:

   ```bash
   make up
   make migrate
   ```

   `make up` waits for database readiness. `make migrate` explicitly selects
   `APP_ENV=local`, but host and credential overrides still determine the actual
   database destination.

5. Run a small scrape:

   ```bash
   uv run apartment-finder-scrape \
     "https://www.imot.bg/obiavi/prodazhbi/grad-sofiya" --max-pages 1
   ```

## Verification

| Command | Purpose |
|---|---|
| `make test-unit` | Unit tests without a running PostgreSQL server |
| `make test-integration` | Repository tests against configured local PostgreSQL |
| `make lint` | Ruff checks |
| `make format` | Apply formatting and automatic lint fixes |
| `make check` | Lint plus unit and integration tests |

Use a disposable database for integration tests. Start PostgreSQL and migrate
before running the full checks. Training tests that need optional ML dependencies
can be run explicitly with the training group:

```bash
uv run --group training pytest tests/unit/test_training_finetune.py -v
```

## Schema changes

1. Update the SQLAlchemy models.
2. Generate a migration against the local database:

   ```bash
   APP_ENV=local uv run alembic revision --autogenerate -m "describe the change"
   ```

3. Review the generated upgrade and downgrade operations in
   `db_migrations/versions/`. Autogeneration requires manual review.
4. Run `make migrate` and the relevant integration tests.
5. Update the data-model documentation when behavior or constraints change.

## Service lifecycle

`make down` stops the default Compose services without intentionally removing
persistent volumes. Temporal and Label Studio use optional Compose profiles;
see [operations](operations.md) and the [training guide](../training/README.md).

`make db-reset` is destructive: it runs Compose teardown with `--volumes` and
`--remove-orphans`, then recreates PostgreSQL and reapplies migrations. Do not
use it when Compose data must be preserved.

## Contribution workflow

Keep domain and application code independent of concrete infrastructure. Add or
change adapter implementations behind the application ports. Test behavior at
the affected boundary, update command/configuration documentation, and run the
relevant checks before submitting a change.

Do not commit `.env`, credentials, scraped datasets, or model artifacts.
`data/` and `checkpoints/` are ignored by Git.
