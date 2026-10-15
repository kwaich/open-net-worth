"""A correctness-first, data-version-keyed cache for expensive READ computations.

Why: on a single-user localhost box the tabs recompute net worth / projections / valuations
from ~2k transactions + ~200 instruments on EVERY request, and a tab-load fires ~10 of these at
once. Nothing is shared, so the GIL serialises a pile of redundant work into multi-second waits.
This memoises each expensive read and, crucially, invalidates the instant the underlying data
changes — so it can never serve a stale money number.

The cache key includes an epoch = (SQLite ``PRAGMA data_version``, ``core.io.file_write_epoch()``):

- ``PRAGMA data_version`` on a persistent probe connection changes ONLY after another connection
  commits, and is observable only once that commit is durable/visible (verified: a read that
  sees the new version already sees the new rows). So a DB write busts the cache post-commit,
  with no stale-under-new-key window.
- ``file_write_epoch()`` covers the remaining file-backed stores that ``data_version`` cannot see
  (profile.json → base currency, fx_cache, skills); it is bumped AFTER each atomic file write
  lands.

Guarantees:
- **No staleness.** A value tagged with an epoch is only ever served while that epoch is current.
  The leader tags the result with the epoch read BEFORE computing, so a write landing mid-compute
  only moves the epoch forward and invalidates the just-stored entry — it can never publish OLD
  data under a NEWER epoch.
- **Single-flight.** Concurrent misses for one key compute once; the rest wait and share it —
  this is what collapses the concurrent tab-load fan-out.
- **Mutation-safe.** Callers get a ``deepcopy``; mutating it cannot corrupt the cached value or
  another caller's copy.
- **SQLite-only.** Non-SQLite engines (Postgres, CI) bypass the cache entirely — correctness over
  speed on an engine the owner does not run.
- **Engine-scoped.** If the process swaps engines (test isolation resets ``get_engine()``), the
  probe + store reset automatically, so a stale epoch from another database can never collide.
"""

from __future__ import annotations

import copy
import os
import threading
from typing import Any, Callable

from backend.core import io as core_io
from backend.core.user_time import user_today
from backend.db.engine import get_engine

_lock = threading.Lock()  # guards _store + _inflight
_store: dict[Any, tuple[tuple, Any]] = {}  # key -> (epoch, result)
_inflight: dict[Any, threading.Event] = {}


def _memo_enabled() -> bool:
    """Memoisation kill switch — OFF unless ONW_READ_CACHE=1 (owner call 2026-08-12:
    endpoints first get fully optimized with the best UX, then a caching layer gets
    REDESIGNED deliberately; until then no derived money number is ever served from a
    store). The compute gate below is unaffected — it shapes concurrency, it caches
    nothing."""
    return os.environ.get("ONW_READ_CACHE", "0") == "1"


# A tab-load fan-out lands ~15 of these computations in the threadpool at once, all
# pure-Python, and the GIL makes that WORSE than running them one by one (measured on real
# data: 18 endpoints serial-cold 2.7s, the same 18 concurrent 6.8s — every request inflating
# to the whole burst's wall). Cap how many leader computes run simultaneously: 3 measured
# fastest (some DB I/O overlaps; more just thrashes context switches).
_compute_gate = threading.BoundedSemaphore(3)
_gate_state = threading.local()  # .held — this thread is already inside the gate


def _gated_compute(fn: Callable, args: tuple, kwargs: dict) -> Any:
    """Run one expensive compute with at most 3 running concurrently.

    A leader that is itself running inside the gate may call other cached reads (projection →
    hydration context): those nested computes bypass rather than self-deadlock. A leader that
    cannot get a slot within 8s computes ungated — liveness over shaping, never a hard wall."""
    if getattr(_gate_state, "held", False):
        return fn(*args, **kwargs)
    acquired = _compute_gate.acquire(timeout=8)
    if acquired:
        _gate_state.held = True
    try:
        return fn(*args, **kwargs)
    finally:
        if acquired:
            _gate_state.held = False
            _compute_gate.release()


_probe_lock = threading.Lock()  # serialises the (not thread-safe) probe connection
_probe = None
_probe_engine = None  # the engine the probe belongs to; identity change ⇒ reset


def _reset_locked() -> None:
    _store.clear()
    _inflight.clear()


def _db_data_version() -> int | None:
    """SQLite ``PRAGMA data_version`` on a persistent probe connection, or None on non-SQLite
    (caller then bypasses the cache). Recreates the probe + clears the store if the engine
    identity changed (test isolation / a re-init)."""
    global _probe, _probe_engine
    eng = get_engine()
    if eng.dialect.name != "sqlite":
        return None
    with _probe_lock:
        if _probe_engine is not eng:
            if _probe is not None:
                try:
                    _probe.close()
                except Exception:  # noqa: BLE001 — best effort on a dead engine
                    pass
            _probe = eng.connect()
            _probe_engine = eng
            with _lock:
                _reset_locked()
        return int(_probe.exec_driver_sql("PRAGMA data_version").scalar())


def current_epoch() -> tuple | None:
    """The cache epoch, or None when caching must be bypassed (non-SQLite).

    ``user_today()`` is part of the epoch so date-dependent reads (projections from "today",
    month-to-date, "today's movers") recompute at the local-date boundary even if no write
    happened overnight — a DB/file write is not the only thing that can change their answer."""
    dv = _db_data_version()
    if dv is None:
        return None
    return (dv, core_io.file_write_epoch(), user_today().isoformat())


def reset() -> None:
    """Drop everything (probe + store). For dispose_engine() and tests."""
    global _probe, _probe_engine
    with _probe_lock:
        if _probe is not None:
            try:
                _probe.close()
            except Exception:  # noqa: BLE001
                pass
        _probe = None
        _probe_engine = None
    with _lock:
        _reset_locked()


def cached(
    key_fn: Callable[..., Any], *, when: Callable[..., bool] | None = None
) -> Callable[[Callable], Callable]:
    """Memoise a read function on the data-version epoch. ``key_fn(*args, **kwargs)`` returns a
    hashable identity for the call (INCLUDE every arg that changes the result). The wrapped
    function's result is treated as immutable and returned as a deepcopy.

    ``when(*args, **kwargs)`` (optional): only cache a call when this returns True — for a
    function whose fast API path is cacheable but whose internal path takes pre-loaded mutable
    inputs (lists/dicts) that must not be memoised. A non-cacheable call computes directly."""

    def decorate(fn: Callable) -> Callable:
        def wrapper(*args, **kwargs):
            if when is not None and not when(*args, **kwargs):
                return fn(*args, **kwargs)  # this call is not cacheable — compute directly
            if not _memo_enabled():
                # Owner call 2026-08-12: memoisation OFF by default until the endpoints are
                # fully optimized and a caching layer is REDESIGNED deliberately — the compute
                # gate below stays, it is concurrency shaping, not caching.
                return _gated_compute(fn, args, kwargs)
            epoch = current_epoch()
            if epoch is None:  # non-sqlite: no cache, compute directly
                return fn(*args, **kwargs)
            key = (fn.__qualname__, key_fn(*args, **kwargs))
            with _lock:
                entry = _store.get(key)
                if entry is not None and entry[0] == epoch:
                    return copy.deepcopy(entry[1])
                waiter = _inflight.get(key)
                leader = waiter is None
                if leader:
                    _inflight[key] = threading.Event()
            if not leader:
                waiter.wait(timeout=30)
                with _lock:
                    entry = _store.get(key)
                if entry is not None and entry[0] == epoch:
                    return copy.deepcopy(entry[1])
                # epoch moved while waiting: compute uncached (gated — every waiter falling
                # back at once is exactly the stampede the gate exists for)
                return _gated_compute(fn, args, kwargs)
            try:
                result = _gated_compute(fn, args, kwargs)
                with _lock:
                    _store[key] = (epoch, result)  # tag with the PRE-compute epoch (no stale-new)
                return copy.deepcopy(result)
            finally:
                with _lock:
                    ev = _inflight.pop(key, None)
                if ev is not None:
                    ev.set()

        wrapper.__wrapped__ = fn
        wrapper.__name__ = getattr(fn, "__name__", "cached")
        wrapper.__qualname__ = getattr(fn, "__qualname__", "cached")
        return wrapper

    return decorate
