"""Per-request phase timing — the "where does a request spend its milliseconds" instrument.

A mutable stats dict lives in a ``ContextVar``. The request timing middleware (``backend/main.py``)
creates it at request start and reads it back when the response goes out; the SQLAlchemy engine
cursor events (``backend/db/engine.py``) accumulate DB-driver time, query count and rows into it
as queries run. It works across the sync-endpoint threadpool hop because a ``ContextVar`` copy
shares the SAME dict object, so mutations from the worker thread are visible to the middleware.

``core`` stays a stdlib leaf — this is only ``contextvars``.
"""

from __future__ import annotations

import contextvars

_req_stats: contextvars.ContextVar = contextvars.ContextVar("onw_req_stats", default=None)


def start_request() -> dict:
    """Begin a request's timing scope; returns the stats dict the middleware reads back."""
    stats = {"db_ms": 0.0, "db_queries": 0, "db_rows": 0}
    _req_stats.set(stats)
    return stats


def current() -> dict | None:
    """The active request's stats dict, or None outside a request (scheduler/CLI/tests)."""
    return _req_stats.get()


def record_db(elapsed_ms: float, rows: int = 0) -> None:
    """Fold one executed statement's driver time + rowcount into the active request (no-op when
    there is no active request scope)."""
    stats = _req_stats.get()
    if stats is None:
        return
    stats["db_ms"] += elapsed_ms
    stats["db_queries"] += 1
    if rows and rows > 0:
        stats["db_rows"] += rows
