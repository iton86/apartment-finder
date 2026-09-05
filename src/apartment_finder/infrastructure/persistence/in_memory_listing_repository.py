"""
A minimal in-memory implementation of ListingRepository. Useful for
local runs and tests before the real Postgres-backed repository exists.
Swapping this for a Postgres implementation later means writing one new
file here — the use case and domain layers never change.
"""

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
