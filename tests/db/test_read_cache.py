"""read_cache — the data-version-keyed read memo (perf/latency-fixes).

The guarantees that make it safe to put in front of money reads: a HIT recomputes nothing and
returns a mutation-safe copy; a DB write OR a file-store write invalidates immediately (no stale
money numbers); concurrent misses single-flight to ONE computation; the local-date rolls the
epoch. Each test drives the REAL invalidation seams (a committed DB write; an atomic file write),
not a mocked epoch.
"""

from __future__ import annotations

import threading
import time

import pytest
from sqlalchemy import text

from backend.core import io as core_io
from backend.core.paths import DATA_DIR
from backend.db import read_cache
from backend.db.engine import get_engine
from tests.db.conftest import sqlite_only

_SQLITE_ONLY = sqlite_only(
    "read_cache bypasses non-SQLite engines by design (its epoch is PRAGMA data_version)"
)


@pytest.fixture(autouse=True)
def _memo_on(monkeypatch):
    """Memoisation is OFF by default in production (owner call 2026-08-12) — these tests
    exercise the memo semantics themselves, so they opt in. The two gate tests below hold
    with the memo on OR off; they run under the same opt-in for determinism."""
    monkeypatch.setenv("ONW_READ_CACHE", "1")


def _counting_fn():
    calls = {"n": 0}

    @read_cache.cached(lambda x=0: x)
    def fn(x=0):
        calls["n"] += 1
        return {"x": x, "n": calls["n"], "tags": ["a"]}

    return fn, calls


@_SQLITE_ONLY
def test_hit_recomputes_nothing_and_returns_a_copy(db_isolated):
    fn, calls = _counting_fn()
    a = fn(5)
    b = fn(5)
    assert calls["n"] == 1  # second call was a hit
    assert a == b  # identical value
    assert a is not b and a["tags"] is not b["tags"]  # deep copy — mutation-isolated
    a["tags"].append("mutated")
    assert fn(5)["tags"] == ["a"]  # the cached value is untouched by a caller mutating its copy


@_SQLITE_ONLY
def test_db_write_invalidates(db_isolated):
    fn, calls = _counting_fn()
    fn(1)
    assert calls["n"] == 1
    fn(1)
    assert calls["n"] == 1  # hit
    with get_engine().begin() as conn:  # a committed DB write bumps PRAGMA data_version
        conn.execute(text("create table if not exists _rc_probe(v int)"))
        conn.execute(text("insert into _rc_probe(v) values (1)"))
    fn(1)
    assert calls["n"] == 2  # invalidated → recomputed


@_SQLITE_ONLY
def test_file_write_invalidates(db_isolated):
    fn, calls = _counting_fn()
    fn(2)
    fn(2)
    assert calls["n"] == 1  # hit
    core_io.atomic_write_text(DATA_DIR / "_rc_probe.json", '{"base": "USD"}')
    try:
        fn(2)
        assert calls["n"] == 2  # a file-store write (e.g. base currency) also invalidates
    finally:
        (DATA_DIR / "_rc_probe.json").unlink(missing_ok=True)


@_SQLITE_ONLY
def test_distinct_args_cache_separately(db_isolated):
    fn, calls = _counting_fn()
    fn(1)
    fn(2)
    assert calls["n"] == 2
    fn(1)
    fn(2)
    assert calls["n"] == 2  # both were hits


@_SQLITE_ONLY
def test_concurrent_misses_single_flight(db_isolated):
    calls = {"n": 0}

    @read_cache.cached(lambda: "k")
    def slow():
        calls["n"] += 1
        time.sleep(0.15)  # hold the flight so the others pile up behind it
        return {"ok": True}

    threads = [threading.Thread(target=slow) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert calls["n"] == 1  # eight concurrent misses collapsed to one computation


@_SQLITE_ONLY
def test_leader_computes_are_gated_to_three_concurrent(db_isolated):
    """A tab-load fan-out of DISTINCT keys must not run all leaders simultaneously — the GIL
    makes 15-at-once slower than one-by-one (measured). At most 3 compute concurrently."""
    running = {"now": 0, "peak": 0}
    guard = threading.Lock()

    @read_cache.cached(lambda x: x)
    def slow(x):
        with guard:
            running["now"] += 1
            running["peak"] = max(running["peak"], running["now"])
        time.sleep(0.05)
        with guard:
            running["now"] -= 1
        return {"x": x}

    threads = [threading.Thread(target=slow, args=(i,)) for i in range(12)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert running["peak"] <= 3  # the compute gate, not the threadpool, sets the ceiling


def test_nested_cached_calls_do_not_deadlock_under_the_gate(db_isolated):
    """A gated leader may itself call other cached reads (projection → hydration context).
    The nested compute must bypass the gate — 12 threads × nested calls finish promptly."""

    @read_cache.cached(lambda x: ("inner", x))
    def inner(x):
        time.sleep(0.01)
        return {"inner": x}

    @read_cache.cached(lambda x: ("outer", x))
    def outer(x):
        return {"outer": inner(x)["inner"]}

    results = {}

    def run(i):
        results[i] = outer(i % 4)

    threads = [threading.Thread(target=run, args=(i,)) for i in range(12)]
    t0 = time.monotonic()
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert time.monotonic() - t0 < 5  # nowhere near the 8s liveness valve
    assert all(results[i] == {"outer": i % 4} for i in range(12))
