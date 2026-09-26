"""
A minimal in-memory implementation of ListingRepository. Useful for
local runs and tests before the real Postgres-backed repository exists.
Swapping this for a Postgres implementation later means writing one new
file here — the use case and domain layers never change.
"""

from datetime import datetime, timedelta

from apartment_finder.application.ports import ListingRepository
from apartment_finder.domain.entities import Listing, ListingId, ListingStatus, SearchListingsResult


class InMemoryListingRepository(ListingRepository):
    def __init__(self):
        self._listings: dict[str, Listing] = {}

    def save(self, listing: Listing) -> None:
        self._listings[str(listing.id)] = listing

    def exists(self, listing_id) -> bool:
        return str(listing_id) in self._listings

    def filter_unseen(self, results: list[SearchListingsResult]) -> list[SearchListingsResult]:
        return [result for result in results if str(result.id) not in self._listings]

    def all(self) -> list[Listing]:
        return list(self._listings.values())

    def record_seen(self, listing_ids: set[ListingId], seen_at: datetime) -> None:
        for listing in self._listings.values():
            if listing.id in listing_ids:
                listing.last_seen_at = seen_at
                listing.status = ListingStatus.ACTIVE

    def record_unreachable(self, listing_id: ListingId, checked_at: datetime) -> None:
        listing = self._listings.get(str(listing_id))
        if listing is not None:
            listing.status = (
                ListingStatus.EXPIRED
                if listing.last_seen_at < checked_at - timedelta(days=3)
                else ListingStatus.UNREACHABLE
            )
