"""
Domain layer — the innermost circle of clean architecture.

Rules for this layer:
- No imports from playwright, sqlalchemy, or any other library.
- No knowledge of HTTP, files, or databases.
- If you can't explain a piece of code without mentioning a technology
  (Playwright, Postgres, Telegram), it doesn't belong here.
"""

from dataclasses import dataclass, field
from datetime import UTC, datetime
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


class ListingStatus(StrEnum):
    ACTIVE = "active"
    UNREACHABLE = "unreachable"
    EXPIRED = "expired"


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


@dataclass(frozen=True)
class SearchScope:
    """The search filters that make absence from a result page meaningful."""

    area_slugs: frozenset[str] = frozenset()
    rooms: frozenset[int] = frozenset()

    def matches(self, listing: "Listing") -> bool:
        if self.area_slugs and slugify_bg(listing.area) not in self.area_slugs:
            return False
        return not (self.rooms and listing.rooms not in self.rooms)


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
    status: ListingStatus = ListingStatus.ACTIVE
    last_seen_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    @property
    def price_per_sqm(self) -> float | None:
        if self.price and self.area_sqm and self.area_sqm > 0:
            return self.price.amount / self.area_sqm
        return None


_CYRILLIC_TO_LATIN = str.maketrans(
    {
        "а": "a",
        "б": "b",
        "в": "v",
        "г": "g",
        "д": "d",
        "е": "e",
        "ж": "zh",
        "з": "z",
        "и": "i",
        "й": "y",
        "к": "k",
        "л": "l",
        "м": "m",
        "н": "n",
        "о": "o",
        "п": "p",
        "р": "r",
        "с": "s",
        "т": "t",
        "у": "u",
        "ф": "f",
        "х": "h",
        "ц": "ts",
        "ч": "ch",
        "ш": "sh",
        "щ": "sht",
        "ъ": "a",
        "ь": "",
        "ю": "yu",
        "я": "ya",
    }
)


def slugify_bg(value: str) -> str:
    return "-".join(value.lower().translate(_CYRILLIC_TO_LATIN).split())


@dataclass
class DownloadedImage:
    """Represents an image once it has been fetched and saved somewhere —
    the domain doesn't care whether 'somewhere' is local disk or blob storage."""

    listing_id: ListingId
    sequence: int
    storage_path: str
