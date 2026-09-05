"""
Logging setup belongs to the interface layer for the same reason wiring
does: choosing handlers, levels and formats is a decision about how this
program is operated, not about what the domain means.

The rule this file exists to enforce: only the composition root configures
logging. Every other module does `logger = logging.getLogger(__name__)` and
nothing else — no basicConfig, no handlers, no levels. That's what makes
the whole app's verbosity controllable from one flag, and what keeps the
library code importable from tests without hijacking pytest's own logging.

Logs go to stderr, deliberately. The CLI prints its result summary to
stdout, so `apartment-finder-scrape ... > results.txt` keeps the report and
lets the operational chatter stay on the terminal.
"""

import logging
import os
import sys

DEFAULT_LEVEL = "INFO"
# short_name, not name: the real logger names are up to 62 characters
# ("apartment_finder.application.use_cases.scrape_and_store_listings"),
# which is longer than most of the messages beside them. ShortNameFilter
# below collapses them to "layer.module".
LOG_FORMAT = "%(asctime)s %(levelname)-8s %(short_name)-38s | %(message)s"
DATE_FORMAT = "%H:%M:%S"

PACKAGE = "apartment_finder"

# Third-party loggers that are actively harmful at DEBUG rather than merely
# noisy: azure.identity and azure.core's HTTP policy log request and
# response headers, which is how a bearer token ends up in a log file, and
# playwright logs every protocol frame (megabytes per listing). Our own
# DEBUG output is the point of --log-level debug; theirs isn't.
NOISY_LOGGERS = (
    "azure",
    "azure.core.pipeline.policies.http_logging_policy",
    "azure.identity",
    "playwright",
    "urllib3",
    "asyncio",
)
NOISY_LOGGER_FLOOR = logging.INFO


def shorten_logger_name(name: str) -> str:
    """'apartment_finder.application.use_cases.scrape_and_store_listings'
    -> 'application.scrape_and_store_listings'.

    Keeping the first component means the layer stays visible, which is
    exactly the thing worth knowing when reading a clean-architecture
    codebase's logs: whether a line came from the use case or the adapter.
    Third-party names pass through untouched so 'azure.identity' is still
    recognisably not ours.
    """
    parts = name.split(".")
    if parts[0] != PACKAGE:
        return name
    parts = parts[1:]
    if not parts:
        return PACKAGE
    if len(parts) == 1:
        return parts[0]
    return f"{parts[0]}.{parts[-1]}"


class ShortNameFilter(logging.Filter):
    """Attaches the abbreviated name LOG_FORMAT reads.

    A filter rather than a custom Formatter subclass: filters compose with
    whatever formatter or handler is in play, so this keeps working if a
    JSON handler or a file handler is added later.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        record.short_name = shorten_logger_name(record.name)
        return True


def resolve_level(level: str | None = None) -> int:
    """Turn a level name into a numeric level, preferring an explicit
    argument (the --log-level flag) over LOG_LEVEL in the environment.

    LOG_LEVEL exists so the Docker image and compose can turn up verbosity
    without rewriting the container's command.
    """
    name = (level or os.environ.get("LOG_LEVEL") or DEFAULT_LEVEL).strip().upper()
    numeric = logging.getLevelNamesMapping().get(name)
    if numeric is None:
        valid = "DEBUG, INFO, WARNING, ERROR, CRITICAL"
        raise ValueError(f"Unknown log level {name!r} — expected one of {valid}")
    return numeric


def configure_logging(level: str | None = None) -> int:
    """Install the one and only logging configuration. Returns the level
    actually applied, so the caller can log it."""
    numeric = resolve_level(level)

    # force=True replaces any handler a library installed at import time;
    # without it a stray basicConfig elsewhere would silently win and this
    # format/level would be ignored.
    logging.basicConfig(
        level=numeric,
        stream=sys.stderr,
        format=LOG_FORMAT,
        datefmt=DATE_FORMAT,
        force=True,
    )

    # The filter goes on the handler, not on a logger: on a logger it would
    # only see records logged directly to it and miss everything propagating
    # up from child loggers, so most lines would arrive without short_name
    # and the format string would raise.
    for handler in logging.getLogger().handlers:
        handler.addFilter(ShortNameFilter())

    # max() rather than a flat INFO: at --log-level warning the third-party
    # loggers should get quieter too, not be pinned back up to INFO.
    for name in NOISY_LOGGERS:
        logging.getLogger(name).setLevel(max(numeric, NOISY_LOGGER_FLOOR))

    return numeric
