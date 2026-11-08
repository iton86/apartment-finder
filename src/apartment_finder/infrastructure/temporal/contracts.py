"""Serializable inputs shared by workflows and activities."""

from dataclasses import dataclass
from math import isfinite
from urllib.parse import urlparse

TASK_QUEUE = "apartment-finder"


@dataclass(frozen=True)
class EnrichmentRequest:
    """Settings for a separate post-scrape POI activity."""

    model_version: str
    url: str = "http://127.0.0.1:5001"
    limit: int = 1000
    timeout_minutes: int = 60

    def validate(self) -> None:
        """Validate the service URL, model version and execution limits."""
        url = urlparse(self.url)
        if url.scheme not in {"http", "https"} or not url.hostname:
            raise ValueError("POI service URL must be HTTP(S)")
        if not self.model_version.strip():
            raise ValueError("POI model version must not be blank")
        if self.limit < 1 or self.timeout_minutes < 1:
            raise ValueError("POI limit and timeout must be positive")


@dataclass(frozen=True)
class ScrapeRequest:
    """Parameters for one scrape run, passed from the workflow to the activity."""

    search_url: str
    max_pages: int = 1
    max_workers: int = 4
    request_delay: float = 3.0
    timeout_minutes: int = 60
    enrichment: EnrichmentRequest | None = None

    def validate(self) -> None:
        """Check the request's values.

        Raises:
            ValueError: If the URL is not HTTP(S) or a numeric limit is out of range.
        """
        if self.enrichment is not None:
            self.enrichment.validate()
        url = urlparse(self.search_url)
        if url.scheme not in {"http", "https"} or not url.netloc:
            raise ValueError("search_url must be an HTTP(S) URL")
        if self.max_pages < 1 or self.max_workers < 1 or self.timeout_minutes < 1:
            raise ValueError("max_pages, max_workers and timeout_minutes must be positive")
        if not isfinite(self.request_delay) or self.request_delay < 0:
            raise ValueError("request_delay must be finite and non-negative")
