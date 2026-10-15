"""watch_history — keeps each watched ticker's daily closes and sector on file, so the watched
pulse can draw its trend line against its SECTOR fund and judge moves against its own history.

Nothing else records a watched-only name's closes (held names get theirs from the live price path
and the startup sweep). Two entry points:

* ``sweep`` — the 12-hourly ``watch_history`` job (via ``prices.backfill_watched_instruments``):
  every watched ticker, plus the benchmarks and sector funds the trend line compares against.
* ``request`` — the pulse asks for the names it had to draw WITHOUT a line (just added, or never
  fetched): a one-off background top-up, at most once per ticker per ``RETRY_SECS``, so a new
  ticker gets its line on the next refresh instead of up to 12 hours later.

A ticker with an instrument is fetched through ``prices.backfill_history``; one without (typed in
by symbol) is fetched by that symbol and stored under it — the key ``watch_context.history_for``
reads. Manual-priced instruments and crypto typed by symbol have no Yahoo history and are skipped.
Thin history → a year; otherwise a one-month refresh (idempotent: dates are never overwritten).
"""

from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timedelta, timezone

from backend.core.user_time import user_today

_log = logging.getLogger(__name__)

MIN_POINTS = 220  # ≈ a year of closes: enough for a 200-day average + lookback
RETRY_SECS = 6 * 3600
_requested: dict[str, float] = {}
_lock = threading.Lock()


def targets(items: list[dict]) -> list[tuple[str, str, dict]]:
    """(history key, "instrument" | "symbol", item) for each watched ticker that has an upstream."""
    from backend.db.instrument_store import InstrumentStore

    insts = InstrumentStore()
    out: list[tuple[str, str, dict]] = []
    for item in items:
        if item.get("type", "ticker") != "ticker":
            continue
        iid = item.get("instrument_id")
        if iid:
            inst = insts.find_by_id(iid) or {}
            pid = inst.get("price_source_id") or ""
            if pid and (inst.get("price_source") or "").lower() != "manual":
                out.append((pid, "instrument", item))
            continue
        symbol = (item.get("value") or "").strip()
        if symbol and (item.get("asset_class") or "equity").lower() != "crypto":
            out.append((symbol, "symbol", item))
    return out


def _top_up(key: str, kind: str) -> int:
    from backend.db.price_history_store import PriceHistoryStore
    from backend.services import prices

    since = (user_today() - timedelta(days=400)).isoformat()
    thin = len(PriceHistoryStore().series(key, since=since)) < MIN_POINTS
    lookback = "1Y" if thin else "1M"
    # A bar dated today (UTC) may be a session still trading — every venue's live session falls
    # on its UTC date — and a stored date is never rewritten, so only settled days are kept.
    before = datetime.now(timezone.utc).date().isoformat()
    if kind == "symbol":
        return prices.backfill_symbol_history(key, lookback=lookback, before=before)
    return prices.backfill_history(key, lookback=lookback, before=before)


def ensure_sectors(items: list[dict]) -> int:
    """Look up the sector of every watched equity that has none on file (one Yahoo call each,
    cached for good), so its trend line can compare with its sector fund. Returns how many."""
    from backend.services import prices
    from backend.services.watch_context import instruments_for, sector_for

    insts = instruments_for(items)
    cache = prices.load_sectors_cache()
    found = 0
    for item in items:
        if (item.get("asset_class") or "equity").lower() != "equity":
            continue
        if item.get("type", "ticker") != "ticker" or sector_for(item, insts, cache):
            continue
        if prices.ensure_sector((item.get("value") or "").strip()):
            found += 1
    return found


def sweep(items: list[dict], *, pace: float) -> dict[str, int]:
    """Top up every watched ticker, then the comparison funds; fill in missing sectors."""
    from backend.services.benchmark_seed import reference_price_ids

    results: dict[str, int] = {}
    for key, kind in [(k, kind) for k, kind, _ in targets(items)] + [
        (pid, "instrument") for pid in reference_price_ids()
    ]:
        if key in results:
            continue
        results[key] = _top_up(key, kind)
        time.sleep(pace)
    ensure_sectors(items)
    return results


def _claim(keys: list[str], now: float) -> list[str]:
    with _lock:
        fresh = [k for k in keys if now - _requested.get(k, 0.0) >= RETRY_SECS]
        for k in fresh:
            _requested[k] = now
    return fresh


def request(items: list[dict]) -> list[str]:
    """Background top-up for watched names the pulse drew without a line (or without a sector).
    Returns the keys it started fetching; never blocks, never raises, never runs in QA mode."""
    from backend.core.config import get_settings

    if not items or get_settings().onw_qa_mode:
        return []
    try:
        wanted = targets(items)
    except Exception:
        _log.debug("watch_history: target lookup failed", exc_info=True)
        return []
    fresh = set(_claim([k for k, _, _ in wanted], time.time()))
    jobs = [(k, kind) for k, kind, _ in wanted if k in fresh]
    todo = [item for k, _, item in wanted if k in fresh]
    if not jobs:
        return []

    def run() -> None:
        for key, kind in jobs:
            try:
                _top_up(key, kind)
            except Exception:
                _log.warning("watch_history: top-up failed for %s", key, exc_info=True)
        try:
            ensure_sectors(todo)
        except Exception:
            _log.warning("watch_history: sector lookup failed", exc_info=True)

    threading.Thread(target=run, daemon=True, name="watch-history-request").start()
    return [k for k, _ in jobs]
