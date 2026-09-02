"""
Interface layer — the composition root. This is the ONLY place in the
whole codebase that imports concrete infrastructure classes AND the use
case together, and wires them up. Every other layer only ever sees
abstractions (ports).

Run with: uv run apartment-finder-scrape
"""

import argparse
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
from apartment_finder.infrastructure.scrapers.imot_bg_scraper import ImotBgScraper
from apartment_finder.infrastructure.storage.azure_blob_image_downloader import (
    AzureBlobImageDownloader,
)


def main() -> None:
    parser = argparse.ArgumentParser(description="Scrape apartment listings")
    parser.add_argument("search_url", help="Search/results page URL to scrape")
    parser.add_argument("--max-pages", type=int, default=1)
    args = parser.parse_args()

    storage_account_name = os.environ["AZURE_STORAGE_ACCOUNT_NAME"]
    container_name = os.environ.get("AZURE_STORAGE_CONTAINER_NAME", "listing-images")

    # --- Composition: swap any of these three lines to change behavior
    # without touching application or domain code. E.g. replace
    # InMemoryListingRepository with a future PostgresListingRepository.
    scraper = ImotBgScraper(request_delay_seconds=3.0)
    image_downloader = AzureBlobImageDownloader(
        storage_account_name=storage_account_name,
        container_name=container_name,
    )
    repository = PostgresListingRepository(connection_url=build_postgres_connection_url())

    use_case = ScrapeAndStoreListings(scraper, image_downloader, repository)

    result = use_case.execute(args.search_url, max_pages=args.max_pages)

    print(f"Found {result.total_found} listings.")
    print(f"Saved {len(result.new_listings)} new listings.")
    print(f"Skipped {result.skipped_duplicates} duplicates already seen.")

    for listing in result.new_listings:
        print(f"  - {listing.title} | {listing.price} | {listing.location}")


if __name__ == "__main__":
    main()
