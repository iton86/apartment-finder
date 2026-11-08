import argparse
import asyncio
import sys
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

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
    monkeypatch.setenv("APP_ENV", "local")

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
            poi_model_version="fingerprint",
            poi_service_url="http://127.0.0.1:5001",
            poi_limit=42,
            poi_timeout_minutes=10,
        )
        await temporal_cli.run(args)
        callback = handle.update.call_args.args[0]
        existing = temporal_cli.build_schedule(ScrapeRequest(args.search_url), "test", "queue")
        existing.state.paused = True
        result = callback(SimpleNamespace(description=SimpleNamespace(schedule=existing)))
        assert result.schedule.state.paused
        assert result.schedule.action.args[0].max_pages == 2
        assert result.schedule.action.args[0].enrichment.model_version == "fingerprint"
        assert result.schedule.action.args[0].enrichment.limit == 42

    asyncio.run(check())


def test_worker_connects_and_runs(monkeypatch):
    async def check():
        client = object()
        worker = SimpleNamespace(run=AsyncMock())
        worker_factory = Mock(return_value=worker)
        monkeypatch.setattr(temporal_cli.Client, "connect", AsyncMock(return_value=client))
        monkeypatch.setattr(temporal_cli, "Worker", worker_factory)
        args = argparse.Namespace(
            command="worker",
            address="localhost:7233",
            namespace="default",
            task_queue="queue",
        )

        await temporal_cli.run(args)

        temporal_cli.Client.connect.assert_awaited_once_with(
            "localhost:7233",
            namespace="default",
            api_key=None,
            tls=False,
        )
        worker_factory.assert_called_once()
        assert worker_factory.call_args.kwargs["task_queue"] == "queue"
        assert worker_factory.call_args.kwargs["activities"] == [
            activities.scrape_listings,
            activities.enrich_listing_pois,
        ]
        worker.run.assert_awaited_once()

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


@pytest.mark.parametrize("enabled", [False, True])
def test_workflow_enriches_after_scrape_only_when_configured(monkeypatch, enabled):
    from apartment_finder.infrastructure.temporal import workflows
    from apartment_finder.infrastructure.temporal.contracts import EnrichmentRequest

    execute = AsyncMock()
    monkeypatch.setattr(workflows.workflow, "execute_activity", execute)
    monkeypatch.setattr(workflows.workflow, "patched", lambda _: True)
    enrichment = EnrichmentRequest("fingerprint") if enabled else None
    request = ScrapeRequest("https://example.test/search", enrichment=enrichment)
    asyncio.run(workflows.ScrapeListingsWorkflow().run(request))
    assert execute.await_args_list[0].args == ("scrape_listings", request)
    assert execute.await_count == (2 if enabled else 1)
    if enabled:
        assert execute.await_args_list[1].args == ("enrich_listing_pois", enrichment)
        assert execute.await_args_list[1].kwargs["retry_policy"].maximum_attempts == 3


def test_workflow_does_not_enrich_failed_scrape(monkeypatch):
    from apartment_finder.infrastructure.temporal import workflows
    from apartment_finder.infrastructure.temporal.contracts import EnrichmentRequest

    execute = AsyncMock(side_effect=RuntimeError("scrape failed"))
    monkeypatch.setattr(workflows.workflow, "execute_activity", execute)
    request = ScrapeRequest("https://example.test", enrichment=EnrichmentRequest("version"))
    with pytest.raises(RuntimeError, match="scrape failed"):
        asyncio.run(workflows.ScrapeListingsWorkflow().run(request))
    assert execute.await_count == 1


@pytest.mark.parametrize("exit_code", [0, 1])
def test_enrichment_activity_runs_cli(monkeypatch, exit_code):
    from apartment_finder.infrastructure.temporal.contracts import EnrichmentRequest

    async def check():
        create_process = asyncio.create_subprocess_exec
        captured = []

        async def launch(*args, **kwargs):
            captured.extend(args)
            return await create_process(sys.executable, "-c", f"exit({exit_code})", **kwargs)

        monkeypatch.setenv("APP_ENV", "local")
        monkeypatch.setattr(activities.asyncio, "create_subprocess_exec", launch)
        request = EnrichmentRequest("fingerprint", limit=12)
        if exit_code:
            with pytest.raises(ApplicationError, match="POI enrichment exited"):
                await ActivityEnvironment().run(activities.enrich_listing_pois, request)
        else:
            await ActivityEnvironment().run(activities.enrich_listing_pois, request)
        assert "apartment_finder.interface.enrich_cli" in captured
        assert captured[captured.index("--model-version") + 1] == "fingerprint"
        assert captured[captured.index("--limit") + 1] == "12"

    asyncio.run(check())


@pytest.mark.parametrize(
    "kwargs", [{"model_version": " "}, {"url": "file:///x"}, {"limit": 0}, {"timeout_minutes": 0}]
)
def test_invalid_enrichment_settings(kwargs):
    from apartment_finder.infrastructure.temporal.contracts import EnrichmentRequest

    with pytest.raises(ValueError):
        EnrichmentRequest(**({"model_version": "version"} | kwargs)).validate()


def test_enrichment_rejects_cloud_before_launch(monkeypatch):
    from apartment_finder.infrastructure.temporal.contracts import EnrichmentRequest

    monkeypatch.setenv("APP_ENV", "cloud")
    launch = AsyncMock()
    monkeypatch.setattr(activities.asyncio, "create_subprocess_exec", launch)
    with pytest.raises(ApplicationError, match="local only"):
        asyncio.run(
            ActivityEnvironment().run(activities.enrich_listing_pois, EnrichmentRequest("v"))
        )
    launch.assert_not_called()


def test_old_workflow_history_does_not_schedule_enrichment(monkeypatch):
    from apartment_finder.infrastructure.temporal import workflows
    from apartment_finder.infrastructure.temporal.contracts import EnrichmentRequest

    execute = AsyncMock()
    monkeypatch.setattr(workflows.workflow, "execute_activity", execute)
    monkeypatch.setattr(workflows.workflow, "patched", lambda _: False)
    request = ScrapeRequest("https://example.test", enrichment=EnrichmentRequest("v"))
    asyncio.run(workflows.ScrapeListingsWorkflow().run(request))
    assert execute.await_count == 1


def test_temporal_payload_supports_nested_enrichment_and_old_requests():
    from temporalio.converter import DataConverter

    from apartment_finder.infrastructure.temporal.contracts import EnrichmentRequest

    async def check():
        converter = DataConverter.default
        request = ScrapeRequest("https://example.test", enrichment=EnrichmentRequest("version"))
        payloads = await converter.encode([request])
        assert await converter.decode(payloads, [ScrapeRequest]) == [request]
        legacy = await converter.encode([{"search_url": "https://example.test"}])
        decoded = await converter.decode(legacy, [ScrapeRequest])
        assert decoded == [ScrapeRequest("https://example.test")]

    asyncio.run(check())
