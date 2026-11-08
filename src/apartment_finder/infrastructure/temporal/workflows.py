"""Temporal workflow definitions."""

from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy

with workflow.unsafe.imports_passed_through():
    from apartment_finder.infrastructure.temporal.contracts import ScrapeRequest


@workflow.defn
class ScrapeListingsWorkflow:
    """Scrape listings, then optionally enrich pending descriptions with POIs."""

    @workflow.run
    async def run(self, request: ScrapeRequest) -> None:
        """Execute scraping followed by configured POI enrichment."""
        await workflow.execute_activity(
            "scrape_listings",
            request,
            start_to_close_timeout=timedelta(minutes=request.timeout_minutes + 1),
            heartbeat_timeout=timedelta(minutes=1),
            # A failed run is attempted again at the next scheduled interval.
            # Avoid replaying a partially committed scrape immediately.
            retry_policy=RetryPolicy(maximum_attempts=1),
            cancellation_type=workflow.ActivityCancellationType.WAIT_CANCELLATION_COMPLETED,
        )
        # Keep workflow histories from older workers replay-compatible.
        if workflow.patched("post-scrape-poi-enrichment") and request.enrichment is not None:
            await workflow.execute_activity(
                "enrich_listing_pois",
                request.enrichment,
                start_to_close_timeout=timedelta(minutes=request.enrichment.timeout_minutes + 1),
                heartbeat_timeout=timedelta(minutes=1),
                retry_policy=RetryPolicy(maximum_attempts=3),
                cancellation_type=workflow.ActivityCancellationType.WAIT_CANCELLATION_COMPLETED,
            )
