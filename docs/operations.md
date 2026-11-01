# Operations guide

## Execution model

The scraper is a finite command, not an HTTP service. It can run directly, in a
container job, or through a Temporal worker. Temporal is optional. Apply database
migrations before starting scraping processes.

## Container execution

Build the image from the repository root:

```bash
docker build -t apartment-finder .
```

The image includes the installed application, Chromium and its system libraries,
and Alembic migrations. It runs as a non-root user. Offline training tools and
ML dependencies are not included.

For the default local Compose project, migrate using its database network:

```bash
docker run --rm --env-file .env \
  --network apartment-finder_default \
  -e APP_ENV=local -e POSTGRES_HOST=postgres \
  --entrypoint alembic apartment-finder upgrade head
```

Then run the scraper:

```bash
docker run --rm --env-file .env \
  --network apartment-finder_default \
  -e APP_ENV=local -e POSTGRES_HOST=postgres \
  apartment-finder "<search-url>" --max-pages 1
```

Replace `apartment-finder_default` if the Compose project name differs. Inside a
container, `localhost` refers to that container; use `postgres` on the shared
Compose network to reach the database service.

## Deployment contract

Provisioning infrastructure is separate from building the image. A deployment
must provide:

1. An existing PostgreSQL database with reachable DNS and network routing.
2. Configuration from the [reference](configuration.md), including storage
   account configuration currently required by the CLI.
3. For cloud mode, a usable Azure credential and permission to retrieve the
   database password secret from Key Vault.
4. Permission for the hosting platform to pull the container image.
5. Outbound connectivity to the listing website and any required cloud services.
6. A migration execution followed by a scraper execution, using the same database
   destination and compatible image version.

Image-pull authentication and credentials available to application code are
separate concerns. A successful image pull does not prove that
`DefaultAzureCredential` can retrieve a Key Vault secret inside the container.

For a migration job, the executable is `alembic` and its two arguments are
`upgrade` and `head`. For a scrape job, the executable is
`apartment-finder-scrape`; provide the search URL and optional scrape flags as
separate arguments. Run migrations once per deployment, before parallel workers.

A private database requires a network path and DNS resolution from the execution
environment. The same applies to a desktop database client: setting a hostname
alone does not create private connectivity. Public access and private access are
infrastructure decisions; changing application variables does not enable either.

## Recurring execution with Temporal

Start the development services and apply migrations:

```bash
docker compose --profile temporal up -d
make migrate
```

Keep a worker running in one terminal:

```bash
APP_ENV=local uv run apartment-finder-temporal worker
```

Create or update the schedule in another:

```bash
uv run apartment-finder-temporal schedule "<search-url>" \
  --max-pages 1 --max-workers 4 --request-delay 3 --timeout-minutes 60
```

The default schedule ID is `apartment-finder-every-10-minutes`. Repeating the
command updates that schedule and preserves its paused state. The interval is
10 minutes, overlapping ticks are skipped, and the catch-up window is one minute.
The worker accepts one scraping activity at a time.

Inspect executions and pause/resume schedules at <http://localhost:8233>.
Both the server and worker must remain available. The Compose server persists
state in `temporal_data` and is a development server, not a production setup.

Activities launch the scraper CLI as a subprocess, heartbeat every 15 seconds,
and terminate its process group on cancellation or timeout. Activity retries are
disabled; future scheduled runs provide another attempt. Per-listing failures
retain the CLI's success-exit behavior.

To run the worker in the application image:

```bash
docker run --rm --env-file .env \
  --network apartment-finder_default \
  -e APP_ENV=local -e POSTGRES_HOST=postgres \
  -e TEMPORAL_ADDRESS=temporal:7233 \
  --entrypoint apartment-finder-temporal apartment-finder worker
```

## Logs and completion semantics

Logs go to stderr and the human-readable result summary goes to stdout:

```bash
uv run apartment-finder-scrape "<search-url>" --log-level debug > results.txt
```

Info logs show progress; debug logs expose parsing details and duplicate checks.
Third-party logger levels are capped to avoid enabling their debug output.
Treat logs and reports as potentially containing listing/contact information.

A zero exit status does not mean every listing succeeded. Inspect the failure
count and logs. A missing search page (404/410) also returns successfully with an
explicit message; correct the search URL rather than expecting automatic retries
to repair it. Unhandled database or configuration errors fail the process.

## Troubleshooting

| Symptom | Check |
|---|---|
| Chromium cannot start | Browser installation and missing system libraries; rerun `make install` or `make install-deps` as appropriate |
| `DefaultAzureCredential` cannot retrieve a token | Credentials available to the running process, rather than image-registry authentication |
| Secret access denied | Vault name, secret name, identity permissions, and vault network access |
| Database hostname cannot resolve | Exact hostname, execution environment DNS, and private network/DNS configuration |
| Database connection times out | Routing, firewall rules, database availability, and port 5432 reachability |
| Relation/table does not exist | Migration destination and `alembic current`; apply `alembic upgrade head` to that database |
| No new rows on a repeat scrape | Existing identities are skipped; see [limitations](architecture.md#known-limitations) |
| Schedule exists but no execution progresses | Worker availability, matching namespace/task queue, and paused schedule state |

## Data retention

Back up a database before destructive schema or infrastructure changes. Local
Compose volumes hold persistent state, and removing them deletes that state.
Export required records and training artifacts before deleting a temporary cloud
environment. Dataset and model output directories are not backed up by Git.

## Local MLflow POI service

The model registry and the prediction service are separate local processes.
The prediction service loads the model once on CPU and exposes MLflow's
`POST /invocations` endpoint on `127.0.0.1:5001`. It has no database credentials
or database writes. A separate enrichment command saves its results on listing
rows. The model returns names, categories, character offsets and confidence;
it does not predict distances.

From the repository root, apply the listing POI migration and register a completed
training run (its `model/` directory must exist):

```bash
make migrate
make poi-register POI_RUN=checkpoints/poi-v7-cuda-debug
```

Registration copies model weights, tokenizer and inference code into MLflow and
prints an immutable model URI, for example `models:/poi-encoder/1`, and a
`Prediction version` fingerprint (weights, tokenizer, inference settings and code). Keep both. Registration creates a new model
version each time, and does not change any running service. This adds model
artifacts to local MLflow storage; ensure enough disk space for the weights.
You can browse registered versions with `make mlflow-ui` at localhost:5000.

Start the service in a separate terminal, using the printed URI:

```bash
make poi-serve POI_MODEL_URI=models:/poi-encoder/1
```

Send descriptions without accessing the database:

```bash
curl --fail http://127.0.0.1:5001/invocations \
  -H 'Content-Type: application/json' \
  -d '{"dataframe_records":[{"description":"До Южен парк."}]}'
```

The response has `predictions`, one object per input description, each containing
`pois`, `model_version` and `description_sha256`. An empty `pois` list is a
successful prediction. Requests accept 1–100 nonempty descriptions, at most
100000 characters each; long descriptions are processed in token chunks.

After scraping, enrich up to 100 pending local listings using the fingerprint
printed by registration:

```bash
make poi-enrich POI_MODEL_VERSION=<prediction-fingerprint> POI_LIMIT=100
```

Repeat to process more listings. The command stores `pois` (JSON),
`poi_model_version`, `poi_description_sha256` and `poi_predicted_at` directly in
`listings`. It skips rows already predicted for that model and unchanged
text, including rows with no detected POIs. Changing model or description makes
a row eligible again. It refuses output from a different model or mismatched
text spans. If a description changes during inference, the old prediction is
not written. Already committed rows remain saved after a service failure, so
retrying is safe. Human-reviewed labeling annotations are unchanged.

Enrichment is an explicit post-scraping step, not automatically invoked by the
scraper. The command defaults to `APP_ENV=local` and rejects `APP_ENV=cloud`;
its database destination still follows local PostgreSQL environment overrides.
The existing scraper Docker image does not include the training dependencies;
run this local model service with the training and tracking groups as above.
