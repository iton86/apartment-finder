"""
Infrastructure layer — this is the ONLY file that should mention
Playwright, CSS selectors, or imot.bg's URL structure by name.

If imot.bg redesigns their site, or you switch from Playwright to a
different tool, only this file (and maybe its sibling adapters) needs
to change. The use case and domain layers stay untouched.

NOTE: selectors marked # VERIFY are placeholders — imot.bg's bot detection
blocked automated inspection. Confirm by opening a real listing in your
browser, right-click -> Inspect on each field, and update accordingly.
"""

import logging
import os
import queue
import re
import threading
import time
from collections.abc import Iterator, Sequence
from urllib.parse import urljoin

from playwright.sync_api import BrowserContext, Page, sync_playwright

from apartment_finder.application.ports import ScrapeOutcome, SiteScraper
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

# Labels inside the .adParams block. Which rows an ad has varies — a plot of
# land has no "Етаж", a new build no completion year — so these are looked up
# by label rather than by position.
AREA_LABEL = "Площ"
FLOOR_LABEL = "Етаж"

# Gallery photos. Owl Carousel clones the trailing slides to the head of the
# strip so looping looks seamless, which puts the *last* photo first in
# document order — read the uncloned slides when the wrappers are there, and
# fall back to every match on pages where owl never initialised.
IMAGE_SELECTOR = "img.carouselimg"
UNCLONED_IMAGE_SELECTOR = ".owl-item:not(.cloned) img.carouselimg"

# div.location reads as one or two lines:
#     град София, Манастирски ливади
#     ул. Луи Айер                    <- only on some ads
# "град"/"гр." = city, "село"/"с." = village; stripped because the field is
# already called city. The street keeps its "ул."/"бул." prefix, since that
# distinguishes a street from a boulevard of the same name.
CITY_PREFIX_RE = re.compile(r"^(?:град|гр\.|село|с\.)\s+")

# Two container-only Chromium problems, both of which surface as a browser
# that dies on launch or mid-page:
#   --no-sandbox: the sandbox needs kernel calls Docker's default seccomp
#     profile blocks, so Chromium refuses to start.
#   --disable-dev-shm-usage: Docker caps /dev/shm at 64 MB, well under what
#     Chromium assumes, and it crashes on image-heavy pages instead.
# Neither is wanted outside a container (--no-sandbox drops a real security
# boundary), so the Dockerfile opts in by setting CHROMIUM_IN_CONTAINER=1.
CONTAINER_CHROMIUM_ARGS = ["--no-sandbox", "--disable-dev-shm-usage"]

# One Chromium per worker costs roughly 250-350 MB of RSS, so this is a
# memory ceiling as much as a politeness one. Four is comfortable on a
# laptop and inside the 1.39 GB container; past that the request rate is
# capped by RateLimiter anyway, so extra workers buy nothing but memory.
DEFAULT_MAX_WORKERS = 4

# Workers are daemon threads blocked in Playwright, not in Python, so a
# join() can't be interrupted mid-request. This bounds how long shutdown
# waits for one in-flight page load before giving up on it.
WORKER_JOIN_TIMEOUT_SECONDS = 30.0


class RateLimiter:
    """Spaces out requests across every worker thread.

    This is the piece that makes parallelism safe to turn on. The obvious
    implementation — each worker sleeping request_delay_seconds on its own —
    would multiply the request rate imot.bg sees by the worker count, which
    is exactly how a scraper gets itself blocked. One shared limiter means
    the configured delay stays the *aggregate* interval no matter how many
    workers run.
    """

    def __init__(self, min_interval_seconds: float):
        self._min_interval = min_interval_seconds
        self._lock = threading.Lock()
        self._next_allowed = 0.0

    def acquire(self) -> None:
        with self._lock:
            now = time.monotonic()
            wait_for = self._next_allowed - now
            # Book the next slot relative to the later of now and the
            # current reservation, so a thread that stalls after acquiring
            # doesn't let the whole queue bunch up behind it.
            self._next_allowed = max(now, self._next_allowed) + self._min_interval

        # Sleep outside the lock. Holding it while sleeping would serialise
        # every worker on the mutex and undo the concurrency entirely.
        if wait_for > 0:
            time.sleep(wait_for)


class ImotBgScraper(SiteScraper):
    def __init__(
        self,
        request_delay_seconds: float = 3.0,
        max_workers: int = DEFAULT_MAX_WORKERS,
    ):
        self.request_delay_seconds = request_delay_seconds
        self.max_workers = max_workers
        # Shared by discovery and listing requests alike, so the delay
        # describes the total load this scraper puts on the site.
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

                # Every listing is one .item card. The element can carry
                # additional classes such as TOP or VIP; the .item selector
                # still matches all of them. Scoping title and price lookups
                # to the card keeps values from adjacent listings together.
                # hrefs are protocol-relative ("//www.imot.bg/obiava-..."),
                # which urljoin resolves against BASE_URL's scheme.
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

                # Zero on page 1 is the signature of a broken selector or a
                # bot-detection page, and it's silent otherwise — the run
                # just reports "0 listings found" and exits successfully.
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
        """Single listing, start to finish. Still raises on failure — the
        batch path is where errors turn into data."""
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True, args=self._chromium_args())
            try:
                context = browser.new_context(user_agent=USER_AGENT, locale="bg-BG")
                outcome = self._scrape_one(context, url)
            finally:
                browser.close()

        if outcome.error is not None:
            raise outcome.error
        # Narrowing for the type checker: _scrape_one always sets exactly
        # one of listing/error.
        assert outcome.listing is not None
        return outcome.listing

    def scrape_listings(self, urls: Sequence[str]) -> Iterator[ScrapeOutcome]:
        """Scrape a batch across several browsers at once.

        Two separate costs are being removed here. The obvious one is
        latency: page load and render used to be serialised behind the
        delay, so each listing cost delay + latency. The bigger one is that
        the old code launched a whole Playwright instance and Chromium per
        listing — around 1.5 s of pure startup, 500 times over. Each worker
        now launches once and reuses one context for every page it handles.

        Throughput stays bounded by RateLimiter, so this is faster without
        being ruder: with the default 3 s delay the aggregate request rate
        is the same 1-per-3-s it was, the run just stops idling between
        requests. Lowering request_delay_seconds is what actually increases
        load on imot.bg, and that's a deliberate decision, not a side
        effect of adding workers.

        Yields in completion order, not input order.
        """
        worker_count = min(self.max_workers, len(urls))
        if worker_count <= 1:
            # One worker is just the sequential path, and the port's default
            # implementation already is that — no threads, no queues.
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
            # Exactly one outcome per URL is guaranteed: every URL is taken
            # from `pending` exactly once, and both _scrape_one and the
            # worker's failure path always put something back. That's what
            # makes this fixed-count loop safe rather than a potential hang.
            for _ in range(len(urls)):
                yield results.get()
        finally:
            # Reached on early generator close too (a `break` in the
            # consumer), where draining `pending` is what lets the workers
            # notice there's no work left and exit instead of leaking.
            while True:
                try:
                    pending.get_nowait()
                except queue.Empty:
                    break
            for worker in workers:
                worker.join(timeout=WORKER_JOIN_TIMEOUT_SECONDS)

    def _run_worker(self, pending: queue.Queue, results: queue.Queue) -> None:
        """One browser, many listings, until the queue runs dry."""
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True, args=self._chromium_args())
                try:
                    # One context per worker rather than per page: a context
                    # is a browser profile, so reusing it keeps cookies and
                    # session state coherent, which is also what a real
                    # browsing session looks like to bot detection.
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
            # Launching the browser failed (out of memory, missing
            # dependency, killed). Without this, the URLs still queued would
            # never produce an outcome and the consumer's fixed-count loop
            # would block forever, so this thread reports the remainder as
            # failures on its way out.
            logger.error("Scraper worker died: %s", exc, exc_info=True)
            while True:
                try:
                    url = pending.get_nowait()
                except queue.Empty:
                    break
                results.put(ScrapeOutcome(url=url, error=exc))

    def _scrape_one(self, context: BrowserContext, url: str) -> ScrapeOutcome:
        """Load one listing in its own page. Never raises — the contract the
        worker loop depends on to keep outcome counts matching URL counts."""
        self._rate_limiter.acquire()

        page = None
        try:
            page = context.new_page()
            logger.debug("Loading listing page %s", url)
            page.goto(url, wait_until="domcontentloaded")
            page.wait_for_timeout(1500)
            return ScrapeOutcome(url=url, listing=self._extract(page, url))
        except Exception as exc:
            logger.warning("Failed to scrape %s", url, exc_info=True)
            return ScrapeOutcome(url=url, error=exc)
        finally:
            # Pages are per-listing and must be closed or a long run
            # accumulates hundreds of them in one browser.
            if page is not None:
                try:
                    page.close()
                except Exception:
                    logger.debug("Could not close page for %s", url, exc_info=True)

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

        # "Площ:<br><strong>55 m<sup>2</sup></strong>" reads as "Площ:\n55 m2",
        # so _parse_number's leading-number match gives 55.0 and ignores the
        # unit. The first integer in the floor text is the listing's floor;
        # "от N" separately gives the total number of building floors.
        params = self._extract_ad_params(page)
        area_sqm = self._parse_number(params.get(AREA_LABEL, ""))
        floor_text = params.get(FLOOR_LABEL) or None
        floor = self._parse_floor(floor_text)
        building_floors = self._parse_building_floors(floor_text)

        image_urls = self._extract_image_urls(page)

        # A stale selector doesn't raise — _safe_text swallows the timeout and
        # returns "", so the listing saves with a blank field and nobody
        # notices. Naming the empty fields at WARNING is the cheapest early
        # signal that imot.bg changed their markup again.
        missing = [
            name
            for name, value in (
                ("title", title),
                ("price", price),
                ("area_sqm", area_sqm),
                # city rather than the raw text: an unparseable location
                # yields an empty city, which is the failure worth hearing
                # about. street is absent on plenty of real ads, so it is
                # deliberately not listed here.
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

        # .owl-lazy means the URL lives in data-src and src is empty or a
        # placeholder until that slide scrolls into view. Headless we never
        # scroll, so only the first slides would ever get a real src — reading
        # data-src first is what makes photos past the third one appear.
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
            # A slide owl hasn't touched yet can carry an inline base64 blank
            # as its src; that's a placeholder, not a photo.
            if not src or src.startswith("data:"):
                continue
            urls.append(urljoin(BASE_URL, src))
        # Dedup last: the thumbnail strip repeats the main carousel's photos,
        # and dict.fromkeys keeps the first-seen order so DownloadedImage.
        # sequence still matches the order the ad shows them in.
        return list(dict.fromkeys(urls))

    def _extract_ad_params(self, page: Page) -> dict[str, str]:
        """Read the .adParams label/value grid into a {label: value} dict."""
        try:
            # Direct children only — a row's own markup nests <strong>/<sup>,
            # and a descendant match would also return inner wrappers as rows.
            rows = page.locator("div.adParams > div").all_inner_texts()
        except Exception:
            # Returning {} here means area and floor silently come back
            # empty, so the reason has to land somewhere.
            logger.warning("Could not read the .adParams block", exc_info=True)
            return {}

        params = self._parse_ad_params(rows)
        logger.debug("adParams: %s", params)
        return params

    @staticmethod
    def _parse_ad_params(rows: list[str]) -> dict[str, str]:
        params: dict[str, str] = {}
        for row in rows:
            # Split on the label's colon rather than on the newline the <br>
            # renders as: the colon is in the markup, the newline is a
            # rendering artifact that disappears if imot.bg drops the <br>.
            label, separator, value = row.partition(":")
            if not separator:
                continue
            label = label.strip()
            # Rows can hold several text nodes ("Тухла, " + "Въведен в
            # експлоатация " + "2013 г."), so collapse the whitespace between
            # them into single spaces.
            value = " ".join(value.split())
            # First occurrence wins, so a repeated label can't overwrite the
            # value already read from the main block.
            if label and label not in params:
                params[label] = value
        return params

    @staticmethod
    def _safe_text(page: Page, selector: str) -> str:
        try:
            return page.locator(selector).first.inner_text(timeout=3000).strip()
        except Exception as exc:
            # DEBUG rather than WARNING: some fields are legitimately absent
            # (plenty of ads hide the phone), so this fires on healthy pages
            # too. _extract aggregates the ones that matter into one warning.
            # %s not exc_info: a Playwright timeout's traceback is noise, the
            # selector that missed is the whole story.
            logger.debug("No text for selector %r (%s)", selector, type(exc).__name__)
            return ""

    @staticmethod
    def _parse_location(raw: str) -> tuple[str, str, str | None]:
        """'град София, Манастирски ливади\\nул. Луи Айер'
        -> ('София', 'Манастирски ливади', 'ул. Луи Айер')

        The source uses Bulgarian city/village prefixes, which are removed
        because the destination field is already named ``city``.
        """
        lines = [line.strip() for line in raw.splitlines() if line.strip()]
        if not lines:
            return "", "", None

        # partition on the first comma, not split: a neighbourhood
        # containing a comma keeps it instead of being truncated.
        head, _, area = lines[0].partition(",")
        city = CITY_PREFIX_RE.sub("", head.strip()).strip()

        # Anything past the second line is ignored rather than concatenated
        # — no observed ad has one, and guessing at a third field's meaning
        # would be worse than dropping it.
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
        # Listing URLs look like /obiava-1b178411519043054-prodava-dvustaen-...
        # The id is alphanumeric, not purely numeric: matching only digits
        # silently drops the leading "1b" and stores a truncated id, which
        # matters because this is the dedup key.
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
        # Match the number rather than stripping unwanted characters: the unit
        # itself contains a dot ("75,5 кв.м"), so stripping non-[\d.] leaves
        # "75.5." and float() rejects it. Bulgarian pages use "," as the
        # decimal separator.
        match = re.search(r"\d+(?:[.,]\d+)?", raw)
        if not match:
            return None
        try:
            return float(match.group(0).replace(",", "."))
        except ValueError:
            return None
