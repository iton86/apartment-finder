import pytest

from apartment_finder.domain.entities import (
    Currency,
    ListingId,
    Money,
    SearchListingsResult,
    TransactionType,
)
from apartment_finder.infrastructure.persistence.models import Base, ListingModel
from apartment_finder.infrastructure.persistence.postgres_listing_repository import (
    PostgresListingRepository,
)


@pytest.mark.parametrize(
    ("stored_amount", "found_amount", "unseen"),
    [
        (None, None, False),
        (100, None, True),
        (None, 100, True),
        (100, 100, False),
        (100, 101, True),
        (100.1, 100.1, False),
    ],
)
def test_filter_unseen_matches_optional_prices(stored_amount, found_amount, unseen):
    repo = PostgresListingRepository("sqlite:///:memory:")
    try:
        Base.metadata.create_all(repo._engine)
        with repo._engine.begin() as connection:
            connection.execute(
                ListingModel.__table__.insert().values(
                    source="test",
                    external_id="1",
                    price_amount=stored_amount,
                    url="https://example.test",
                    title="Apartment",
                    transaction_type=TransactionType.SALE,
                    city="City",
                    area="Area",
                )
            )
        result = SearchListingsResult(
            id=ListingId("test", "1"),
            title="Apartment",
            url="https://example.test",
            price=Money(found_amount, Currency.EUR) if found_amount is not None else None,
            transaction_type=TransactionType.SALE,
        )
        new_result = SearchListingsResult(
            id=ListingId("test", "new"),
            title="Apartment",
            url="https://example.test/new",
            price=None,
            transaction_type=TransactionType.SALE,
        )
        expected = [result, new_result] if unseen else [new_result]
        assert repo.filter_unseen([result, new_result]) == expected
    finally:
        repo._engine.dispose()
