"""
Postgres implementation of ListingRepository. This is the only file that
knows SQLAlchemy exists — the use case that calls it only sees the
abstract ListingRepository port, exactly like InMemoryListingRepository.
"""

import logging
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import case, create_engine, make_url, select, tuple_, update
from sqlalchemy.orm import Session

from apartment_finder.application.ports import ListingRepository
from apartment_finder.domain.entities import (
    Currency,
    Listing,
    ListingId,
    ListingStatus,
    Money,
    SearchListingsResult,
)
from apartment_finder.infrastructure.persistence.models import (
    ListingModel,
)

logger = logging.getLogger(__name__)


class PostgresListingRepository(ListingRepository):
    def __init__(self, connection_url: str):
        # pool_pre_ping avoids using a dead connection after the DB has
        # been idle — cheap safety net for a server that isn't hit constantly.
        self._engine = create_engine(connection_url, pool_pre_ping=True)
        # NEVER log connection_url directly — it carries the password, and in
        # cloud mode that's the Key Vault secret. render_as_string masks it.
        safe_url = make_url(connection_url).render_as_string(hide_password=True)
        logger.info("Connected to %s", safe_url)

    def save(self, listing: Listing) -> None:
        with Session(self._engine) as session:
            model = ListingModel(
                source=listing.id.source,
                external_id=listing.id.external_id,
                status=listing.status,
                last_seen_at=listing.last_seen_at,
                url=listing.url,
                title=listing.title,
                transaction_type=listing.transaction_type,
                rooms=listing.rooms,
                price_amount=Decimal(str(listing.price.amount)) if listing.price else None,
                price_currency=listing.price.currency.value if listing.price else None,
                area_sqm=Decimal(str(listing.area_sqm)) if listing.area_sqm is not None else None,
                floor=listing.floor,
                building_floors=listing.building_floors,
                city=listing.city,
                area=listing.area,
                street=listing.street,
                description=listing.description,
                agency_name=listing.agency_name,
                apartment_id=listing.apartment_id,
                phone=listing.phone,
            )
            session.add(model)
            session.commit()
            logger.debug("Committed listing %s (row id=%s)", listing.id, model.id)

    def exists(self, listing_id: ListingId) -> bool:
        with Session(self._engine) as session:
            stmt = select(ListingModel.id).where(
                ListingModel.source == listing_id.source,
                ListingModel.external_id == listing_id.external_id,
            )
            found = session.execute(stmt).first() is not None
            logger.debug("Dedup check %s -> %s", listing_id, "hit" if found else "miss")
            return found

    def filter_unseen(self, results: list[SearchListingsResult]) -> list[SearchListingsResult]:
        """Return results without a matching source, external ID, and price amount."""
        if not results:
            return []

        candidate_keys = {(result.id.source, result.id.external_id) for result in results}

        with Session(self._engine) as session:
            stmt = select(
                ListingModel.source, ListingModel.external_id, ListingModel.price_amount
            ).where(tuple_(ListingModel.source, ListingModel.external_id).in_(candidate_keys))
            stored_keys = {tuple(row) for row in session.execute(stmt)}

        unseen_results = [
            result
            for result in results
            if (
                result.id.source,
                result.id.external_id,
                Decimal(str(result.price.amount)) if result.price is not None else None,
            )
            not in stored_keys
        ]
        logger.debug(
            "Filtered %d search result(s): %d unseen, %d already stored",
            len(results),
            len(unseen_results),
            len(results) - len(unseen_results),
        )
        return unseen_results

    def all(self) -> list[Listing]:
        with Session(self._engine) as session:
            models = session.execute(select(ListingModel)).scalars().all()
            logger.debug("Loaded %d listing(s) from the database", len(models))
            return [self._to_entity(m) for m in models]

    def record_seen(self, listing_ids: set[ListingId], seen_at: datetime) -> None:
        if not listing_ids:
            return
        keys = {(key.source, key.external_id) for key in listing_ids}
        with Session(self._engine) as session:
            session.execute(
                update(ListingModel)
                .where(tuple_(ListingModel.source, ListingModel.external_id).in_(keys))
                .values(status=ListingStatus.ACTIVE, last_seen_at=seen_at)
            )
            session.commit()

    def record_unreachable(self, listing_id: ListingId, checked_at: datetime) -> None:
        with Session(self._engine) as session:
            session.execute(
                update(ListingModel)
                .where(
                    ListingModel.source == listing_id.source,
                    ListingModel.external_id == listing_id.external_id,
                )
                .values(
                    status=case(
                        (
                            ListingModel.last_seen_at < checked_at - timedelta(days=3),
                            ListingStatus.EXPIRED.value,
                        ),
                        else_=ListingStatus.UNREACHABLE.value,
                    )
                )
            )
            session.commit()

    @staticmethod
    def _to_entity(model: ListingModel) -> Listing:
        price = None
        if model.price_amount is not None and model.price_currency:
            price = Money(amount=float(model.price_amount), currency=Currency(model.price_currency))

        return Listing(
            id=ListingId(source=model.source, external_id=model.external_id),
            status=model.status,
            last_seen_at=(
                model.last_seen_at.replace(tzinfo=UTC)
                if model.last_seen_at.tzinfo is None
                else model.last_seen_at
            ),
            url=model.url,
            title=model.title,
            price=price,
            transaction_type=model.transaction_type,
            area_sqm=float(model.area_sqm) if model.area_sqm is not None else None,
            floor=model.floor,
            city=model.city,
            area=model.area,
            street=model.street,
            description=model.description or "",
            rooms=model.rooms,
            building_floors=model.building_floors,
            agency_name=model.agency_name,
            apartment_id=model.apartment_id,
            phone=model.phone,
        )
