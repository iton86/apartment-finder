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

## Scheduling with Temporal (every 10 minutes)

Install the updated dependencies with `uv sync`, and ensure Chromium is installed
(`uv run playwright install chromium`). Start the local services and migrate:

```bash
docker compose --profile temporal up -d
make migrate
```

In one terminal, keep the worker running:

```bash
APP_ENV=local uv run apartment-finder-temporal worker
```

In another terminal, create the schedule once:

```bash
uv run apartment-finder-temporal schedule \
  "https://www.imot.bg/obiavi/prodazhbi/grad-sofiya/manastirski-livadi/ednostaen?type_home=2~" \
  --max-pages 25 --max-workers 4 --request-delay 3
```

The first run starts at the next 10-minute interval. Repeating this command updates
the same schedule and preserves its paused state. Inspect runs, manually trigger,
pause, or resume the schedule in the Temporal UI at <http://localhost:8233>.
The schedule ID defaults to `apartment-finder-every-10-minutes`.

The workflow executes a `scrape_listings` activity, which runs the existing CLI in
a subprocess. Individual ads still use the existing four-worker scraper; logs and
the scrape summary appear in the worker terminal. The activity heartbeats every
15 seconds and terminates the scraper process group on cancellation or timeout.
This worker supports Linux/WSL/Docker. The default run limit is 60 minutes; change
it with `schedule --timeout-minutes`. Activity retries are disabled: a failed run
is tried again at the next scheduled interval. Per-ad failures retain the existing
CLI behavior (logged and counted, without failing the whole workflow).

If a run lasts more than 10 minutes, overlapping scheduled ticks are skipped.
The worker processes one scraping activity at a time. Keep both Temporal and the
worker running; Temporal state persists in the `temporal_data` Docker volume.
The Compose Temporal service is for local development, not production hosting.

The worker inherits the same `.env`, database, Azure, and logging settings as the
CLI. To use an existing Temporal service, set `TEMPORAL_ADDRESS`,
`TEMPORAL_NAMESPACE`, and optionally `TEMPORAL_TASK_QUEUE`. Temporal Cloud can use
`TEMPORAL_API_KEY` (enables TLS); `TEMPORAL_TLS=true` also enables TLS explicitly.
No database credentials are stored in the workflow input.

**Search coverage:** scraping never deactivates stored ads based on their absence
from search results. Searches can be partial, empty, or cover different areas.
Automatic deactivation is disabled until search membership and discovery completion
are tracked. The repository's explicit `mark_inactive` operation requires a complete
inventory across the database; an empty inventory deactivates all active ads.

The Docker image can also host the worker by overriding its default entry point:

```bash
docker run --rm --env-file .env --ipc=host \
  --network apartment-finder_default \
  -e APP_ENV=local -e POSTGRES_HOST=postgres -e TEMPORAL_ADDRESS=temporal:7233 \
  --entrypoint apartment-finder-temporal apartment-finder worker
```

See [Temporal schedules](https://docs.temporal.io/develop/python/workflows/schedules)
and [the local Temporal server](https://docs.temporal.io/cli/command-reference/server).

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
