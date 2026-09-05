# Chromium is what shapes this image. Two things about it drive every
# decision below: the playwright Python package and the browser binary
# install separately (same split as `make install`), and the browser links
# against ~40 system libraries that python:slim doesn't ship.

FROM python:3.12-slim AS builder

COPY --from=ghcr.io/astral-sh/uv:0.12.9 /uv /usr/local/bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

WORKDIR /app

# Dependencies resolve from the lockfile alone, so this layer stays cached
# until uv.lock changes — editing src/ doesn't re-download playwright's
# 47 MB wheel.
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-install-project

# README.md is copied because pyproject's `readme` field points at it and
# the build backend reads it while packaging the project.
COPY README.md ./
COPY src ./src
# --no-editable copies the package into site-packages rather than linking
# back to /app/src. That's what lets the runtime stage take just the venv
# and leave the source tree behind.
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-editable


FROM python:3.12-slim AS runtime

# PLAYWRIGHT_BROWSERS_PATH defaults to ~/.cache/ms-playwright — i.e. inside
# root's home, unreadable by the non-root user this container runs as.
# Pinning it to an absolute path is what makes the browser shareable, and it
# has to be set for both the install below and the run.
# PYTHONUNBUFFERED so log lines reach `docker logs` as they happen rather
# than sitting in a pipe buffer until the process exits. LOG_LEVEL is
# overridable at run time with -e LOG_LEVEL=debug.
ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright \
    PATH=/app/.venv/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    LOG_LEVEL=info \
    CHROMIUM_IN_CONTAINER=1

WORKDIR /app

COPY --from=builder /app/.venv /app/.venv

# --with-deps is the apt-get half of the install: it pulls the shared
# libraries Chromium needs. This has to run in the runtime stage, not the
# builder — those libraries live in /usr/lib, which copying a venv across
# stages doesn't carry over.
RUN playwright install --with-deps chromium \
    && chmod -R o+rX /ms-playwright \
    && rm -rf /var/lib/apt/lists/*

# Migrations ride along so the image can also run
# `alembic upgrade head`, not just scrape.
COPY alembic.ini ./
COPY db_migrations ./db_migrations

# Chromium refuses to run as root without --no-sandbox, and giving it a real
# user is the better half of that fix.
RUN useradd --create-home --uid 1000 scraper
USER scraper

# ENTRYPOINT rather than CMD so the search URL and --max-pages pass straight
# through: docker run apartment-finder "<search-url>" --max-pages 2
ENTRYPOINT ["apartment-finder-scrape"]
