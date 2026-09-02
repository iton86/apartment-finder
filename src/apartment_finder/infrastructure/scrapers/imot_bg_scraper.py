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

import re
import time
from urllib.parse import urljoin

from playwright.sync_api import Page, sync_playwright

from apartment_finder.application.ports import SiteScraper
from apartment_finder.domain.entities import Currency, Listing, ListingId, Money

BASE_URL = "https://www.imot.bg"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)


class ImotBgScraper(SiteScraper):
    def __init__(self, request_delay_seconds: float = 3.0):
        self.request_delay_seconds = request_delay_seconds

    def discover_listing_urls(self, search_url: str, max_pages: int) -> list[str]:
        urls: list[str] = []

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(user_agent=USER_AGENT, locale="bg-BG")
            page = context.new_page()

            for page_num in range(1, max_pages + 1):
                paged_url = f"{search_url}&f1={page_num}" if page_num > 1 else search_url
                page.goto(paged_url, wait_until="domcontentloaded")
                page.wait_for_timeout(1500)

                links = page.locator("a.js-item-link").element_handles()  # VERIFY
                for link in links:
                    href = link.get_attribute("href")
                    if href:
                        urls.append(urljoin(BASE_URL, href))

                time.sleep(self.request_delay_seconds)

            browser.close()

        return list(dict.fromkeys(urls))

    def scrape_listing(self, url: str) -> Listing:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(user_agent=USER_AGENT, locale="bg-BG")
            page = context.new_page()
            page.goto(url, wait_until="domcontentloaded")
            page.wait_for_timeout(1500)

            listing = self._extract(page, url)

            browser.close()
            time.sleep(self.request_delay_seconds)

        return listing

    def _extract(self, page: Page, url: str) -> Listing:
        external_id = self._extract_external_id(url)

        title = self._safe_text(page, "h1.adv-title")  # VERIFY
        price_raw = self._safe_text(page, "div.price-tag")  # VERIFY
        area_raw = self._safe_text(page, "span.area-value")  # VERIFY
        floor = self._safe_text(page, "span.floor-value")  # VERIFY
        location = self._safe_text(page, "div.location-breadcrumb")  # VERIFY
        description = self._safe_text(page, "div.description-text")  # VERIFY
        phone = self._safe_text(page, "a.phone-number")  # VERIFY

        price = self._parse_price(price_raw)
        area_sqm = self._parse_number(area_raw)

        image_urls = []
        for img in page.locator("div.gallery img").element_handles():  # VERIFY
            src = img.get_attribute("data-src") or img.get_attribute("src")
            if src:
                image_urls.append(urljoin(BASE_URL, src))

        return Listing(
            id=ListingId(source="imot.bg", external_id=external_id),
            url=url,
            title=title,
            price=price,
            area_sqm=area_sqm,
            floor=floor,
            location=location,
            description=description,
            phone=phone,
            image_urls=image_urls,
        )

    @staticmethod
    def _safe_text(page: Page, selector: str) -> str:
        try:
            return page.locator(selector).first.inner_text(timeout=3000).strip()
        except Exception:
            return ""

    @staticmethod
    def _extract_external_id(url: str) -> str:
        match = re.search(r"(\d{6,})", url)  # VERIFY against real URL pattern
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
        digits = re.sub(r"[^\d.]", "", raw.replace(",", "."))
        try:
            return float(digits)
        except ValueError:
            return None
