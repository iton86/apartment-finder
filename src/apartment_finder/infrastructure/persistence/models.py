"""
SQLAlchemy models — the infrastructure-layer mirror of the database schema.

These are deliberately kept separate from domain.entities.Listing: the
domain entity is what the business logic works with, while these classes
describe how that data is persisted.
"""

from datetime import datetime
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import (
    BigInteger,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from apartment_finder.domain.entities import TransactionType


class Base(DeclarativeBase):
    pass


class ApartmentModel(Base):
    __tablename__ = "apartments"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    listings: Mapped[list["ListingModel"]] = relationship(back_populates="apartment")


class ListingModel(Base):
    __tablename__ = "listings"
    __table_args__ = (
        UniqueConstraint("source", "external_id"),
        Index("idx_listings_city_area", "city", "area"),
        Index("idx_listings_price_per_sqm", "area_sqm", "price_amount"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    source: Mapped[str] = mapped_column(String, nullable=False)
    external_id: Mapped[str] = mapped_column(String, nullable=False)
    url: Mapped[str] = mapped_column(Text, nullable=False)
    transaction_type: Mapped[TransactionType] = mapped_column(
        Enum(
            TransactionType,
            name="transaction_type",
            native_enum=False,
            create_constraint=True,
            values_callable=lambda values: [value.value for value in values],
        ),
        nullable=False,
    )
    title: Mapped[str] = mapped_column(Text, nullable=False)
    rooms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    price_amount: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    price_currency: Mapped[str | None] = mapped_column(String)
    area_sqm: Mapped[Decimal | None] = mapped_column(Numeric(8, 2))
    floor: Mapped[int | None] = mapped_column(Integer, nullable=True)
    building_floors: Mapped[int | None] = mapped_column(Integer, nullable=True)
    city: Mapped[str] = mapped_column(Text, nullable=False)
    area: Mapped[str] = mapped_column(Text, nullable=False)
    street: Mapped[str | None] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text)
    agency_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    phone: Mapped[str | None] = mapped_column(Text)
    scraped_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    apartment_id: Mapped[UUID | None] = mapped_column(
        Uuid, ForeignKey("apartments.id"), nullable=True
    )

    apartment: Mapped[ApartmentModel | None] = relationship(back_populates="listings")
    images: Mapped[list["ListingImageModel"]] = relationship(
        back_populates="listing", cascade="all, delete-orphan"
    )


class ListingImageModel(Base):
    __tablename__ = "listing_images"
    __table_args__ = (UniqueConstraint("listing_id", "sequence"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    listing_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("listings.id", ondelete="CASCADE"), nullable=False
    )
    sequence: Mapped[int] = mapped_column(nullable=False)
    storage_path: Mapped[str] = mapped_column(Text, nullable=False)

    listing: Mapped["ListingModel"] = relationship(back_populates="images")
