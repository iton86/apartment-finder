"""Run a worker or create/update the recurring scrape schedule."""

import argparse
import asyncio
import os
from datetime import timedelta

from dotenv import load_dotenv
from temporalio.client import (
    Client,
    Schedule,
    ScheduleActionStartWorkflow,
    ScheduleAlreadyRunningError,
    ScheduleIntervalSpec,
    ScheduleOverlapPolicy,
    SchedulePolicy,
    ScheduleSpec,
    ScheduleUpdate,
)
from temporalio.worker import Worker

from apartment_finder.infrastructure.temporal.activities import scrape_listings
from apartment_finder.infrastructure.temporal.contracts import TASK_QUEUE, ScrapeRequest
from apartment_finder.infrastructure.temporal.workflows import ScrapeListingsWorkflow
from apartment_finder.interface.logging_config import configure_logging


def build_schedule(request: ScrapeRequest, schedule_id: str, task_queue: str) -> Schedule:
    request.validate()
    return Schedule(
        action=ScheduleActionStartWorkflow(
            ScrapeListingsWorkflow.run,
            request,
            id=f"{schedule_id}-run",
            task_queue=task_queue,
        ),
        spec=ScheduleSpec(intervals=[ScheduleIntervalSpec(every=timedelta(minutes=10))]),
        policy=SchedulePolicy(
            overlap=ScheduleOverlapPolicy.SKIP,
            catchup_window=timedelta(minutes=1),
        ),
    )


async def run(args: argparse.Namespace) -> None:
    request = None
    if args.command == "schedule":
        request = ScrapeRequest(
            args.search_url,
            args.max_pages,
            args.max_workers,
            args.request_delay,
            args.timeout_minutes,
        )
        request.validate()
    api_key = os.environ.get("TEMPORAL_API_KEY")
    client = await Client.connect(
        args.address,
        namespace=args.namespace,
        api_key=api_key,
        tls=bool(api_key) or os.environ.get("TEMPORAL_TLS") == "true",
    )
    if args.command == "worker":
        worker = Worker(
            client,
            task_queue=args.task_queue,
            workflows=[ScrapeListingsWorkflow],
            activities=[scrape_listings],
            max_concurrent_activities=1,
            graceful_shutdown_timeout=timedelta(seconds=10),
        )
        await worker.run()
        return

    schedule = build_schedule(request, args.schedule_id, args.task_queue)
    try:
        await client.create_schedule(args.schedule_id, schedule)
        print(f"Created schedule {args.schedule_id}: every 10 minutes")
    except ScheduleAlreadyRunningError:

        def update(existing):
            # Preserve paused state when updating the URL or scrape settings.
            schedule.state = existing.description.schedule.state
            return ScheduleUpdate(schedule=schedule)

        await client.get_schedule_handle(args.schedule_id).update(update)
        print(f"Updated schedule {args.schedule_id}: every 10 minutes")


def main() -> None:
    load_dotenv()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--address", default=os.getenv("TEMPORAL_ADDRESS", "localhost:7233"))
    parser.add_argument("--namespace", default=os.getenv("TEMPORAL_NAMESPACE", "default"))
    parser.add_argument("--task-queue", default=os.getenv("TEMPORAL_TASK_QUEUE", TASK_QUEUE))
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("worker", help="Run the worker until interrupted")
    schedule = commands.add_parser("schedule", help="Create or update the 10-minute schedule")
    schedule.add_argument("search_url")
    schedule.add_argument("--schedule-id", default="apartment-finder-every-10-minutes")
    schedule.add_argument("--max-pages", type=int, default=1)
    schedule.add_argument("--max-workers", type=int, default=4)
    schedule.add_argument("--request-delay", type=float, default=3.0)
    schedule.add_argument("--timeout-minutes", type=int, default=60)
    args = parser.parse_args()
    configure_logging()
    try:
        asyncio.run(run(args))
    except ValueError as exc:
        parser.error(str(exc))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
