"""A saved price trigger points at an instrument, so its quote currency is known.

The editor's ticker search resolves a market pick before it is saved; Lucius (or an API caller)
may save a bare ticker the book has never seen — "TSLA". At save, such a ticker is resolved
against the market (find-or-create, the same lookup the command palette uses) and the trigger
stores the instrument id. A lookup that fails keeps the ticker as typed: the price monitor still
fetches it by symbol, it just has no currency to show.
"""

from __future__ import annotations

import logging

from backend.db.instrument_store import InstrumentStore

logger = logging.getLogger(__name__)


def pin_price_instrument(trigger: dict) -> dict:
    if trigger.get("kind") != "event" or trigger.get("event") != "price":
        return trigger
    when = trigger.get("when") or {}
    symbol = str(when.get("symbol") or "").strip()
    if not symbol or InstrumentStore().find_by_id(symbol):
        return trigger
    try:
        from backend.services.instruments import resolve_instrument

        instrument, _ = resolve_instrument(symbol)
    except Exception as exc:
        logger.info("automation price ticker %r not resolved: %s", symbol, exc)
        return trigger
    if not instrument:
        return trigger
    return {**trigger, "when": {**when, "symbol": instrument["id"]}}
