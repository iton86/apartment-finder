# apartment-finder

Scrapes real estate listings, stores them, and (eventually) applies AI-driven
selection criteria with Telegram notifications. Built with clean architecture:
domain/application logic never depends on Playwright, Postgres, or Azure directly.

## First-time setup

```bash
uv sync                          # install exact locked dependencies
uv run playwright install chromium
cp .env.example .env             # local dev defaults already work out of the box
```

## Everyday development

```bash
make up          # start local Postgres (docker compose)
make migrate      # apply schema via Alembic
make test         # run unit + integration tests
make format       # auto-fix style issues
make check        # lint + full test suite — run before pushing
```

Local development never touches the real Azure database or costs anything —
that's the point. `APP_ENV` defaults to `local`, which points at the
docker-compose Postgres with throwaway credentials.

## Running against the real Azure database

Only do this deliberately. Set `APP_ENV=cloud` and the variables documented
in `.env.example`'s cloud section, then:

```bash
APP_ENV=cloud uv run apartment-finder-scrape "https://www.imot.bg/obiavi/prodazhbi/grad-sofiya"
```

## Adding a schema change

```bash
# 1. Edit src/apartment_finder/infrastructure/persistence/models.py
# 2. With local Postgres running (make up):
APP_ENV=local uv run alembic revision --autogenerate -m "describe the change"
# 3. Review the generated file in db_migrations/versions/ — autogenerate is a
#    good first draft, not a guarantee
make migrate
```

## Project layout

- `src/apartment_finder/domain/` — core entities, no external dependencies
- `src/apartment_finder/application/` — use cases and abstract ports
- `src/apartment_finder/infrastructure/` — Playwright, Postgres, Azure Blob implementations
- `src/apartment_finder/interface/` — CLI entry point (composition root)
- `db_migrations/` — Alembic migration history
- `tests/unit/` — fast tests against fakes, no DB needed
- `tests/integration/` — real tests against local Postgres
