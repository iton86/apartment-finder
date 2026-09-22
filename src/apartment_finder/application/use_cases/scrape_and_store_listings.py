"""
Use case layer — orchestration logic that reads like the business
requirement it implements, with zero mention of Playwright, Postgres,
or Telegram. It only talks to the abstract ports.
"""

import logging
from dataclasses import dataclass

from apartment_finder.application.ports import (
    ImageDownloader,
    ListingRepository,
    SiteScraper,
)
from apartment_finder.domain.entities import Listing

# stdlib logging is allowed here where playwright and sqlalchemy are not:
# it names no technology and no external system, so the layer still reads
# as pure orchestration. The domain layer stays free of it entirely.
logger = logging.getLogger(__name__)


@dataclass
class ScrapeResult:
    total_found: int
    new_listings: list[Listing]
    skipped_duplicates: int
    inactive_ads: int = 0
    # Listings whose page couldn't be scraped at all. Defaulted so existing
    # callers and tests constructing this by hand keep working.
    failed: int = 0


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
        logger.info("Discovering listing URLs from %s (max_pages=%d)", search_url, max_pages)
        search_results = self._scraper.discover_listing_urls(search_url, max_pages)
        unseen_results = self._repository.filter_unseen(search_results)
        # Search results may be partial or scoped to one neighbourhood. Absence
        # here is not evidence that a stored ad has been removed from the site.
        logger.info("Discovered %d listing(s)", len(search_results))
        unseen_ids = {result.id for result in unseen_results}
        for result in search_results:
            if result.id not in unseen_ids:
                logger.debug("Skipping %s — already stored", result.id)
        for result in unseen_results:
            logger.debug("Queued for scraping: %s", result.url)

        new_listings: list[Listing] = []
        skipped = len(search_results) - len(unseen_results)
        failed = 0
        total = len(search_results)
        urls_to_scrape = [result.url for result in unseen_results]

        # scrape_listings decides for itself whether to overlap requests;
        # this loop only knows results arrive one at a time, in whatever
        # order they finish.
        for index, outcome in enumerate(self._scraper.scrape_listings(urls_to_scrape), start=1):
            logger.debug("Scrape result %d/%d: %s", index, len(urls_to_scrape), outcome.url)

            if outcome.error is not None:
                # One unreadable ad shouldn't cost the rest of the run.
                logger.warning(
                    "Skipping %s — scrape failed: %s",
                    outcome.url,
                    outcome.error,
                    exc_info=outcome.error,
                )
                failed += 1
                continue

            listing = outcome.listing
            if self._repository.exists(listing.id):
                # DEBUG, not INFO: on a re-run of the same search almost
                # every listing lands here, and at INFO that buries the
                # handful of lines that actually mattered.
                logger.debug("Skipping %s — already stored", listing.id)
                skipped += 1
                continue

            # self._image_downloader.download(listing)
            self._repository.save(listing)
            logger.info("Saved new listing %s — %s", listing.id, listing.title)
            logger.debug(
                "  price=%s area_sqm=%s floor=%s images=%d location=%s",
                listing.price,
                listing.area_sqm,
                listing.floor,
                len(listing.image_urls),
                f"{listing.city}, {listing.area}",
            )
            new_listings.append(listing)

        logger.info(
            "Scrape complete | found=%d new=%d duplicates=%d failed=%d",
            total,
            len(new_listings),
            skipped,
            failed,
        )

        return ScrapeResult(
            total_found=len(search_results),
            new_listings=new_listings,
            skipped_duplicates=skipped,
            failed=failed,
        )
