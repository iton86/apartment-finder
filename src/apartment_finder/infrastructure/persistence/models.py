"""
SQLAlchemy models — the infrastructure-layer mirror of sql/schema.sql.
These are deliberately kept separate from domain.entities.Listing: the
domain entity is what the business logic works with, these are what
gets stored in specific rows and columns. PostgresListingRepository is
the only place that translates between the two.
"""

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class ListingModel(Base):
    __tablename__ = "listings"
    __table_args__ = (UniqueConstraint("source", "external_id"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    source: Mapped[str] = mapped_column(String, nullable=False)
    external_id: Mapped[str] = mapped_column(String, nullable=False)
    url: Mapped[str] = mapped_column(Text, nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    price_amount: Mapped[float | None] = mapped_column(Numeric(12, 2))
    price_currency: Mapped[str | None] = mapped_column(String)
    area_sqm: Mapped[float | None] = mapped_column(Numeric(8, 2))
    floor: Mapped[str | None] = mapped_column(Text)
    location: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    phone: Mapped[str | None] = mapped_column(Text)
    scraped_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    images: Mapped[list["ListingImageModel"]] = relationship(
        back_populates="listing", cascade="all, delete-orphan"
    )


class ListingImageModel(Base):
    __tablename__ = "listing_images"
    __table_args__ = (UniqueConstraint("listing_id", "sequence"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    listing_id: Mapped[int] = mapped_column(ForeignKey("listings.id", ondelete="CASCADE"))
    sequence: Mapped[int] = mapped_column(nullable=False)
    storage_path: Mapped[str] = mapped_column(Text, nullable=False)

    listing: Mapped["ListingModel"] = relationship(back_populates="images")
