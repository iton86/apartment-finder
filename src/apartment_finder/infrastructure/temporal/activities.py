"""Run the existing scraper in a cancellable process, outside the workflow sandbox."""

import asyncio
import os
import signal
import sys
from contextlib import suppress

from temporalio import activity
from temporalio.exceptions import ApplicationError

from apartment_finder.infrastructure.temporal.contracts import EnrichmentRequest, ScrapeRequest


async def stop_process(process: asyncio.subprocess.Process) -> None:
    """Stop the CLI and its Chromium children (Linux/WSL/Docker)."""
    with suppress(ProcessLookupError):
        os.killpg(process.pid, signal.SIGTERM)
    try:
        await asyncio.wait_for(process.wait(), timeout=5)
    except TimeoutError:
        pass
    finally:
        with suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGKILL)
        await process.wait()


@activity.defn
async def scrape_listings(request: ScrapeRequest) -> None:
    """Run the scraper CLI as a subprocess, heartbeating until it exits.

    Raises:
        ApplicationError: Non-retryable if the request is invalid; otherwise if
            the scraper exits nonzero. A timeout cancels the run.
    """
    try:
        request.validate()
    except ValueError as exc:
        raise ApplicationError(str(exc), non_retryable=True) from exc

    # Inherit the worker's environment and stream logs directly to its terminal.
    # No shell interpolation; the URL remains one argument.
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "apartment_finder.interface.cli",
        request.search_url,
        "--max-pages",
        str(request.max_pages),
        "--max-workers",
        str(request.max_workers),
        "--request-delay",
        str(request.request_delay),
        start_new_session=True,
    )
    await wait_for_process(process, request.timeout_minutes, "Scraper")


async def wait_for_process(process, timeout_minutes: int, label: str) -> None:
    """Heartbeat until completion, stopping the process group on cancellation."""
    try:
        async with asyncio.timeout(timeout_minutes * 60):
            while True:
                activity.heartbeat()
                try:
                    exit_code = await asyncio.wait_for(process.wait(), timeout=15)
                    break
                except TimeoutError:
                    continue
        if exit_code:
            raise ApplicationError(f"{label} exited with code {exit_code}; see worker logs")
    finally:
        await stop_process(process)


@activity.defn
async def enrich_listing_pois(request: EnrichmentRequest) -> None:
    """Populate pending listing POIs through the existing local enrichment CLI."""
    try:
        request.validate()
        if os.environ.get("APP_ENV", "local") != "local":
            raise ValueError("POI enrichment currently supports APP_ENV=local only")
    except ValueError as exc:
        raise ApplicationError(str(exc), non_retryable=True) from exc
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "apartment_finder.interface.enrich_cli",
        "--url",
        request.url,
        "--model-version",
        request.model_version,
        "--limit",
        str(request.limit),
        start_new_session=True,
    )
    await wait_for_process(process, request.timeout_minutes, "POI enrichment")
