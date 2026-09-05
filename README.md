# apartment-finder

Scrapes real estate listings, stores them, and (eventually) applies AI-driven
selection criteria with Telegram notifications. Built with clean architecture:
domain/application logic never depends on Playwright, Postgres, or Azure directly.

## First-time setup

```bash
make install                     # locked deps + the Chromium binary Playwright drives
cp .env.example .env             # local dev defaults already work out of the box
```

On a bare Linux image (fresh container, clean WSL) Chromium also needs system
libraries. `make install` warns you if they're missing; install them with:

```bash
make install-deps         # needs sudo, once per machine
```

Skip it and the scraper fails at browser launch with `TargetClosedError`,
whose real cause (`libnspr4.so: cannot open shared object file`) is buried
in the Playwright browser logs.

## Everyday development

```bash
make up          # start local Postgres (docker compose)
make migrate      # apply schema via Alembic
make test         # run unit + integration tests
make format       # auto-fix style issues
make check        # lint + full test suite — run before pushing
```

To permanently delete the local database and rebuild it from the current
initial migration:

```bash
make db-reset
```

This removes the Docker Compose `postgres_data` volume. It does not connect to
or modify the Azure database.

Local development never touches the real Azure database or costs anything —
that's the point. `APP_ENV` defaults to `local`, which points at the
docker-compose Postgres with throwaway credentials.

## Running against the real Azure database

Only do this deliberately. Set `APP_ENV=cloud` and the variables documented
in `.env.example`'s cloud section, then:

```bash
APP_ENV=cloud uv run apartment-finder-scrape "https://www.imot.bg/obiavi/prodazhbi/grad-sofiya"
```

## Logging

Logs go to **stderr**, the result summary to **stdout** — so
`... > results.txt` keeps the report and leaves the chatter on screen.

```bash
uv run apartment-finder-scrape "<search-url>"                    # info (default)
uv run apartment-finder-scrape "<search-url>" --log-level debug  # full trace
LOG_LEVEL=debug uv run apartment-finder-scrape "<search-url>"    # same, via env
```

`--log-level` beats `LOG_LEVEL`, so an image or `.env` can set a default
that a single run overrides.

What each level is for:

- **info** — one line per new listing plus a `found/new/duplicates` summary.
  Duplicate skips are deliberately *not* here: on a re-run of the same
  search they're nearly every listing and would bury the rest.
- **debug** — every results page, every listing URL, the parsed `adParams`
  dict, per-field values, gallery slide counts, and each dedup hit/miss.
  This is the level to use when a field comes back empty.
- **warning** — the failures that don't stop the run: a selector matching
  nothing, a listing parsed with empty fields, an image that 404s.

Two deliberate constraints, both in
[logging_config.py](src/apartment_finder/interface/logging_config.py):

- Only the CLI configures logging. Every other module just does
  `logger = logging.getLogger(__name__)`, which is what makes one flag
  control the whole app and keeps imports side-effect-free under pytest.
- `--log-level debug` does **not** turn on debug for `azure.*`, `playwright`
  or `urllib3`. Azure's HTTP policy logs auth headers at that level, so
  third-party loggers are capped at info.

## Running in Docker

The image bundles Chromium, so it needs no host Playwright install:

```bash
DOCKER_BUILDKIT=1 docker build -t apartment-finder .
docker run --rm --env-file .env --ipc=host \
  apartment-finder "https://www.imot.bg/obiavi/prodazhbi/grad-sofiya" --max-pages 2
```

`--ipc=host` matters: Chromium shares memory between its processes through
IPC, and Docker's default namespace is small enough that image-heavy pages
crash the browser without it.

To reach the docker-compose Postgres from inside the container, the host
isn't `localhost` any more — join its network and use the service name:

```bash
docker run --rm --env-file .env --ipc=host \
  --network apartment-finder_default -e POSTGRES_HOST=postgres \
  apartment-finder "<search-url>"
```

The same image can run migrations, since `alembic.ini` and `db_migrations/`
are baked in:

```bash
docker run --rm --env-file .env --entrypoint alembic apartment-finder upgrade head
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
