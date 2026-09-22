from datetime import UTC, datetime

from apartment_finder.domain.entities import ListingId
from apartment_finder.infrastructure.persistence.in_memory_listing_repository import (
    InMemoryListingRepository,
)
from tests.unit.test_scrape_and_store_listings import FakeScraper


def test_deactivation_matches_full_id_and_preserves_dates_on_repeated_calls():
    repository = InMemoryListingRepository()
    scraper = FakeScraper()
    present = scraper.scrape_listing("https://fake.test/listing/1")
    missing = scraper.scrape_listing("https://fake.test/listing/1")
    missing.id = ListingId("another-source", "1")
    repository.save(present)
    repository.save(missing)

    before = datetime.now(UTC)
    updated = repository.mark_inactive([scraper._search_result("1")])
    assert updated == [missing]
    assert not missing.is_active
    assert before <= missing.deactivated_at <= datetime.now(UTC)
    first_date = missing.deactivated_at
    assert present.is_active
    assert present.deactivated_at is None

    assert repository.mark_inactive([present]) == []
    assert missing.deactivated_at == first_date
    assert repository.mark_inactive([]) == [present]
    assert not present.is_active
    assert present.deactivated_at is not None
    assert missing.deactivated_at == first_date
    assert repository.mark_inactive([]) == []
