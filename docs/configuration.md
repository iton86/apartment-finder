# Configuration reference

Configuration is loaded from environment variables and `.env`. Existing process
environment values take precedence over values loaded from `.env`.
The [settings implementation](../src/apartment_finder/infrastructure/persistence/settings.py)
is the authority for database connection behavior.

## Database selection

`APP_ENV` accepts `local` (default) or `cloud`. Other values raise an error.
These names choose how credentials are obtained; they do not enforce whether a
hostname points to a local or remote database.

### Local mode

| Variable | Default |
|---|---|
| `POSTGRES_HOST` | `localhost` |
| `POSTGRES_PORT` | `5432` |
| `POSTGRES_DATABASE` | `apartment_finder` |
| `POSTGRES_USERNAME` | `apartment_finder` |
| `POSTGRES_PASSWORD` | `local_dev_only` |

The generated URL uses SQLAlchemy's `postgresql+psycopg` driver. Local mode does
not add an explicit TLS option. The defaults match the development Compose file.

### Cloud mode

| Variable | Requirement |
|---|---|
| `POSTGRES_HOST` | Required: database hostname, without scheme or port |
| `POSTGRES_DATABASE` | Required: existing database name |
| `POSTGRES_ADMIN_USERNAME` | Required: PostgreSQL login |
| `KEY_VAULT_NAME` | Required: vault name, not its URL |
| `POSTGRES_PASSWORD_SECRET_NAME` | Defaults to `postgres-admin-password` |

Cloud mode fetches the password from Key Vault using `DefaultAzureCredential`,
then connects to PostgreSQL with that username/password, port 5432, and
`sslmode=require`. It does not authenticate to PostgreSQL using an identity token.
`POSTGRES_PORT`, `POSTGRES_USERNAME`, and `POSTGRES_PASSWORD` are not used by this
mode. The process needs both permission to read the secret and network access
to the vault and database.

Connection strings currently interpolate credentials directly; URL-reserved
characters in credentials may require a code change to construct URLs safely.

## Storage and logging

| Variable | Behavior |
|---|---|
| `AZURE_STORAGE_ACCOUNT_NAME` | Required by the scraper CLI, even while image downloads are disabled |
| `AZURE_STORAGE_CONTAINER_NAME` | Defaults to `listing-images` |
| `LOG_LEVEL` | Defaults to `info`; overridden by `--log-level` |
| `CHROMIUM_IN_CONTAINER` | Set to `1` by the Dockerfile for container browser configuration |

## Scraper command

```bash
uv run apartment-finder-scrape "<search-url>" \
  --max-pages 1 --max-workers 4 --request-delay 3 --log-level info
```

| Argument | Default | Meaning |
|---|---|---|
| `search_url` | Required | imot.bg search/results URL |
| `--max-pages` | `1` | Maximum number of search pages |
| `--max-workers` | `4` | Concurrent listing workers |
| `--request-delay` | `3.0` | Minimum seconds between paced requests within a scraper instance |
| `--log-level` | Environment/default | `debug`, `info`, `warning`, `error`, or `critical` |

## Temporal

| Variable | Default / behavior |
|---|---|
| `TEMPORAL_ADDRESS` | `localhost:7233` |
| `TEMPORAL_NAMESPACE` | `default` |
| `TEMPORAL_TASK_QUEUE` | `apartment-finder` |
| `TEMPORAL_API_KEY` | Optional; supplying it also enables TLS |
| `TEMPORAL_TLS` | The exact value `true` enables TLS |

CLI `--address`, `--namespace`, and `--task-queue` options precede the `worker`
or `schedule` subcommand. The schedule accepts the scrape options above except
`--log-level`, plus `--schedule-id` and `--timeout-minutes` (default 60).
See [operations](operations.md) for examples.

## Training

Training dependencies are optional. Teacher provider credentials, labeling
configuration, dataset formats, and fine-tuning options are documented in the
[training guide](../training/README.md). Use `--help` on the relevant module for
its complete argument list. Teacher labeling can send description text to an
external provider and incur charges.
