"""Enrich scraped listing descriptions using a running local MLflow model."""

import argparse
import os

from sqlalchemy import create_engine

from apartment_finder.application.use_cases.enrich_listings import enrich_listings
from apartment_finder.infrastructure.persistence.settings import build_postgres_connection_url
from apartment_finder.infrastructure.poi.http_predictor import MlflowHttpPredictor
from apartment_finder.infrastructure.poi.repository import ListingPoiRepository


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:5001")
    parser.add_argument(
        "--model-version", required=True, help="Fingerprint printed at registration"
    )
    parser.add_argument("--limit", type=int, default=100)
    args = parser.parse_args()
    if not args.model_version.strip():
        parser.error("model-version must be the fingerprint printed at registration")
    if args.limit < 1:
        parser.error("limit must be positive")
    if os.environ.get("APP_ENV", "local") != "local":
        parser.error("This enrichment command targets local deployment only")
    engine = create_engine(build_postgres_connection_url(), pool_pre_ping=True)
    try:
        count = enrich_listings(
            ListingPoiRepository(engine),
            MlflowHttpPredictor(args.url),
            args.model_version,
            args.limit,
        )
        print(f"Saved POIs on {count} listing(s).")
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
