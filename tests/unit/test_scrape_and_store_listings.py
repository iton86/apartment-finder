"""
This test never touches Playwright, the filesystem, or a database — it
uses fake in-memory implementations of every port. That's the payoff of
clean architecture: the core business logic (the use case) can be fully
tested in milliseconds, with no network, no browser, no DB.
"""

import logging

import pytest

from apartment_finder.application.ports import ImageDownloader, SiteScraper
from apartment_finder.application.use_cases.scrape_and_store_listings import (
    ScrapeAndStoreListings,
)
from apartment_finder.domain.entities import (
    Currency,
    Listing,
    ListingId,
    Money,
    SearchListingsResult,
    TransactionType,
)
from apartment_finder.infrastructure.persistence.in_memory_listing_repository import (
    InMemoryListingRepository,
)


class FakeScraper(SiteScraper):
    """A test double — returns canned data instead of hitting a real site."""

    def discover_listing_urls(self, search_url: str, max_pages: int) -> list[SearchListingsResult]:
        return [self._search_result("1"), self._search_result("2")]

    @staticmethod
    def _search_result(external_id: str) -> SearchListingsResult:
        return SearchListingsResult(
            id=ListingId(source="fake.test", external_id=external_id),
            url=f"https://fake.test/listing/{external_id}",
            title=f"Test listing {external_id}",
            price=Money(amount=100000, currency=Currency.EUR),
            transaction_type=TransactionType.SALE,
        )

    def scrape_listing(self, url: str) -> Listing:
        ext_id = url.rstrip("/").split("/")[-1]
        return Listing(
            id=ListingId(source="fake.test", external_id=ext_id),
            url=url,
            title=f"Test listing {ext_id}",
            price=Money(amount=100000, currency=Currency.EUR),
            transaction_type=TransactionType.SALE,
            area_sqm=60.0,
            floor=2,
            city="София",
            area="Манастирски ливади",
            street="ул. Луи Айер",
            description="A nice test apartment",
            image_urls=["https://fake.test/img1.jpg"],
        )


class FlakyScraper(FakeScraper):
    """Fails on one listing, succeeds on the other. Note it only overrides
    scrape_listing — scrape_listings comes from SiteScraper's default, so
    this also covers the port turning a raise into a ScrapeOutcome."""

    def discover_listing_urls(self, search_url: str, max_pages: int) -> list[SearchListingsResult]:
        return [
            self._search_result("1"),
            self._search_result("boom"),
            self._search_result("3"),
        ]

    def scrape_listing(self, url: str) -> Listing:
        if url.endswith("boom"):
            raise RuntimeError("page did not load")
        return super().scrape_listing(url)


class FakeImageDownloader(ImageDownloader):
    def download(self, listing: Listing) -> list:
        return []  # no-op — image downloading isn't what this test verifies


@pytest.mark.parametrize("empty", [False, True])
def test_search_never_deactivates_ads_missing_from_results(monkeypatch, empty):
    repository = InMemoryListingRepository()
    scraper = FakeScraper()
    outside_search = scraper.scrape_listing("https://fake.test/listing/elsewhere")
    repository.save(outside_search)
    if empty:
        monkeypatch.setattr(scraper, "discover_listing_urls", lambda *args: [])
    use_case = ScrapeAndStoreListings(scraper, FakeImageDownloader(), repository)

    result = use_case.execute("https://fake.test/search", max_pages=1)

    assert result.inactive_ads == 0
    assert outside_search.is_active
    assert outside_search.deactivated_at is None


def test_failed_discovery_does_not_deactivate_stored_ads(monkeypatch):
    repository = InMemoryListingRepository()
    scraper = FakeScraper()
    existing = scraper.scrape_listing("https://fake.test/listing/1")
    repository.save(existing)

    def fail(*args):
        raise RuntimeError("discovery failed")

    monkeypatch.setattr(scraper, "discover_listing_urls", fail)
    with pytest.raises(RuntimeError, match="discovery failed"):
        ScrapeAndStoreListings(scraper, FakeImageDownloader(), repository).execute("search")
    assert existing.is_active
    assert existing.deactivated_at is None


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


def test_one_failed_listing_does_not_abandon_the_rest():
    # Before batching, a single raise from scrape_listing aborted the whole
    # run and lost every listing scraped up to that point.
    repository = InMemoryListingRepository()
    use_case = ScrapeAndStoreListings(FlakyScraper(), FakeImageDownloader(), repository)

    result = use_case.execute("https://fake.test/search", max_pages=1)

    assert result.total_found == 3
    assert result.failed == 1
    assert len(result.new_listings) == 2
    assert len(repository.all()) == 2


def test_failures_are_reported_at_warning_with_the_url(caplog):
    repository = InMemoryListingRepository()
    use_case = ScrapeAndStoreListings(FlakyScraper(), FakeImageDownloader(), repository)

    with caplog.at_level(logging.WARNING):
        use_case.execute("https://fake.test/search", max_pages=1)

    warnings = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert any("listing/boom" in m and "page did not load" in m for m in warnings), warnings


def test_logs_a_summary_and_one_info_line_per_new_listing(caplog):
    repository = InMemoryListingRepository()
    use_case = ScrapeAndStoreListings(FakeScraper(), FakeImageDownloader(), repository)

    with caplog.at_level(logging.INFO):
        use_case.execute("https://fake.test/search", max_pages=1)

    messages = [r.getMessage() for r in caplog.records]
    assert sum("Saved new listing" in m for m in messages) == 2
    assert "Scrape complete | found=2 new=2 duplicates=0 failed=0" in messages


def test_duplicate_skips_stay_out_of_info_output(caplog):
    # A re-run is mostly duplicates. If each one logged at INFO the summary
    # would be buried, so skips are DEBUG-only.
    repository = InMemoryListingRepository()
    use_case = ScrapeAndStoreListings(FakeScraper(), FakeImageDownloader(), repository)
    use_case.execute("https://fake.test/search", max_pages=1)

    caplog.clear()
    with caplog.at_level(logging.INFO):
        use_case.execute("https://fake.test/search", max_pages=1)

    assert not any("Skipping" in r.getMessage() for r in caplog.records)

    caplog.clear()
    with caplog.at_level(logging.DEBUG):
        use_case.execute("https://fake.test/search", max_pages=1)

    assert sum("already stored" in r.getMessage() for r in caplog.records) == 2
