"""Playwright adapter for imot.bg discovery, extraction, and field parsing."""

import logging
import os
import queue
import re
import threading
import time
from collections.abc import Iterator, Sequence
from urllib.parse import urljoin, urlsplit

from playwright.sync_api import BrowserContext, Page, sync_playwright

from apartment_finder.application.ports import ListingUnavailableError, ScrapeOutcome, SiteScraper
from apartment_finder.domain.entities import (
    Currency,
    Listing,
    ListingId,
    Money,
    SearchListingsResult,
    TransactionType,
)

logger = logging.getLogger(__name__)

BASE_URL = "https://www.imot.bg"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)

# Look up optional property attributes by label; row order varies by listing.
AREA_LABEL = "Площ"
FLOOR_LABEL = "Етаж"

# Prefer original carousel slides to preserve photo order; cloned slides repeat photos.
IMAGE_SELECTOR = "img.carouselimg"
UNCLONED_IMAGE_SELECTOR = ".owl-item:not(.cloned) img.carouselimg"

# Strip city/village prefixes; retain street prefixes such as "ул." and "бул.".
CITY_PREFIX_RE = re.compile(r"^(?:град|гр\.|село|с\.)\s+")

# Container opt-in: disable the sandbox and avoid Docker's limited /dev/shm.
# Keep Chromium's default protections outside containers.
CONTAINER_CHROMIUM_ARGS = ["--no-sandbox", "--disable-dev-shm-usage"]

# Limit concurrent browser instances to bound memory usage.
DEFAULT_MAX_WORKERS = 4

# Bound shutdown waits for daemon workers with in-flight browser requests.
WORKER_JOIN_TIMEOUT_SECONDS = 30.0


class RateLimiter:
    """Reserve request slots at a shared minimum interval across worker threads."""

    def __init__(self, min_interval_seconds: float):
        self._min_interval = min_interval_seconds
        self._lock = threading.Lock()
        self._next_allowed = 0.0

    def acquire(self) -> None:
        with self._lock:
            now = time.monotonic()
            wait_for = self._next_allowed - now
            # Reserve request slots under the lock to maintain the shared interval.
            self._next_allowed = max(now, self._next_allowed) + self._min_interval

        # Release the lock before waiting so other workers can reserve slots.
        if wait_for > 0:
            time.sleep(wait_for)


class ImotBgScraper(SiteScraper):
    """Discover and scrape imot.bg listings with a shared request limiter."""

    source = "imot.bg"

    def __init__(
        self,
        request_delay_seconds: float = 3.0,
        max_workers: int = DEFAULT_MAX_WORKERS,
    ):
        self.request_delay_seconds = request_delay_seconds
        self.max_workers = max_workers
        # Share the request interval across discovery and listing workers.
        self._rate_limiter = RateLimiter(request_delay_seconds)

    def discover_listing_urls(self, search_url: str, max_pages: int) -> list[SearchListingsResult]:
        results: list[SearchListingsResult] = []

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True, args=self._chromium_args())
            context = browser.new_context(user_agent=USER_AGENT, locale="bg-BG")
            page = context.new_page()
            url_main_part, url_search_params = search_url.split("?")
            page.goto(search_url, wait_until="domcontentloaded")
            page.wait_for_timeout(1500)
            last_page = int(self._safe_text(page, "div.a.saveSlink.gray"))
            max_pages = min(max_pages, last_page)

            for page_num in range(1, max_pages + 1):
                paged_url = (
                    f"{url_main_part}/p-{page_num}?{url_search_params}"
                    if page_num > 1
                    else search_url
                )
                logger.debug("Fetching results page %d/%d: %s", page_num, max_pages, paged_url)

                self._rate_limiter.acquire()
                page.goto(paged_url, wait_until="domcontentloaded")
                page.wait_for_timeout(1500)

                # Scope fields to each card; urljoin resolves protocol-relative links.
                found_on_page = 0
                for item in page.locator("div.item").all():
                    link = item.locator("a.title.saveSlink").first
                    if not link.count():
                        continue

                    href = link.get_attribute("href")
                    if href:
                        url = urljoin(BASE_URL, href)
                        title = " ".join(link.inner_text().split())
                        price = item.locator("div.price").first
                        price_raw = " ".join(price.inner_text().split()) if price.count() else ""
                        results.append(
                            SearchListingsResult(
                                id=ListingId(
                                    source="imot.bg",
                                    external_id=self._extract_external_id(url),
                                ),
                                title=title,
                                url=url,
                                price=self._parse_price(price_raw),
                                transaction_type=self._parse_transaction_type(title),
                            )
                        )
                        found_on_page += 1

                # Empty results can indicate stale selectors or a blocked page.
                if found_on_page == 0:
                    logger.warning(
                        "No listing cards matched 'div.item' with 'a.title.saveSlink' on %s — "
                        "selector may be stale, or the page was blocked",
                        paged_url,
                    )
                else:
                    logger.debug("Results page %d yielded %d link(s)", page_num, found_on_page)

            browser.close()

        deduped = list({result.id: result for result in results}.values())
        if len(deduped) != len(results):
            logger.debug(
                "Deduplicated %d listing(s) repeated across result pages",
                len(results) - len(deduped),
            )
        return deduped

    def scrape_listing(self, url: str) -> Listing:
        """Scrape one listing and raise any reported extraction or navigation error."""
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True, args=self._chromium_args())
            try:
                context = browser.new_context(user_agent=USER_AGENT, locale="bg-BG")
                outcome = self._scrape_one(context, url)
            finally:
                browser.close()

        if outcome.error is not None:
            raise outcome.error
        # _scrape_one returns either a listing or an error.
        assert outcome.listing is not None
        return outcome.listing

    def scrape_listings(self, urls: Sequence[str]) -> Iterator[ScrapeOutcome]:
        """Yield batch outcomes in completion order using a bounded worker pool.

        Each worker reuses one browser context. A shared rate limiter spaces
        requests across workers independently of browser and page-load latency.
        """
        worker_count = min(self.max_workers, len(urls))
        if worker_count <= 1:
            # Use the port's sequential implementation for at most one worker.
            yield from super().scrape_listings(urls)
            return

        pending: queue.Queue[str] = queue.Queue()
        for url in urls:
            pending.put(url)
        results: queue.Queue[ScrapeOutcome] = queue.Queue()

        logger.info(
            "Scraping %d listing(s) across %d worker(s), %.1fs between requests",
            len(urls),
            worker_count,
            self.request_delay_seconds,
        )

        workers = [
            threading.Thread(
                target=self._run_worker,
                args=(pending, results),
                name=f"imot-scraper-{i + 1}",
                daemon=True,
            )
            for i in range(worker_count)
        ]
        for worker in workers:
            worker.start()

        try:
            # Collect one outcome per input URL, in completion order.
            for _ in range(len(urls)):
                yield results.get()
        finally:
            # Discard pending work when the generator exits, including on explicit close.
            while True:
                try:
                    pending.get_nowait()
                except queue.Empty:
                    break
            for worker in workers:
                worker.join(timeout=WORKER_JOIN_TIMEOUT_SECONDS)

    def _run_worker(self, pending: queue.Queue, results: queue.Queue) -> None:
        """Process queued listings with one browser and report pending work on failure."""
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True, args=self._chromium_args())
                try:
                    # Reuse cookies and session state across each worker's listings.
                    context = browser.new_context(user_agent=USER_AGENT, locale="bg-BG")
                    while True:
                        try:
                            url = pending.get_nowait()
                        except queue.Empty:
                            break
                        results.put(self._scrape_one(context, url))
                finally:
                    browser.close()
        except BaseException as exc:
            # Report failures for queued URLs so the consumer can collect their outcomes.
            logger.error("Scraper worker died: %s", exc, exc_info=True)
            while True:
                try:
                    url = pending.get_nowait()
                except queue.Empty:
                    break
                results.put(ScrapeOutcome(url=url, error=exc))

    def _scrape_one(self, context: BrowserContext, url: str) -> ScrapeOutcome:
        """Load and extract a listing, returning navigation and extraction errors as outcomes."""
        self._rate_limiter.acquire()

        page = None
        try:
            page = context.new_page()
            logger.debug("Loading listing page %s", url)
            response = page.goto(url, wait_until="domcontentloaded")
            if response is None:
                raise RuntimeError("Listing navigation returned no response")
            if response.status in (404, 410):
                raise ListingUnavailableError(f"Listing unavailable (HTTP {response.status})")
            if response.status >= 400:
                raise RuntimeError(f"Listing request failed (HTTP {response.status})")
            page.wait_for_timeout(1500)
            self._check_removal_redirect(url, page.url)
            self._check_removal_notice(page)
            return ScrapeOutcome(url=url, listing=self._extract(page, url))
        except Exception as exc:
            logger.warning("Failed to scrape %s", url, exc_info=True)
            return ScrapeOutcome(url=url, error=exc)
        finally:
            # Close each page to prevent accumulation during long batches.
            if page is not None:
                try:
                    page.close()
                except Exception:
                    logger.debug("Could not close page for %s", url, exc_info=True)

    @staticmethod
    def _check_removal_redirect(requested_url: str, final_url: str) -> None:
        requested = urlsplit(requested_url)
        destination = urlsplit(final_url)
        site_hosts = {"imot.bg", "www.imot.bg"}
        if (
            requested.hostname in site_hosts
            and requested.path.startswith("/obiava-")
            and destination.scheme in {"http", "https"}
            and destination.hostname in site_hosts
            and (
                destination.path == "/obiavi/prodazhbi"
                or destination.path.startswith("/obiavi/prodazhbi/")
            )
        ):
            raise ListingUnavailableError("Listing redirected to the sales search page")

    @staticmethod
    def _check_removal_notice(page: Page) -> None:
        # Only standalone notices without listing markup count as removal.
        notices = {
            "обявата е изтрита",
            "обявата е неактивна",
            "обявата е свалена",
            "обявата не съществува",
        }
        if page.locator("div.title").count():
            return
        lines = {
            " ".join(line.lower().split()).rstrip(".! ")
            for line in page.locator("body").inner_text().splitlines()
        }
        if lines & notices:
            raise ListingUnavailableError("Listing page displays a removal notice")

    def _extract(self, page: Page, url: str) -> Listing:
        external_id = self._extract_external_id(url)

        title = self._safe_text(page, "div.title")
        price_raw = self._safe_text(page, "div.cena")
        location_raw = self._safe_text(page, "div.location")
        description = self._safe_text(page, "div.text")
        phone = self._normalize_phone(self._safe_text(page, "div.phone"))
        agency_name = self._safe_text(page, "div.dealer a.who div.info > div.name") or None

        price = self._parse_price(price_raw)
        transaction_type = self._parse_transaction_type(title)
        rooms = self._parse_rooms(title)
        city, area, street = self._parse_location(location_raw)

        # Parse area and floor values independently from the label/value grid.
        params = self._extract_ad_params(page)
        area_sqm = self._parse_number(params.get(AREA_LABEL, ""))
        floor_text = params.get(FLOOR_LABEL) or None
        floor = self._parse_floor(floor_text)
        building_floors = self._parse_building_floors(floor_text)

        image_urls = self._extract_image_urls(page)

        # Warn about missing core fields that may indicate changed markup.
        missing = [
            name
            for name, value in (
                ("title", title),
                ("price", price),
                ("area_sqm", area_sqm),
                # Validate the parsed city; street is optional.
                ("city", city),
                ("images", image_urls),
            )
            if not value
        ]
        if missing:
            logger.warning("Listing %s parsed with empty field(s): %s", url, ", ".join(missing))

        logger.debug(
            "Parsed %s | title=%r price=%s area_sqm=%s floor=%r images=%d "
            "city=%r area=%r street=%r phone=%r description=%d chars",
            external_id,
            title,
            price,
            area_sqm,
            floor,
            len(image_urls),
            city,
            area,
            street,
            phone,
            len(description),
        )

        return Listing(
            id=ListingId(source="imot.bg", external_id=external_id),
            url=url,
            title=title,
            price=price,
            transaction_type=transaction_type,
            area_sqm=area_sqm,
            floor=floor,
            city=city,
            area=area,
            street=street,
            description=description,
            rooms=rooms,
            building_floors=building_floors,
            agency_name=agency_name,
            phone=phone,
            image_urls=image_urls,
        )

    @staticmethod
    def _chromium_args() -> list[str]:
        if os.environ.get("CHROMIUM_IN_CONTAINER") == "1":
            return list(CONTAINER_CHROMIUM_ARGS)
        return []

    def _extract_image_urls(self, page: Page) -> list[str]:
        images = page.locator(UNCLONED_IMAGE_SELECTOR)
        if images.count() == 0:
            logger.debug(
                "No .owl-item wrappers found — carousel not initialised, falling back to %s",
                IMAGE_SELECTOR,
            )
            images = page.locator(IMAGE_SELECTOR)

        # Lazy-loaded slides store the photo URL in data-src before entering view.
        raw_srcs = [
            img.get_attribute("data-src") or img.get_attribute("src") for img in images.all()
        ]
        resolved = self._resolve_image_urls(raw_srcs)
        logger.debug("Gallery: %d slide(s) -> %d unique photo URL(s)", len(raw_srcs), len(resolved))
        return resolved

    @staticmethod
    def _resolve_image_urls(raw_srcs: list[str | None]) -> list[str]:
        urls: list[str] = []
        for src in raw_srcs:
            # Skip inline placeholders for slides that have not loaded.
            if not src or src.startswith("data:"):
                continue
            urls.append(urljoin(BASE_URL, src))
        # Deduplicate resolved URLs while preserving gallery order.
        return list(dict.fromkeys(urls))

    def _extract_ad_params(self, page: Page) -> dict[str, str]:
        """Read the property attribute grid into a label-to-value mapping."""
        try:
            # Select direct rows only, excluding nested value markup.
            rows = page.locator("div.adParams > div").all_inner_texts()
        except Exception:
            # Log extraction failures before falling back to empty attributes.
            logger.warning("Could not read the .adParams block", exc_info=True)
            return {}

        params = self._parse_ad_params(rows)
        logger.debug("adParams: %s", params)
        return params

    @staticmethod
    def _parse_ad_params(rows: list[str]) -> dict[str, str]:
        params: dict[str, str] = {}
        for row in rows:
            # Split on the label delimiter, independent of rendered line breaks.
            label, separator, value = row.partition(":")
            if not separator:
                continue
            label = label.strip()
            # Normalize whitespace across nested text nodes.
            value = " ".join(value.split())
            # Preserve the first value for duplicate labels.
            if label and label not in params:
                params[label] = value
        return params

    @staticmethod
    def _safe_text(page: Page, selector: str) -> str:
        try:
            return page.locator(selector).first.inner_text(timeout=3000).strip()
        except Exception as exc:
            # Optional fields may be absent; _extract warns about missing core fields.
            logger.debug("No text for selector %r (%s)", selector, type(exc).__name__)
            return ""

    @staticmethod
    def _parse_location(raw: str) -> tuple[str, str, str | None]:
        """Parse city, neighbourhood, and optional street from location lines.

        Remove Bulgarian city/village prefixes and retain street prefixes.
        """
        lines = [line.strip() for line in raw.splitlines() if line.strip()]
        if not lines:
            return "", "", None

        # Preserve commas within the neighbourhood name.
        head, _, area = lines[0].partition(",")
        city = CITY_PREFIX_RE.sub("", head.strip()).strip()

        # Only the second line is interpreted as a street.
        street = lines[1] if len(lines) > 1 else None

        return city, area.strip(), street

    @staticmethod
    def _parse_transaction_type(title: str) -> TransactionType:
        normalized = " ".join(title.split()).casefold()
        if "продава" in normalized:
            return TransactionType.SALE
        if "дава под наем" in normalized:
            return TransactionType.RENT
        raise ValueError(f"Unsupported listing transaction type in title: {title!r}")

    @staticmethod
    def _parse_rooms(title: str) -> int | None:
        match = re.search(r"\b(\d+)-СТАЕН\b", title, flags=re.IGNORECASE)
        return int(match.group(1)) if match else None

    @staticmethod
    def _parse_building_floors(floor: str | None) -> int | None:
        if not floor:
            return None
        match = re.search(r"\bот\s+(\d+)\b", floor, flags=re.IGNORECASE)
        return int(match.group(1)) if match else None

    @staticmethod
    def _parse_floor(floor: str | None) -> int | None:
        if not floor:
            return None
        match = re.search(r"\d+", floor)
        return int(match.group()) if match else None

    @staticmethod
    def _normalize_phone(raw: str) -> str | None:
        normalized = re.sub(r"\s+", "", raw)
        return normalized or None

    @staticmethod
    def _extract_external_id(url: str) -> str:
        # Listing IDs are alphanumeric, for example /obiava-1b178411519043054-...
        match = re.search(r"/obiava-([a-z0-9]+)", url)
        return match.group(1) if match else url.rstrip("/").split("/")[-1]

    @staticmethod
    def _parse_price(raw: str) -> Money | None:
        if not raw:
            return None
        currency = Currency.EUR if "€" in raw else Currency.BGN
        digits = re.sub(r"[^\d]", "", raw)
        return Money(amount=float(digits), currency=currency) if digits else None

    @staticmethod
    def _parse_number(raw: str) -> float | None:
        if not raw:
            return None
        # Match the numeric value without punctuation from units such as "кв.м".
        # Accept both Bulgarian decimal commas and decimal points.
        match = re.search(r"\d+(?:[.,]\d+)?", raw)
        if not match:
            return None
        try:
            return float(match.group(0).replace(",", "."))
        except ValueError:
            return None
