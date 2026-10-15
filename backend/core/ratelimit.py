"""In-memory sliding-window rate limiter (RFC P3-AUTH §10 hardening — S4).

A tiny, dependency-free (stdlib only) brute-force guard for ``POST /api/auth/login``.
It is a leaf in the dependency graph (imports nothing internal) so it stays
unit-testable and import-linter-clean.

Design:
- Track FAILED attempts only, keyed by client IP. A successful login clears the
  key (``reset(key)``) so a legitimate user is never throttled.
- After ``max_attempts`` failures inside ``window_seconds`` (a sliding window),
  ``check(key)`` reports the key as blocked with the seconds until the oldest
  in-window attempt ages out (the ``Retry-After`` hint).
- State is a module-level dict keyed by IP → list of monotonic timestamps,
  pruned by the window on each touch. It resets on process restart, which is
  acceptable for a single-user self-hosted instance (no persistence by design).

Hermetic testing: the clock is injectable (``clock=time.monotonic`` by default),
so a test can simulate window expiry without a real ``sleep``; ``reset()`` clears
all state for order-independence. The module exposes a shared ``login_limiter``
plus a module-level ``reset()`` that clears it.
"""

from __future__ import annotations

import threading
import time
from typing import Callable, Dict, List


class SlidingWindowLimiter:
    """A reusable per-key sliding-window failed-attempt limiter.

    ``record(key)``  — note one failed attempt (now).
    ``check(key)``   — ``(allowed, retry_after)``: ``allowed`` False once the key
                       has ``max_attempts`` attempts in the window; ``retry_after``
                       is seconds until the next attempt would be permitted.
    ``reset(key)``   — clear one key (call on a SUCCESSFUL login).
    ``clear()``      — drop all state (tests / restart).
    """

    def __init__(
        self,
        max_attempts: int,
        window_seconds: float,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.max_attempts = max_attempts
        self.window_seconds = window_seconds
        self._clock = clock
        self._lock = threading.Lock()
        self._hits: Dict[str, List[float]] = {}

    def _prune(self, key: str, now: float) -> List[float]:
        cutoff = now - self.window_seconds
        kept = [t for t in self._hits.get(key, ()) if t > cutoff]
        if kept:
            self._hits[key] = kept
        else:
            self._hits.pop(key, None)
        return kept

    def record(self, key: str) -> None:
        """Record one failed attempt for ``key`` at the current time."""
        with self._lock:
            now = self._clock()
            kept = self._prune(key, now)
            kept.append(now)
            self._hits[key] = kept

    def check(self, key: str) -> tuple[bool, int]:
        """Return ``(allowed, retry_after_seconds)`` for ``key`` without recording."""
        with self._lock:
            now = self._clock()
            kept = self._prune(key, now)
            if len(kept) < self.max_attempts:
                return True, 0
            # Blocked until the oldest in-window attempt ages out.
            retry_after = self.window_seconds - (now - kept[0])
            return False, max(1, int(retry_after) + (1 if retry_after % 1 else 0))

    def reset(self, key: str) -> None:
        """Clear all recorded attempts for one key (e.g. after a successful login)."""
        with self._lock:
            self._hits.pop(key, None)

    def clear(self) -> None:
        """Drop all state (process restart / test isolation)."""
        with self._lock:
            self._hits.clear()


# Tunable defaults (RFC §10: 5 failures / 15 min). Module-level so a test can
# monkeypatch them and reconstruct the shared limiter cheaply.
LOGIN_MAX_ATTEMPTS = 5
LOGIN_WINDOW_SECONDS = 15 * 60  # 15 minutes

# The shared limiter the login route uses. Reconstruct via ``reset()`` if a test
# overrides the constants above.
login_limiter = SlidingWindowLimiter(LOGIN_MAX_ATTEMPTS, LOGIN_WINDOW_SECONDS)


def reset() -> None:
    """Rebuild the shared login limiter from the current module constants.

    Used by tests for order-independence (and after a monkeypatch of the
    ``LOGIN_*`` constants) — clears all recorded attempts.
    """
    global login_limiter
    login_limiter = SlidingWindowLimiter(LOGIN_MAX_ATTEMPTS, LOGIN_WINDOW_SECONDS)
