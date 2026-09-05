"""
Interface layer — the composition root. This is the ONLY place in the
whole codebase that imports concrete infrastructure classes AND the use
case together, and wires them up. Every other layer only ever sees
abstractions (ports).

Run with: uv run apartment-finder-scrape
"""

import argparse
import logging
import os

from apartment_finder.application.use_cases.scrape_and_store_listings import (
    ScrapeAndStoreListings,
)
from apartment_finder.infrastructure.persistence.postgres_listing_repository import (
    PostgresListingRepository,
)
from apartment_finder.infrastructure.persistence.settings import (
    build_postgres_connection_url,
)
from apartment_finder.infrastructure.scrapers.imot_bg_scraper import (
    DEFAULT_MAX_WORKERS,
    ImotBgScraper,
)
from apartment_finder.infrastructure.storage.azure_blob_image_downloader import (
    AzureBlobImageDownloader,
)
from apartment_finder.interface.logging_config import configure_logging

logger = logging.getLogger(__name__)


def main() -> None:
    parser = argparse.ArgumentParser(description="Scrape apartment listings")
    parser.add_argument("search_url", help="Search/results page URL to scrape")
    parser.add_argument("--max-pages", type=int, default=1)
    parser.add_argument(
        "--max-workers",
        type=int,
        default=DEFAULT_MAX_WORKERS,
        help=f"Listings to scrape concurrently (default: {DEFAULT_MAX_WORKERS}). "
        "Does not raise the request rate — --request-delay controls that.",
    )
    parser.add_argument(
        "--request-delay",
        type=float,
        default=3.0,
        help="Minimum seconds between requests, shared across all workers "
        "(default: 3.0). Lowering this is what actually increases load on "
        "imot.bg, so treat it as the politeness dial.",
    )
    parser.add_argument(
        "--log-level",
        default=None,
        choices=["debug", "info", "warning", "error", "critical"],
        help="Verbosity (default: LOG_LEVEL env var, else info). "
        "'debug' traces every page, parsed field and image.",
    )
    args = parser.parse_args()

    # First thing after parsing: everything below this line may log, and
    # anything logged before configuration would go to the root logger's
    # fallback handler with the wrong format.
    level = configure_logging(args.log_level)
    logger.debug("Logging configured at %s", logging.getLevelName(level))

    storage_account_name = os.environ["AZURE_STORAGE_ACCOUNT_NAME"]
    container_name = os.environ.get("AZURE_STORAGE_CONTAINER_NAME", "listing-images")

    # --- Composition: swap any of these three lines to change behavior
    # without touching application or domain code. E.g. replace
    # InMemoryListingRepository with a future PostgresListingRepository.
    logger.info(
        "Starting scrape | APP_ENV=%s | max_pages=%d | workers=%d | delay=%.1fs | %s",
        os.environ.get("APP_ENV", "local"),
        args.max_pages,
        args.max_workers,
        args.request_delay,
        args.search_url,
    )

    scraper = ImotBgScraper(
        request_delay_seconds=args.request_delay,
        max_workers=args.max_workers,
    )
    image_downloader = AzureBlobImageDownloader(
        storage_account_name=storage_account_name,
        container_name=container_name,
    )
    repository = PostgresListingRepository(connection_url=build_postgres_connection_url())

    use_case = ScrapeAndStoreListings(scraper, image_downloader, repository)

    result = use_case.execute(args.search_url, max_pages=args.max_pages)

    # These stay prints, not logs: they're the command's output, not a
    # record of what it did. Keeping them on stdout means the report
    # survives redirection while logs go to stderr.
    print(f"Found {result.total_found} listings.")
    print(f"Saved {len(result.new_listings)} new listings.")
    print(f"Skipped {result.skipped_duplicates} duplicates already seen.")
    if result.failed:
        print(f"Failed to scrape {result.failed} listings (see logs).")

    for listing in result.new_listings:
        print(f"  - {listing.title} | {listing.price} | {listing.city}, {listing.area}")


if __name__ == "__main__":
    main()
