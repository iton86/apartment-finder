"""
Postgres implementation of ListingRepository. This is the only file that
knows SQLAlchemy exists — the use case that calls it only sees the
abstract ListingRepository port, exactly like InMemoryListingRepository.
"""

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from apartment_finder.application.ports import ListingRepository
from apartment_finder.domain.entities import (
    Currency,
    Listing,
    ListingId,
    Money,
)
from apartment_finder.infrastructure.persistence.models import (
    ListingModel,
)


class PostgresListingRepository(ListingRepository):
    def __init__(self, connection_url: str):
        # pool_pre_ping avoids using a dead connection after the DB has
        # been idle — cheap safety net for a server that isn't hit constantly.
        self._engine = create_engine(connection_url, pool_pre_ping=True)

    def save(self, listing: Listing) -> None:
        with Session(self._engine) as session:
            model = ListingModel(
                source=listing.id.source,
                external_id=listing.id.external_id,
                url=listing.url,
                title=listing.title,
                price_amount=listing.price.amount if listing.price else None,
                price_currency=listing.price.currency.value if listing.price else None,
                area_sqm=listing.area_sqm,
                floor=listing.floor,
                location=listing.location,
                description=listing.description,
                phone=listing.phone,
            )
            session.add(model)
            session.commit()

    def exists(self, listing_id: ListingId) -> bool:
        with Session(self._engine) as session:
            stmt = select(ListingModel.id).where(
                ListingModel.source == listing_id.source,
                ListingModel.external_id == listing_id.external_id,
            )
            return session.execute(stmt).first() is not None

    def all(self) -> list[Listing]:
        with Session(self._engine) as session:
            models = session.execute(select(ListingModel)).scalars().all()
            return [self._to_entity(m) for m in models]

    @staticmethod
    def _to_entity(model: ListingModel) -> Listing:
        price = None
        if model.price_amount is not None and model.price_currency:
            price = Money(amount=float(model.price_amount), currency=Currency(model.price_currency))

        return Listing(
            id=ListingId(source=model.source, external_id=model.external_id),
            url=model.url,
            title=model.title,
            price=price,
            area_sqm=float(model.area_sqm) if model.area_sqm is not None else None,
            floor=model.floor,
            location=model.location,
            description=model.description or "",
            phone=model.phone,
        )
