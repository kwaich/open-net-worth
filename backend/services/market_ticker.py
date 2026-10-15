"""Market rows for the topbar ticker — commodities, coins and FX, beside the holdings the
frontend already has.

The holdings half of the ticker needs no server work: the frontend already holds every
position with its own day move (`usePortfolio().positionRows`). What it CANNOT get is a quote
for something the owner does not hold — gold, oil, BTC against the base currency, EUR/SGD.
That is all this module supplies.

`prices.fetch_yahoo_quotes` is the seam: one batch call prices commodities (`GC=F`), coins
(`BTC-USD`) and FX pairs (`EURSGD=X`) alike. It deliberately does NOT cache (see its
docstring), so the TTL cache lives here — a ticker polls, and an uncached fetch per poll per
open tab would hammer the provider for data that moves on a minutes timescale.
"""

from __future__ import annotations

import logging
import threading
import time

from backend.core.fx import base_currency

_log = logging.getLogger(__name__)

# 60s. The strip re-renders continuously but the DATA behind it is a spot quote; a shorter TTL
# buys nothing a viewer can perceive and costs a provider round-trip per open tab.
_TTL_SECONDS = 60.0

_lock = threading.Lock()
_cache: tuple[float, list[dict]] | None = None

# Yahoo symbols. Kept deliberately short: a ticker that scrolls for a minute before repeating
# is a screensaver, not a status bar.
_COMMODITIES: list[tuple[str, str]] = [
    ("GC=F", "Gold"),
    ("SI=F", "Silver"),
    ("CL=F", "Crude"),
    ("NG=F", "Nat Gas"),
]
_COINS: list[tuple[str, str]] = [
    ("BTC", "BTC"),
    ("ETH", "ETH"),
    ("SOL", "SOL"),
]
# A handful of large, liquid names so the strip reads as a market status bar rather than only
# a commodities/crypto board — the same reason a Bloomberg terminal ticker always carries a
# few household names alongside FX and futures. Kept short for the same reason as the
# commodities list above; MEGA-CAP only, so a symbol is very unlikely to ever be delisted.
# Label is the ticker itself, matching portfolio holdings and coins elsewhere in this strip —
# a company name here read as inconsistent with every other row.
_EQUITIES: list[tuple[str, str]] = [
    ("AAPL", "AAPL"),
    ("MSFT", "MSFT"),
    ("NVDA", "NVDA"),
    ("GOOGL", "GOOGL"),
    ("AMZN", "AMZN"),
]
# Quoted AGAINST the owner's base currency, so the numbers mean something to them. USD is
# skipped when it IS the base — "USD/USD 1.00" is noise.
_FX_COUNTERS: list[str] = ["USD", "EUR", "GBP", "JPY", "SGD", "VND"]


def _fx_symbol(counter: str, base: str) -> str:
    return f"{counter}{base}=X"


# Coins quote against USD, NOT the owner's base. Yahoo prices BTC-SGD but has no ETH-SGD or
# SOL-SGD, so basing them on the owner's currency made two of the three silently vanish from
# the strip (verified against the live provider, 2026-08-24). USD is both universally
# available and how these are conventionally quoted.
_COIN_QUOTE = "USD"


def _coin_symbol(coin: str) -> str:
    return f"{coin}-{_COIN_QUOTE}"


def _plan(base: str) -> list[tuple[str, str, str, str]]:
    """(yahoo_symbol, label, kind, unit) for everything the strip wants priced."""
    rows: list[tuple[str, str, str, str]] = []
    for sym, label in _COMMODITIES:
        rows.append((sym, label, "commodity", "USD"))
    for coin, label in _COINS:
        rows.append((_coin_symbol(coin), label, "coin", _COIN_QUOTE))
    # US-listed, so USD is correct here the same way it is for coins — these are not
    # converted to the owner's base for the same reason: a native quote, not a lie.
    for sym, label in _EQUITIES:
        rows.append((sym, label, "equity", "USD"))
    for counter in _FX_COUNTERS:
        if counter == base:
            continue
        rows.append((_fx_symbol(counter, base), f"{counter}/{base}", "fx", base))
    return rows


def _build(base: str) -> list[dict]:
    from backend.services import prices

    plan = _plan(base)
    quotes = prices.fetch_yahoo_quotes([sym for sym, _, _, _ in plan])
    got = quotes.get("prices") or {}
    prev = quotes.get("prev_prices") or {}
    states = quotes.get("states") or {}
    extended = quotes.get("extended") or {}

    out: list[dict] = []
    for sym, label, kind, unit in plan:
        # An unavailable symbol is OMITTED, never rendered as a zero or a stale number. A
        # ticker showing a wrong price is worse than a shorter ticker.
        if states.get(sym) == "unavailable":
            continue
        # A one-line strip has no room for the asset-detail page's two-block "at close +
        # separate pre/post line" layout — while a pre/post session is active, the row SHOWS
        # the extended quote directly via the same read chokepoint every other price display
        # in the app uses (owner ask 2026-08-31: a flat "0.00%" ahead of/after a US name's
        # regular session is worse than a live pre/post number), tagged with `session` so the
        # frontend can mark it with a distinct icon rather than let it read as an official
        # regular-session move.
        quote = prices.resolve_price_quote(sym, prices=got, extended=extended, prev_prices=prev)
        if quote is None or quote.price <= 0:
            continue
        row = {
            "symbol": sym,
            "label": label,
            "kind": kind,
            "unit": unit,
            "price": quote.price,
            "change_pct": quote.change_pct,
        }
        if quote.is_extended:
            row["session"] = quote.extended_session
        out.append(row)
    return out


def market_ticker_rows(*, force: bool = False) -> list[dict]:
    """Commodity / coin / FX rows for the ticker. Cached for `_TTL_SECONDS`.

    Never raises: a provider outage degrades the strip to whatever the frontend can render
    from holdings alone, which is the honest outcome — a status bar must not take a page down.
    """
    global _cache
    now = time.monotonic()
    with _lock:
        if not force and _cache is not None and (now - _cache[0]) < _TTL_SECONDS:
            return _cache[1]

    try:
        rows = _build(base_currency())
    except Exception:  # noqa: BLE001 - a status bar must never take the page down
        _log.warning("market ticker fetch failed", exc_info=True)
        with _lock:
            # Serve the last good rows if we have them; an empty list otherwise.
            return _cache[1] if _cache is not None else []

    with _lock:
        _cache = (time.monotonic(), rows)
    return rows
