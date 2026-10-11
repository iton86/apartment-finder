# Apartment Finder

Apartment Finder collects apartment listings from imot.bg and stores structured
records in PostgreSQL. It supports individual scraping runs, recurring execution
through Temporal, and an independent machine-learning pipeline for extracting
points of interest (POIs) from listing descriptions.

## Capabilities

- Discover and parse listings using Playwright and Chromium.
- Control listing concurrency and the minimum interval between requests.
- Persist listings and track their availability over subsequent runs.
- Run database migrations and scraping from the same container image.
- Export descriptions, review annotations, and fine-tune RoBERTa-compatible
  encoders for POI names and categories.

The project uses ports and adapters to separate domain rules from browser,
database, and cloud integrations. See the [architecture](docs/architecture.md)
for data flow and implementation boundaries.

## Quick start

Requires Python 3.12 or later, uv, Make, and Docker with Compose. Run from the
repository root. Copy the environment file only if you do not already have one.

```bash
make install
cp .env.example .env
make up
make migrate
uv run apartment-finder-scrape \
  "https://www.imot.bg/obiavi/prodazhbi/grad-sofiya" \
  --max-pages 1 --max-workers 4 --request-delay 3
```

On Linux, run `make install-deps` if Chromium's system libraries are missing.
The supplied `.env.example` selects the local database. Verify the destination
before running against an existing environment.

## Documentation

| Document | Purpose |
|---|---|
| [Documentation index](docs/README.md) | Reading paths and documentation scope |
| [Architecture and data model](docs/architecture.md) | Components, scrape lifecycle, persistence, limitations |
| [Development guide](docs/development.md) | Setup, tests, migrations, contribution workflow |
| [Configuration reference](docs/configuration.md) | Environment variables and command-line options |
| [Operations guide](docs/operations.md) | Containers, scheduling, deployment requirements, troubleshooting |
| [Machine-learning overview](docs/machine-learning.md) | Training boundaries, workflow, artifacts, evaluation |
| [Annotation and training guide](training/README.md) | Detailed dataset preparation and training instructions |

## Implementation status

Image storage adapters exist, but the scraping use case currently does not call
image downloading or persist image records. Existing listing prices and details
are not refreshed. Model inference is an offline tool and is not integrated into
the scraping pipeline. Preference matching and Telegram notifications remain
planned functionality.

See [known limitations](docs/architecture.md#known-limitations) before relying on
listing status, price history, or model predictions in downstream applications.
