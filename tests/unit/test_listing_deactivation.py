from datetime import UTC, datetime

import pytest

from apartment_finder.domain.entities import Listing, ListingId, TransactionType
from apartment_finder.infrastructure.persistence.models import Base
from apartment_finder.infrastructure.persistence.postgres_listing_repository import (
    PostgresListingRepository,
)


@pytest.fixture
def repository():
    repo = PostgresListingRepository("sqlite:///:memory:")
    Base.metadata.create_all(repo._engine)
    yield repo
    repo._engine.dispose()


def listing(external_id, **kwargs):
    return Listing(
        id=ListingId("test", external_id),
        is_active=kwargs.pop("is_active", True),
        url="https://example.test",
        title="Apartment",
        price=None,
        transaction_type=TransactionType.SALE,
        area_sqm=None,
        floor=None,
        city="City",
        area="Area",
        street=None,
        description="",
        **kwargs,
    )


def test_deactivation_sets_date_only_once_and_leaves_present_ads_active(repository):
    present, missing = listing("present"), listing("missing")
    repository.save(present)
    repository.save(missing)
    before = datetime.now(UTC).replace(tzinfo=None)
    updated = repository.mark_inactive([present])
    stored = {ad.id.external_id: ad for ad in repository.all()}
    assert updated == [stored["missing"]]
    assert stored["present"].is_active
    assert stored["present"].deactivated_at is None
    assert not stored["missing"].is_active
    first_date = stored["missing"].deactivated_at
    # SQLite drops timezone information; Postgres stores a timezone-aware timestamp.
    assert before <= first_date <= datetime.now(UTC).replace(tzinfo=None)
    updated = repository.mark_inactive([])
    stored = {ad.id.external_id: ad for ad in repository.all()}
    assert updated == [stored["present"]]
    assert stored["missing"].deactivated_at == first_date
    assert not stored["present"].is_active
    assert stored["present"].deactivated_at is not None
    assert repository.mark_inactive([]) == []


def test_mark_inactive_returns_empty_when_all_ads_are_present(repository):
    present = listing("present")
    repository.save(present)
    assert repository.mark_inactive([present]) == []
    assert repository.all()[0].is_active


def test_save_inactive_listing_records_date(repository):
    repository.save(listing("inactive", is_active=False))
    assert repository.all()[0].deactivated_at is not None


def test_save_preserves_supplied_deactivation_date(repository):
    deactivated_at = datetime(2026, 1, 1, tzinfo=UTC)
    repository.save(listing("inactive", is_active=False, deactivated_at=deactivated_at))
    assert repository.all()[0].deactivated_at == deactivated_at.replace(tzinfo=None)
