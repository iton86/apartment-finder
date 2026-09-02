"""
Use case layer — orchestration logic that reads like the business
requirement it implements, with zero mention of Playwright, Postgres,
or Telegram. It only talks to the abstract ports.
"""

from dataclasses import dataclass

from apartment_finder.application.ports import (
    ImageDownloader,
    ListingRepository,
    SiteScraper,
)
from apartment_finder.domain.entities import Listing


@dataclass
class ScrapeResult:
    total_found: int
    new_listings: list[Listing]
    skipped_duplicates: int


class ScrapeAndStoreListings:
    """
    'As a user, I want new listings from a site collected and saved,
    without re-processing ones I've already seen.'

    This use case doesn't know or care whether the scraper is Playwright-based,
    whether storage is Postgres, or whether images end up on local disk or
    Azure Blob — it only knows the shapes defined in application/ports.
    """

    def __init__(
        self,
        scraper: SiteScraper,
        image_downloader: ImageDownloader,
        repository: ListingRepository,
    ):
        self._scraper = scraper
        self._image_downloader = image_downloader
        self._repository = repository

    def execute(self, search_url: str, max_pages: int = 1) -> ScrapeResult:
        listing_urls = self._scraper.discover_listing_urls(search_url, max_pages)

        new_listings: list[Listing] = []
        skipped = 0

        for url in listing_urls:
            listing = self._scraper.scrape_listing(url)

            if self._repository.exists(listing.id):
                skipped += 1
                continue

            self._image_downloader.download(listing)
            self._repository.save(listing)
            new_listings.append(listing)

        return ScrapeResult(
            total_found=len(listing_urls),
            new_listings=new_listings,
            skipped_duplicates=skipped,
        )
