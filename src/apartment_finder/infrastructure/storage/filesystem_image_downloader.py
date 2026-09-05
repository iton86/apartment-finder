"""
Infrastructure: downloads images to local disk using Playwright's request
context (reuses browser session/cookies). Swappable later for an
Azure Blob Storage implementation without touching the use case.
"""

import logging
from pathlib import Path

from playwright.sync_api import sync_playwright

from apartment_finder.application.ports import ImageDownloader
from apartment_finder.domain.entities import DownloadedImage, Listing

logger = logging.getLogger(__name__)

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)


class FilesystemImageDownloader(ImageDownloader):
    def __init__(self, base_dir: str = "./downloaded_images"):
        self.base_dir = Path(base_dir)
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def download(self, listing: Listing) -> list[DownloadedImage]:
        listing_dir = self.base_dir / str(listing.id).replace(":", "_")
        listing_dir.mkdir(parents=True, exist_ok=True)

        downloaded: list[DownloadedImage] = []

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(user_agent=USER_AGENT)

            for i, image_url in enumerate(listing.image_urls):
                try:
                    response = context.request.get(image_url)
                    if response.ok:
                        ext = self._guess_extension(image_url)
                        path = listing_dir / f"{i}{ext}"
                        path.write_bytes(response.body())
                        downloaded.append(
                            DownloadedImage(
                                listing_id=listing.id,
                                sequence=i,
                                storage_path=str(path),
                            )
                        )
                        logger.debug("Wrote %s (%d bytes)", path, len(response.body()))
                    else:
                        logger.warning("Skipping image %s — HTTP %d", image_url, response.status)
                except Exception:
                    logger.warning("Failed to download %s", image_url, exc_info=True)

            browser.close()

        logger.info(
            "Downloaded %d/%d image(s) for %s to %s",
            len(downloaded),
            len(listing.image_urls),
            listing.id,
            listing_dir,
        )

        return downloaded

    @staticmethod
    def _guess_extension(url: str) -> str:
        for ext in (".jpg", ".jpeg", ".png", ".webp"):
            if ext in url.lower():
                return ext
        return ".jpg"
