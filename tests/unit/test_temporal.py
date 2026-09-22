import argparse
import asyncio
import sys
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from temporalio.client import ScheduleAlreadyRunningError, ScheduleOverlapPolicy
from temporalio.exceptions import ApplicationError
from temporalio.testing import ActivityEnvironment

from apartment_finder.infrastructure.temporal import activities
from apartment_finder.infrastructure.temporal.contracts import ScrapeRequest
from apartment_finder.interface import temporal_cli


def test_schedule_runs_every_ten_minutes_without_overlap():
    request = ScrapeRequest("https://example.test/search")
    schedule = temporal_cli.build_schedule(request, "test", "queue")
    assert schedule.spec.intervals[0].every == timedelta(minutes=10)
    assert schedule.policy.overlap == ScheduleOverlapPolicy.SKIP
    assert schedule.action.args == [request]
    assert schedule.action.task_queue == "queue"


@pytest.mark.parametrize(
    "kwargs",
    [
        {"max_pages": 0},
        {"max_workers": 0},
        {"request_delay": -1},
        {"request_delay": float("nan")},
        {"timeout_minutes": 0},
    ],
)
def test_reject_invalid_scrape_options(kwargs):
    with pytest.raises(ValueError):
        ScrapeRequest("https://example.test/search", **kwargs).validate()


def test_schedule_update_preserves_paused_state(monkeypatch):
    async def check():
        handle = SimpleNamespace(update=AsyncMock())
        client = SimpleNamespace(
            create_schedule=AsyncMock(side_effect=ScheduleAlreadyRunningError()),
            get_schedule_handle=lambda _: handle,
        )
        monkeypatch.setattr(temporal_cli.Client, "connect", AsyncMock(return_value=client))
        args = argparse.Namespace(
            command="schedule",
            search_url="https://example.test/search",
            max_pages=2,
            max_workers=4,
            request_delay=3.0,
            timeout_minutes=60,
            address="localhost:7233",
            namespace="default",
            task_queue="queue",
            schedule_id="test",
        )
        await temporal_cli.run(args)
        callback = handle.update.call_args.args[0]
        existing = temporal_cli.build_schedule(ScrapeRequest(args.search_url), "test", "queue")
        existing.state.paused = True
        result = callback(SimpleNamespace(description=SimpleNamespace(schedule=existing)))
        assert result.schedule.state.paused
        assert result.schedule.action.args[0].max_pages == 2

    asyncio.run(check())


@pytest.mark.parametrize("exit_code", [0, 1])
def test_activity_runs_process_and_reports_failure(monkeypatch, exit_code):
    async def check():
        create_process = asyncio.create_subprocess_exec
        captured = []

        async def launch(*args, **kwargs):
            captured.extend(args)
            return await create_process(sys.executable, "-c", f"exit({exit_code})", **kwargs)

        monkeypatch.setattr(activities.asyncio, "create_subprocess_exec", launch)
        environment = ActivityEnvironment()
        request = ScrapeRequest("https://example.test/search?a=1&b=2", max_workers=4)
        if exit_code:
            with pytest.raises(ApplicationError, match="exited with code 1"):
                await environment.run(activities.scrape_listings, request)
        else:
            await environment.run(activities.scrape_listings, request)
        assert request.search_url in captured
        assert captured[captured.index("--max-workers") + 1] == "4"

    asyncio.run(check())


def test_activity_cancellation_stops_scraper(monkeypatch):
    async def check():
        create_process = asyncio.create_subprocess_exec
        launched = asyncio.Event()
        process = None

        async def launch(*args, **kwargs):
            nonlocal process
            process = await create_process(
                sys.executable,
                "-c",
                "import time; time.sleep(60)",
                **kwargs,
            )
            launched.set()
            return process

        monkeypatch.setattr(activities.asyncio, "create_subprocess_exec", launch)
        task = asyncio.create_task(
            ActivityEnvironment().run(
                activities.scrape_listings,
                ScrapeRequest("https://example.test/search"),
            )
        )
        await launched.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert process.returncode is not None

    asyncio.run(check())
