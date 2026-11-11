"""Bounded HTTPS downloads retained as content-addressed local snapshots."""

import hashlib
import json
import os
import tempfile
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

ALLOWED_HOSTS = {"ge.sofiaplan.bg", "gis.sofiaplan.bg", "urbandata.sofia.bg"}
MAX_BYTES = 60_000_000


def check_url(url: str) -> None:
    """Accept only public HTTPS source hosts and standard ports."""
    parsed = urlsplit(url)
    if (
        parsed.scheme != "https"
        or parsed.hostname not in ALLOWED_HOSTS
        or parsed.port not in (None, 443)
        or parsed.username
        or parsed.password
    ):
        raise ValueError("Unapproved geospatial source URL")


class SafeRedirect(HTTPRedirectHandler):
    """Apply the source allowlist to every redirect."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        """Reject redirects outside the configured public providers."""
        check_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class SnapshotDownloader:
    """Download with bounded retries and atomically retain original bytes."""

    def __init__(self, directory: Path):
        self.directory = directory.resolve()
        self.directory.mkdir(parents=True, exist_ok=True)
        self.records: dict[str, dict] = {}
        self.opener = build_opener(SafeRedirect())

    def read(self, url: str) -> bytes:
        """Read one bounded resource, retaining its checksum and local URI."""
        check_url(url)
        for attempt in range(3):
            try:
                with self.opener.open(
                    Request(url, headers={"User-Agent": "apartment-finder/geo"}), timeout=60
                ) as response:
                    raw = response.read(MAX_BYTES + 1)
                break
            except HTTPError as error:
                if error.code not in {429, 500, 502, 503, 504} or attempt == 2:
                    raise
                delay = error.headers.get("Retry-After", "")
                time.sleep(min(int(delay), 30) if delay.isdigit() else 2**attempt)
            except (URLError, TimeoutError):
                if attempt == 2:
                    raise
                time.sleep(2**attempt)
        if len(raw) > MAX_BYTES:
            raise ValueError("Source response exceeds download size limit")
        digest = hashlib.sha256(raw).hexdigest()
        path = self.directory / digest
        # Write fully before linking: another process can never see partial bytes.
        with tempfile.NamedTemporaryFile(dir=self.directory, delete=False) as output:
            temporary = Path(output.name)
            try:
                output.write(raw)
                output.flush()
                os.fsync(output.fileno())
                try:
                    os.link(temporary, path)
                except FileExistsError:
                    if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
                        raise ValueError("Stored snapshot checksum mismatch") from None
            finally:
                temporary.unlink(missing_ok=True)
        self.records[url] = {"sha256": digest, "uri": path.as_uri(), "bytes": len(raw)}
        return raw

    def json(self, url: str) -> dict:
        """Decode a JSON object and reject ArcGIS HTTP-200 errors."""
        data = json.loads(self.read(url))
        if not isinstance(data, dict) or "error" in data:
            raise ValueError("Invalid or unsuccessful geospatial JSON response")
        return data


class SnapshotReplayDownloader:
    """Replay a retained manifest without contacting the source providers."""

    def __init__(self, manifest: dict):
        self.records = manifest["snapshots"]

    def read(self, url: str) -> bytes:
        """Read original bytes and verify their recorded checksum."""
        from urllib.parse import unquote

        record = self.records[url]
        parsed = urlsplit(record["uri"])
        if parsed.scheme != "file" or parsed.netloc:
            raise ValueError("Replay requires local file snapshots")
        path = Path(unquote(parsed.path))
        if path.stat().st_size > MAX_BYTES:
            raise ValueError("Replay snapshot exceeds size limit")
        raw = path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != record["sha256"]:
            raise ValueError("Replay snapshot checksum mismatch")
        return raw

    def json(self, url: str) -> dict:
        """Decode original JSON, rejecting source errors."""
        data = json.loads(self.read(url))
        if not isinstance(data, dict) or "error" in data:
            raise ValueError("Invalid or unsuccessful geospatial JSON response")
        return data
