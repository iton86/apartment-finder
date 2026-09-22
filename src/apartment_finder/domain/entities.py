"""
Domain layer — the innermost circle of clean architecture.

Rules for this layer:
- No imports from playwright, sqlalchemy, or any other library.
- No knowledge of HTTP, files, or databases.
- If you can't explain a piece of code without mentioning a technology
  (Playwright, Postgres, Telegram), it doesn't belong here.
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from uuid import UUID


class Currency(StrEnum):
    EUR = "EUR"
    BGN = "BGN"


@dataclass(frozen=True)
class Money:
    amount: float
    currency: Currency

    def __post_init__(self):
        if self.amount < 0:
            raise ValueError("Price cannot be negative")


class TransactionType(StrEnum):
    SALE = "sale"
    RENT = "rent"


@dataclass(frozen=True)
class ListingId:
    """Wraps the raw external ID so it can't accidentally be confused with
    a plain string elsewhere (e.g. a URL or a title)."""

    source: str  # e.g. "imot.bg"
    external_id: str  # the site's own ad ID

    def __str__(self) -> str:
        return f"{self.source}:{self.external_id}"


@dataclass
class SearchListingsResult:
    """
    Stores ads details visible in the search result page
    """

    id: ListingId
    title: str
    url: str
    price: Money | None
    transaction_type: TransactionType


@dataclass
class Listing:
    """The core business entity — a real estate ad, independent of where
    it came from or how it will be stored."""

    id: ListingId
    url: str
    title: str
    price: Money | None
    transaction_type: TransactionType
    area_sqm: float | None
    floor: int | None
    city: str
    area: str
    street: str | None
    description: str
    rooms: int | None = None
    building_floors: int | None = None
    agency_name: str | None = None
    apartment_id: UUID | None = None
    phone: str | None = None
    image_urls: list[str] = field(default_factory=list)
    is_active: bool = True
    deactivated_at: datetime | None = None

    @property
    def price_per_sqm(self) -> float | None:
        if self.price and self.area_sqm and self.area_sqm > 0:
            return self.price.amount / self.area_sqm
        return None


@dataclass
class DownloadedImage:
    """Represents an image once it has been fetched and saved somewhere —
    the domain doesn't care whether 'somewhere' is local disk or blob storage."""

    listing_id: ListingId
    sequence: int
    storage_path: str
