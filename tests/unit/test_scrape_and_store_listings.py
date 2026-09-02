"""
This test never touches Playwright, the filesystem, or a database — it
uses fake in-memory implementations of every port. That's the payoff of
clean architecture: the core business logic (the use case) can be fully
tested in milliseconds, with no network, no browser, no DB.
"""

from apartment_finder.application.ports import ImageDownloader, SiteScraper
from apartment_finder.application.use_cases.scrape_and_store_listings import (
    ScrapeAndStoreListings,
)
from apartment_finder.domain.entities import Currency, Listing, ListingId, Money
from apartment_finder.infrastructure.persistence.in_memory_listing_repository import (
    InMemoryListingRepository,
)


class FakeScraper(SiteScraper):
    """A test double — returns canned data instead of hitting a real site."""

    def discover_listing_urls(self, search_url: str, max_pages: int) -> list[str]:
        return ["https://fake.test/listing/1", "https://fake.test/listing/2"]

    def scrape_listing(self, url: str) -> Listing:
        ext_id = url.rstrip("/").split("/")[-1]
        return Listing(
            id=ListingId(source="fake.test", external_id=ext_id),
            url=url,
            title=f"Test listing {ext_id}",
            price=Money(amount=100000, currency=Currency.EUR),
            area_sqm=60.0,
            floor="2nd of 5",
            location="Sofia, Center",
            description="A nice test apartment",
            image_urls=["https://fake.test/img1.jpg"],
        )


class FakeImageDownloader(ImageDownloader):
    def download(self, listing: Listing) -> list:
        return []  # no-op — image downloading isn't what this test verifies


def test_scrapes_and_saves_new_listings():
    repository = InMemoryListingRepository()
    use_case = ScrapeAndStoreListings(FakeScraper(), FakeImageDownloader(), repository)

    result = use_case.execute("https://fake.test/search", max_pages=1)

    assert result.total_found == 2
    assert len(result.new_listings) == 2
    assert result.skipped_duplicates == 0
    assert len(repository.all()) == 2


def test_skips_listings_already_in_repository():
    repository = InMemoryListingRepository()
    use_case = ScrapeAndStoreListings(FakeScraper(), FakeImageDownloader(), repository)

    use_case.execute("https://fake.test/search", max_pages=1)
    # Running it again should skip both, since they're already saved.
    result = use_case.execute("https://fake.test/search", max_pages=1)

    assert result.skipped_duplicates == 2
    assert len(result.new_listings) == 0
