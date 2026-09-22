"""
Run with local Postgres up: `make up && make migrate`, then `make test-integration`
(or directly: `APP_ENV=local uv run pytest tests/integration -v`)

Unlike the unit tests (which use fakes and run in milliseconds), this
talks to a real Postgres instance — proving the SQL, the ORM mapping,
and the unique constraint actually behave as expected together.
"""

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, make_url
from sqlalchemy.exc import IntegrityError
from sqlalchemy.schema import CreateSchema, DropSchema

from apartment_finder.domain.entities import (
    Currency,
    Listing,
    ListingId,
    Money,
    SearchListingsResult,
    TransactionType,
)
from apartment_finder.infrastructure.persistence.models import Base
from apartment_finder.infrastructure.persistence.postgres_listing_repository import (
    PostgresListingRepository,
)
from apartment_finder.infrastructure.persistence.settings import (
    build_postgres_connection_url,
)


@pytest.fixture
def repository(monkeypatch):
    monkeypatch.setenv("APP_ENV", "local")
    url = make_url(build_postgres_connection_url())
    if url.host not in {"localhost", "127.0.0.1", "::1", "postgres"}:
        pytest.fail("Integration tests require a local PostgreSQL host")
    schema = f"test_listings_{uuid4().hex}"
    admin = create_engine(url, connect_args={"connect_timeout": 5})
    repo = None
    created = False
    try:
        with admin.begin() as connection:
            connection.execute(CreateSchema(schema))
        created = True
        test_url = url.update_query_dict({"options": f"-csearch_path={schema}"})
        repo = PostgresListingRepository(test_url.render_as_string(hide_password=False))
        Base.metadata.create_all(repo._engine)
        yield repo
    finally:
        if repo is not None:
            repo._engine.dispose()
        try:
            if created:
                with admin.begin() as connection:
                    connection.execute(DropSchema(schema, cascade=True))
        finally:
            admin.dispose()


def make_test_listing(external_id: str) -> Listing:
    return Listing(
        id=ListingId(source="test-source", external_id=external_id),
        url=f"https://example.test/{external_id}",
        title="Test apartment",
        price=Money(amount=100000, currency=Currency.EUR),
        transaction_type=TransactionType.SALE,
        area_sqm=55.0,
        floor=3,
        city="София",
        area="Лозенец",
        street="ул. Мур",
        description="A nice test apartment",
    )


def make_search_result(
    source: str, external_id: str, amount: float | None = 100000
) -> SearchListingsResult:
    return SearchListingsResult(
        id=ListingId(source=source, external_id=external_id),
        url=f"https://example.test/{external_id}",
        title=f"Result {external_id}",
        price=Money(amount=amount, currency=Currency.EUR) if amount is not None else None,
        transaction_type=TransactionType.SALE,
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


def test_duplicate_source_external_id_and_price_is_rejected(repository):
    listing = make_test_listing("t3")
    repository.save(listing)

    with pytest.raises(IntegrityError):
        repository.save(listing)


def test_same_listing_with_a_different_price_can_be_saved(repository):
    repository.save(make_test_listing("price-change"))
    changed = make_test_listing("price-change")
    changed.price = Money(amount=110000, currency=Currency.EUR)
    repository.save(changed)
    assert sorted(ad.price.amount for ad in repository.all()) == [100000, 110000]


@pytest.mark.parametrize(
    ("stored_amount", "found_amount", "unseen"),
    [
        (None, None, False),
        (100000, None, True),
        (None, 100000, True),
        (100000, 110000, True),
        (100000.1, 100000.1, False),
    ],
)
def test_filter_unseen_handles_optional_and_changed_prices(
    repository, stored_amount, found_amount, unseen
):
    listing = make_test_listing("price")
    listing.price = (
        Money(amount=stored_amount, currency=Currency.EUR) if stored_amount is not None else None
    )
    repository.save(listing)
    result = make_search_result("test-source", "price", found_amount)
    assert repository.filter_unseen([result]) == ([result] if unseen else [])


def test_mark_inactive_returns_updated_records_and_preserves_timestamp(repository):
    present, missing = make_test_listing("present"), make_test_listing("missing")
    repository.save(present)
    repository.save(missing)
    before = datetime.now(UTC)
    updated = repository.mark_inactive([present])
    assert len(updated) == 1
    assert updated[0].id == missing.id
    assert not updated[0].is_active
    first_date = updated[0].deactivated_at
    assert first_date.tzinfo is not None
    assert before <= first_date <= datetime.now(UTC)
    assert repository.mark_inactive([present]) == []
    stored = {ad.id: ad for ad in repository.all()}
    assert stored[missing.id].deactivated_at == first_date
    assert stored[present.id].is_active
    assert stored[present.id].deactivated_at is None
    assert [ad.id for ad in repository.mark_inactive([])] == [present.id]
    assert repository.mark_inactive([]) == []


def test_filter_unseen_returns_only_results_absent_from_database(repository):
    repository.save(make_test_listing("stored"))
    stored = make_search_result("test-source", "stored")
    unseen = make_search_result("test-source", "unseen")
    same_external_id_from_another_source = make_search_result("another-source", "stored")

    assert repository.filter_unseen([stored, unseen, same_external_id_from_another_source]) == [
        unseen,
        same_external_id_from_another_source,
    ]


def test_filter_unseen_accepts_an_empty_list(repository):
    assert repository.filter_unseen([]) == []
