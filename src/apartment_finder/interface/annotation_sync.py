"""Continuous pull of saved annotations; approval remains a separate action."""

import argparse
import logging
import math
from threading import Event
from uuid import UUID

from apartment_finder.application.use_cases.sync_annotations import SyncAnnotations

logger = logging.getLogger(__name__)


def positive_interval(value: str) -> float:
    try:
        interval = float(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("interval must be a positive finite number") from exc
    if not math.isfinite(interval) or interval <= 0:
        raise argparse.ArgumentTypeError("interval must be a positive finite number")
    return interval


def watch_annotations(
    synchronizer: SyncAnnotations,
    *,
    batch_id: UUID | None = None,
    interval: float = 30.0,
    stop_event: Event | None = None,
) -> None:
    """Sync immediately, then wait between completed passes until stopped.

    Reload tasks each pass so newly exported tasks are discovered. A temporary
    database or service failure must not terminate automatic synchronization.
    """
    interval = positive_interval(str(interval))
    stop_event = stop_event if stop_event is not None else Event()
    logger.info(
        "Watching saved annotations every %s seconds; batch=%s", interval, batch_id or "all"
    )
    while not stop_event.is_set():
        try:
            result = synchronizer.execute(batch_id=batch_id)
            logger.info("Synced %d task(s), %d failed.", result.synced, result.failed)
        except Exception:  # noqa: BLE001 - retry the next polling pass after transient failures
            logger.exception("Annotation sync pass failed; retrying in %s seconds", interval)
        stop_event.wait(interval)
