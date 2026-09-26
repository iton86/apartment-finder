from datetime import UTC, datetime, timedelta

import pytest

from apartment_finder.domain.entities import ListingId, ListingStatus
from apartment_finder.infrastructure.persistence.in_memory_listing_repository import (
    InMemoryListingRepository,
)
from apartment_finder.infrastructure.persistence.models import Base
from apartment_finder.infrastructure.persistence.postgres_listing_repository import (
    PostgresListingRepository,
)
from tests.unit.test_scrape_and_store_listings import FakeScraper


@pytest.fixture(params=["memory", "sql"])
def repository(request):
    if request.param == "memory":
        yield InMemoryListingRepository()
    else:
        repo = PostgresListingRepository("sqlite:///:memory:")
        Base.metadata.create_all(repo._engine)
        yield repo
        repo._engine.dispose()


@pytest.mark.parametrize(
    ("age", "expected"),
    [
        (timedelta(days=2), ListingStatus.UNREACHABLE),
        (timedelta(days=3), ListingStatus.UNREACHABLE),
        (timedelta(days=3, microseconds=1), ListingStatus.EXPIRED),
    ],
)
def test_failure_preserves_sighting_and_recovery_restores_active(repository, age, expected):
    now = datetime.now(UTC)
    listing = FakeScraper().scrape_listing("https://fake.test/listing/1")
    listing.last_seen_at = now - age
    other = FakeScraper().scrape_listing("https://fake.test/listing/2")
    other.id = ListingId("other", "1")
    repository.save(listing)
    repository.save(other)
    repository.record_unreachable(listing.id, now)
    stored = {ad.id: ad for ad in repository.all()}
    assert stored[listing.id].status == expected
    assert stored[listing.id].last_seen_at == now - age
    assert stored[other.id].status == ListingStatus.ACTIVE
    repository.record_seen({listing.id}, now)
    stored = {ad.id: ad for ad in repository.all()}
    assert stored[listing.id].status == ListingStatus.ACTIVE
    assert stored[listing.id].last_seen_at == now


def test_unknown_ids_and_empty_sightings_do_not_modify_records(repository):
    listing = FakeScraper().scrape_listing("https://fake.test/listing/1")
    repository.save(listing)
    repository.record_seen(set(), datetime.now(UTC))
    repository.record_unreachable(ListingId("other", "1"), datetime.now(UTC))
    assert repository.all()[0].status == ListingStatus.ACTIVE
