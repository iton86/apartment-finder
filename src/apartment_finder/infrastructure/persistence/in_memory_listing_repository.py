"""
A minimal in-memory implementation of ListingRepository. Useful for
local runs and tests before the real Postgres-backed repository exists.
Swapping this for a Postgres implementation later means writing one new
file here — the use case and domain layers never change.
"""

from datetime import UTC, datetime

from apartment_finder.application.ports import ListingRepository
from apartment_finder.domain.entities import Listing, SearchListingsResult


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

    def mark_inactive(self, listings: list[Listing] | list[SearchListingsResult]) -> list[Listing]:
        """Deactivate ads absent from a complete inventory, returning only changed ads."""
        active_ids = {listing.id for listing in listings}
        deactivated_at = datetime.now(UTC)
        updated = []
        for listing in self._listings.values():
            if listing.is_active and listing.id not in active_ids:
                listing.is_active = False
                listing.deactivated_at = deactivated_at
                updated.append(listing)
        return updated
