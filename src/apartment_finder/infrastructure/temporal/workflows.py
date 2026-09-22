from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy

with workflow.unsafe.imports_passed_through():
    from apartment_finder.infrastructure.temporal.contracts import ScrapeRequest


@workflow.defn
class ScrapeListingsWorkflow:
    @workflow.run
    async def run(self, request: ScrapeRequest) -> None:
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
