"""
The rate limiter is the safety interlock on parallel scraping: it's what
keeps four workers from hitting imot.bg four times as hard as configured.
These tests use tiny intervals so the suite stays fast, and assert on
*gaps between grants* rather than total elapsed time, which is what the
guarantee actually is.
"""

import threading
import time

from apartment_finder.infrastructure.scrapers.imot_bg_scraper import RateLimiter

INTERVAL = 0.05
# Timing assertions need slack for scheduler jitter; too tight and this
# test flakes on a loaded machine for no good reason.
TOLERANCE = 0.8


def collect_grant_times(limiter: RateLimiter, threads: int, per_thread: int) -> list[float]:
    grants: list[float] = []
    lock = threading.Lock()

    def worker():
        for _ in range(per_thread):
            limiter.acquire()
            with lock:
                grants.append(time.monotonic())

    workers = [threading.Thread(target=worker) for _ in range(threads)]
    for w in workers:
        w.start()
    for w in workers:
        w.join()

    return sorted(grants)


class TestRateLimiter:
    def test_spaces_sequential_requests(self):
        limiter = RateLimiter(INTERVAL)
        grants = collect_grant_times(limiter, threads=1, per_thread=4)

        gaps = [b - a for a, b in zip(grants, grants[1:], strict=False)]
        assert all(gap >= INTERVAL * TOLERANCE for gap in gaps), gaps

    def test_many_threads_do_not_multiply_the_request_rate(self):
        # The whole point. Four workers each sleeping locally would let
        # four requests through per interval; one shared limiter must not.
        limiter = RateLimiter(INTERVAL)
        grants = collect_grant_times(limiter, threads=4, per_thread=3)

        assert len(grants) == 12
        gaps = [b - a for a, b in zip(grants, grants[1:], strict=False)]
        assert all(gap >= INTERVAL * TOLERANCE for gap in gaps), gaps

    def test_first_acquire_does_not_block(self):
        limiter = RateLimiter(min_interval_seconds=10.0)

        start = time.monotonic()
        limiter.acquire()

        # A fresh limiter owes no debt — waiting before the very first
        # request would add the delay to every run for nothing.
        assert time.monotonic() - start < 1.0

    def test_a_slow_caller_does_not_bank_up_credit(self):
        # After a long pause the limiter should grant immediately, but it
        # must not then hand out a burst of backdated slots.
        limiter = RateLimiter(INTERVAL)
        limiter.acquire()
        time.sleep(INTERVAL * 4)

        grants = collect_grant_times(limiter, threads=2, per_thread=2)
        gaps = [b - a for a, b in zip(grants, grants[1:], strict=False)]
        assert all(gap >= INTERVAL * TOLERANCE for gap in gaps), gaps

    def test_zero_interval_is_allowed_and_never_sleeps(self):
        limiter = RateLimiter(0.0)

        start = time.monotonic()
        for _ in range(50):
            limiter.acquire()

        assert time.monotonic() - start < 1.0
