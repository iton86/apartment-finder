"""Serializable inputs shared by workflows and activities."""

from dataclasses import dataclass
from math import isfinite
from urllib.parse import urlparse

TASK_QUEUE = "apartment-finder"


@dataclass(frozen=True)
class ScrapeRequest:
    search_url: str
    max_pages: int = 1
    max_workers: int = 4
    request_delay: float = 3.0
    timeout_minutes: int = 60

    def validate(self) -> None:
        url = urlparse(self.search_url)
        if url.scheme not in {"http", "https"} or not url.netloc:
            raise ValueError("search_url must be an HTTP(S) URL")
        if self.max_pages < 1 or self.max_workers < 1 or self.timeout_minutes < 1:
            raise ValueError("max_pages, max_workers and timeout_minutes must be positive")
        if not isfinite(self.request_delay) or self.request_delay < 0:
            raise ValueError("request_delay must be finite and non-negative")
