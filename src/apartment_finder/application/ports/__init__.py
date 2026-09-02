"""
Ports — abstract contracts. This is the key clean-architecture idea:
the application layer describes WHAT it needs (scrape a site, save an
image, persist a listing) without knowing HOW it happens. Infrastructure
implements these interfaces; application never imports infrastructure.
"""

from abc import ABC, abstractmethod

from apartment_finder.domain.entities import DownloadedImage, Listing


class SiteScraper(ABC):
    """Anything that can turn a search page into listing URLs, and a
    listing URL into a Listing, qualifies — regardless of whether it's
    built with Playwright, requests, or something else entirely."""

    @abstractmethod
    def discover_listing_urls(self, search_url: str, max_pages: int) -> list[str]: ...

    @abstractmethod
    def scrape_listing(self, url: str) -> Listing: ...


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
    def all(self) -> list[Listing]: ...


class NotificationSender(ABC):
    @abstractmethod
    def notify(self, listing: Listing) -> None: ...
