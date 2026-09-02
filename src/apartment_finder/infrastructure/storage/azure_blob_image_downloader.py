"""
Infrastructure: downloads images from the source site and uploads them
straight to Azure Blob Storage, held only in memory in between — nothing
touches local disk.

Authentication uses DefaultAzureCredential, which tries multiple methods
in order and picks whichever works:
  - Managed Identity (when running on an Azure VM, App Service, Container App, etc.)
  - Your `az login` session (when running locally)
This means the exact same code works unchanged on your laptop and in
production — no connection strings or account keys stored anywhere.
"""

from io import BytesIO

from azure.identity import DefaultAzureCredential
from azure.storage.blob import BlobServiceClient, ContentSettings
from playwright.sync_api import sync_playwright

from apartment_finder.application.ports import ImageDownloader
from apartment_finder.domain.entities import DownloadedImage, Listing

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)


class AzureBlobImageDownloader(ImageDownloader):
    def __init__(self, storage_account_name: str, container_name: str):
        account_url = f"https://{storage_account_name}.blob.core.windows.net"
        self._blob_service = BlobServiceClient(
            account_url=account_url,
            credential=DefaultAzureCredential(),
        )
        self._container_name = container_name

    def download(self, listing: Listing) -> list[DownloadedImage]:
        listing_prefix = str(listing.id).replace(":", "_")
        downloaded: list[DownloadedImage] = []

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(user_agent=USER_AGENT)

            for i, image_url in enumerate(listing.image_urls):
                try:
                    response = context.request.get(image_url)
                    if not response.ok:
                        continue

                    ext = self._guess_extension(image_url)
                    blob_name = f"{listing_prefix}/{i}{ext}"
                    content_type = self._guess_content_type(ext)

                    blob_client = self._blob_service.get_blob_client(
                        container=self._container_name, blob=blob_name
                    )
                    # Upload directly from the in-memory response body —
                    # no intermediate local file is ever created.
                    blob_client.upload_blob(
                        BytesIO(response.body()),
                        overwrite=True,
                        content_settings=ContentSettings(content_type=content_type),
                    )

                    downloaded.append(
                        DownloadedImage(
                            listing_id=listing.id,
                            sequence=i,
                            storage_path=f"{self._container_name}/{blob_name}",
                        )
                    )
                except Exception as e:
                    print(f"  Warning: failed to upload image {image_url}: {e}")

            browser.close()

        return downloaded

    @staticmethod
    def _guess_extension(url: str) -> str:
        for ext in (".jpg", ".jpeg", ".png", ".webp"):
            if ext in url.lower():
                return ext
        return ".jpg"

    @staticmethod
    def _guess_content_type(ext: str) -> str:
        return {
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".png": "image/png",
            ".webp": "image/webp",
        }.get(ext, "application/octet-stream")
