"""Unit tests for the sliding-window login rate limiter (RFC P3-AUTH §10, S4).

Hermetic: the clock is injected so window-expiry is simulated with no real
sleep, and each test builds its own limiter so order is irrelevant.
"""

from __future__ import annotations

from backend.core import ratelimit
from backend.core.ratelimit import SlidingWindowLimiter


class FakeClock:
    """A controllable monotonic clock for deterministic window tests."""

    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def test_blocks_after_max_attempts():
    clock = FakeClock()
    lim = SlidingWindowLimiter(max_attempts=3, window_seconds=60, clock=clock)
    key = "1.2.3.4"
    assert lim.check(key) == (True, 0)
    for _ in range(3):
        lim.record(key)
    allowed, retry_after = lim.check(key)
    assert allowed is False
    assert retry_after > 0


def test_window_expiry_allows_again():
    clock = FakeClock()
    lim = SlidingWindowLimiter(max_attempts=3, window_seconds=60, clock=clock)
    key = "1.2.3.4"
    for _ in range(3):
        lim.record(key)
    assert lim.check(key)[0] is False
    # Age every attempt out of the 60s window (no real sleep).
    clock.advance(61)
    assert lim.check(key) == (True, 0)


def test_retry_after_counts_down_with_partial_window():
    clock = FakeClock()
    lim = SlidingWindowLimiter(max_attempts=2, window_seconds=100, clock=clock)
    key = "1.2.3.4"
    lim.record(key)
    clock.advance(40)
    lim.record(key)
    allowed, retry_after = lim.check(key)
    assert allowed is False
    # Oldest attempt is 40s old → ~60s left in its window.
    assert 55 <= retry_after <= 60


def test_reset_clears_a_single_key():
    clock = FakeClock()
    lim = SlidingWindowLimiter(max_attempts=2, window_seconds=60, clock=clock)
    a, b = "1.1.1.1", "2.2.2.2"
    for _ in range(2):
        lim.record(a)
        lim.record(b)
    assert lim.check(a)[0] is False
    assert lim.check(b)[0] is False
    lim.reset(a)
    assert lim.check(a) == (True, 0)
    assert lim.check(b)[0] is False  # other key untouched


def test_clear_drops_all_state():
    clock = FakeClock()
    lim = SlidingWindowLimiter(max_attempts=1, window_seconds=60, clock=clock)
    lim.record("x")
    assert lim.check("x")[0] is False
    lim.clear()
    assert lim.check("x") == (True, 0)


def test_module_reset_rebuilds_shared_limiter():
    ratelimit.login_limiter.record("z")
    ratelimit.login_limiter.record("z")
    ratelimit.reset()
    # A fresh limiter instance with no recorded attempts.
    assert ratelimit.login_limiter.check("z") == (True, 0)
