"""
Run with local Postgres up: `make up && make migrate`, then `make test-integration`
(or directly: `APP_ENV=local uv run pytest tests/integration -v`)

Unlike the unit tests (which use fakes and run in milliseconds), this
talks to a real Postgres instance — proving the SQL, the ORM mapping,
and the unique constraint actually behave as expected together.
"""

import pytest

from apartment_finder.domain.entities import Currency, Listing, ListingId, Money
from apartment_finder.infrastructure.persistence.postgres_listing_repository import (
    PostgresListingRepository,
)
from apartment_finder.infrastructure.persistence.settings import (
    build_postgres_connection_url,
)


@pytest.fixture
def repository():
    repo = PostgresListingRepository(connection_url=build_postgres_connection_url())
    yield repo
    # Clean up between tests so they don't interfere with each other.
    from sqlalchemy import text
    from sqlalchemy.orm import Session

    with Session(repo._engine) as session:
        session.execute(text("TRUNCATE listings, listing_images RESTART IDENTITY CASCADE"))
        session.commit()


def make_test_listing(external_id: str) -> Listing:
    return Listing(
        id=ListingId(source="test-source", external_id=external_id),
        url=f"https://example.test/{external_id}",
        title="Test apartment",
        price=Money(amount=100000, currency=Currency.EUR),
        area_sqm=55.0,
        floor="3rd of 6",
        location="Sofia, Lozenets",
        description="A nice test apartment",
    )


def test_save_and_retrieve_listing(repository):
    listing = make_test_listing("t1")
    repository.save(listing)

    all_listings = repository.all()
    assert len(all_listings) == 1
    assert all_listings[0].title == "Test apartment"
    assert all_listings[0].price.amount == 100000


def test_exists_returns_true_after_save(repository):
    listing = make_test_listing("t2")
    assert repository.exists(listing.id) is False

    repository.save(listing)
    assert repository.exists(listing.id) is True


def test_duplicate_source_and_external_id_is_rejected(repository):
    listing = make_test_listing("t3")
    repository.save(listing)

    with pytest.raises(Exception):  # IntegrityError from the UNIQUE constraint
        repository.save(listing)
