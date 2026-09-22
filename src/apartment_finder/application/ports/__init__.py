"""
Ports — abstract contracts. This is the key clean-architecture idea:
the application layer describes WHAT it needs (scrape a site, save an
image, persist a listing) without knowing HOW it happens. Infrastructure
implements these interfaces; application never imports infrastructure.
"""

from abc import ABC, abstractmethod
from collections.abc import Iterator, Sequence
from dataclasses import dataclass

from apartment_finder.domain.entities import DownloadedImage, Listing, SearchListingsResult


@dataclass
class ScrapeOutcome:
    """One listing's result, success or failure.

    The failure is carried as data rather than raised because these arrive
    in a batch: on a 500-listing run, one ad with broken markup shouldn't
    abandon the 499 that would have worked.
    """

    url: str
    listing: Listing | None = None
    error: Exception | None = None


class SiteScraper(ABC):
    """Anything that can turn a search page into listing URLs, and a
    listing URL into a Listing, qualifies — regardless of whether it's
    built with Playwright, requests, or something else entirely."""

    @abstractmethod
    def discover_listing_urls(
        self, search_url: str, max_pages: int
    ) -> list[SearchListingsResult]: ...

    @abstractmethod
    def scrape_listing(self, url: str) -> Listing: ...

    def scrape_listings(self, urls: Sequence[str]) -> Iterator[ScrapeOutcome]:
        """Scrape many listings, yielding each as it finishes.

        Concrete, not abstract, and deliberately: this sequential version is
        a correct implementation of the contract, so a test double or a
        simpler adapter gets batch semantics for free and only an adapter
        that can genuinely overlap requests needs to override it.

        Implementations may yield in any order — callers that need the
        original order must sort by themselves.
        """
        for url in urls:
            try:
                yield ScrapeOutcome(url=url, listing=self.scrape_listing(url))
            except Exception as exc:
                yield ScrapeOutcome(url=url, error=exc)


class ImageDownloader(ABC):
    @abstractmethod
    def download(self, listing: Listing) -> list[DownloadedImage]: ...


class ListingRepository(ABC):
    """Persistence contract — could be backed by Postgres, an in-memory
    dict for tests, or anything else."""

    @abstractmethod
    def save(self, listing: Listing) -> None: ...

    @abstractmethod
    def exists(self, listing_id) -> bool: ...

    @abstractmethod
    def filter_unseen(self, results: list[SearchListingsResult]) -> list[SearchListingsResult]: ...

    @abstractmethod
    def all(self) -> list[Listing]: ...

    @abstractmethod
    def mark_inactive(self, listings: list[Listing] | list[SearchListingsResult]) -> list[Listing]:
        """Reconcile against a complete inventory; never pass scoped search results.

        Return newly deactivated listings. An empty inventory deactivates all.
        """
        ...


class NotificationSender(ABC):
    @abstractmethod
    def notify(self, listing: Listing) -> None: ...
