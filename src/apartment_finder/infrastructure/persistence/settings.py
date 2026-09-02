"""
Two ways to get a connection URL, chosen explicitly by APP_ENV — never
guessed, because guessing wrong here means accidentally hitting the
wrong database.

  APP_ENV=local (default)  -> reads everything from .env / plain env vars.
                               No Azure login needed. Safe to run anywhere,
                               anytime, without touching real data.

  APP_ENV=cloud             -> non-secret details from env vars, password
                               fetched live from Key Vault via
                               DefaultAzureCredential. Used for the real
                               Azure database only.
"""

import os

from dotenv import load_dotenv

# Loads .env into os.environ if present. Safe to call even if the file
# doesn't exist (e.g. in CI, where real env vars are set directly) —
# it just does nothing in that case.
load_dotenv()


def build_postgres_connection_url() -> str:
    app_env = os.environ.get("APP_ENV", "local")

    if app_env == "local":
        return _build_local_url()
    elif app_env == "cloud":
        return _build_cloud_url()
    else:
        raise ValueError(f"Unknown APP_ENV '{app_env}' — expected 'local' or 'cloud'")


def _build_local_url() -> str:
    host = os.environ.get("POSTGRES_HOST", "localhost")
    port = os.environ.get("POSTGRES_PORT", "5432")
    database = os.environ.get("POSTGRES_DATABASE", "apartment_finder")
    username = os.environ.get("POSTGRES_USERNAME", "apartment_finder")
    password = os.environ.get("POSTGRES_PASSWORD", "local_dev_only")

    return f"postgresql+psycopg://{username}:{password}@{host}:{port}/{database}"


def _build_cloud_url() -> str:
    # Deferred import: azure-identity/azure-keyvault-secrets are only
    # needed in cloud mode, so local dev never has to import them.
    from azure.identity import DefaultAzureCredential
    from azure.keyvault.secrets import SecretClient

    host = os.environ["POSTGRES_HOST"]
    database = os.environ["POSTGRES_DATABASE"]
    username = os.environ["POSTGRES_ADMIN_USERNAME"]
    key_vault_name = os.environ["KEY_VAULT_NAME"]
    secret_name = os.environ.get("POSTGRES_PASSWORD_SECRET_NAME", "postgres-admin-password")

    vault_url = f"https://{key_vault_name}.vault.azure.net"
    client = SecretClient(vault_url=vault_url, credential=DefaultAzureCredential())
    password = client.get_secret(secret_name).value

    return f"postgresql+psycopg://{username}:{password}@{host}:5432/{database}?sslmode=require"
