"""News-arrival event registry (RFC news-ingestion-latency.md) — a ``core`` leaf, mirrors
``core.channels``.

Lets ``monitoring`` register CONSUMERS that react to freshly-inserted news evidence rows
without ``core`` importing ``monitoring``/``services`` (the inversion every seam in this module
follows — see ``core.io.register_write_hook`` / ``core.channels.register_channel``).
``monitoring.collect.gather_rss``/``gather_matched`` call :func:`emit` once per fetch with the
batch of NEWLY INSERTED evidence rows (a row whose ``seen_count`` is 1 after the dedup-merge —
callers determine that, this module just fans the batch out). Consumers are in-process async
fns; one consumer's exception is isolated and logged so a broken consumer (e.g. the
significance screen) can never sink a poll tick. This is the "strategies plug in here" surface
the RFC calls out — a future push lane (Phase 3, Alpaca websocket) feeds the SAME seam directly,
no consumer-side change needed.
"""

from __future__ import annotations

import logging
from typing import Awaitable, Callable

logger = logging.getLogger(__name__)

# A consumer takes the batch of newly-inserted evidence rows (dicts, full evidence-row shape —
# id/url/title/snippet/published_at/first_seen/subjects/source/via/...) and returns nothing.
Consumer = Callable[[list[dict]], Awaitable[None]]

_CONSUMERS: dict[str, Consumer] = {}


def register_consumer(name: str, fn: Consumer) -> None:
    """Register an async arrival consumer under `name`. Last registration wins."""
    _CONSUMERS[name] = fn


def registered_consumers() -> dict[str, Consumer]:
    """A snapshot copy of the currently-registered consumers (name → async fn)."""
    return dict(_CONSUMERS)


def unregister_consumer(name: str) -> None:
    """Remove a consumer (used by tests to keep registrations isolated)."""
    _CONSUMERS.pop(name, None)


async def emit(rows: list[dict]) -> None:
    """Hand a batch of newly-inserted evidence rows to every registered consumer.

    Best-effort + isolated: one consumer's exception is caught and logged (by class only, never
    raw text) so it can never sink the caller's poll. No-op on an empty batch or no consumers."""
    if not rows:
        return
    for name, fn in list(_CONSUMERS.items()):
        try:
            await fn(rows)
        except Exception as exc:
            logger.warning("news_arrivals: consumer %s failed: %s", name, type(exc).__name__)
