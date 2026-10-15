"""watchlist_quotes — the Investments tab's Watchlist view (GET /api/watchlist/quotes).

A broker-style quote board: every TICKER on every named watchlist (held or not — unlike the Intel
pulse, which drops names you own), with its last price, today's change, volume, day / 52-week
range and market cap from ONE batched Yahoo quote (`fetch_quote_snapshots`, cached 3 min). A held
name also carries your position — quantity, today's base-currency P&L and unrealized P&L — from the
same invested-book fold the Holdings view uses (`investments_book.project_invested`), so the two
views can never disagree. Read-only.
"""

from __future__ import annotations

import logging
from datetime import datetime

from backend.services import watch_context
from backend.services import watch_rules as rules

logger = logging.getLogger(__name__)


def _ticker_items(watchlist: dict) -> list[dict]:
    """One entry per ticker; a ticker listed twice keeps the union of its watchlist ids."""
    by_key: dict[str, dict] = {}
    for item in watchlist.get("items") or []:
        if (item.get("type") or "ticker") != "ticker":
            continue
        key = (item.get("value") or "").strip().upper()
        if not key:
            continue
        seen = by_key.get(key)
        if seen is None:
            by_key[key] = {**item, "watchlist_ids": list(item.get("watchlist_ids") or [])}
            continue
        for wid in item.get("watchlist_ids") or []:
            if wid not in seen["watchlist_ids"]:
                seen["watchlist_ids"].append(wid)
    return list(by_key.values())


def _positions_by_ticker() -> dict[str, dict]:
    """ticker (and ``iid:<instrument_id>``) → {quantity, value, day_pl, unrealized} in base
    currency, legs across accounts summed. `unrealized` is None when any leg has no cost basis
    (a partial sum would understate it)."""
    from backend.services import investments_book
    from backend.services import positions as positions_service
    from backend.services.fx_cache import load_fx_cache

    projected, _grand = investments_book.project_invested(
        positions_service.hydrated(), rates=load_fx_cache()
    )
    out: dict[str, dict] = {}
    iids: dict[str, str] = {}
    for p in projected:
        key = (p.get("ticker") or p.get("symbol") or "").strip().upper()
        qty = float(p.get("quantity") or 0)
        if not key or qty <= 0 or p.get("vesting_state") == "unvested":
            continue
        if p.get("instrument_id"):
            iids[f"iid:{p['instrument_id']}"] = key
        pos = out.setdefault(key, {"quantity": 0.0, "value": 0.0, "day_pl": 0.0, "unrealized": 0.0})
        pos["quantity"] += qty
        pos["value"] += p.get("base_value") or 0.0
        pos["day_pl"] += p.get("daily_pl") or 0.0
        pos["unrealized"] = (
            None
            if pos["unrealized"] is None or p.get("pl") is None
            else pos["unrealized"] + p["pl"]
        )
    rounded = {
        k: {kk: (round(v, 6) if kk == "quantity" else _r(v)) for kk, v in pos.items()}
        for k, pos in out.items()
    }
    return {**rounded, **{alias: rounded[key] for alias, key in iids.items()}}


def _position_for(item: dict, positions: dict[str, dict]) -> dict | None:
    iid = item.get("instrument_id")
    hit = positions.get(f"iid:{iid}") if iid else None
    return hit or positions.get(item["value"].strip().upper())


def _r(v: float | None) -> float | None:
    return round(v, 2) if v is not None else None


SPARK_POINTS = 30  # ~six weeks of trading days — the row's sparkline


def _spark(item: dict, snap: dict, insts: dict, now: datetime) -> list[float] | None:
    """The last SPARK_POINTS settled closes ending on today's price, from the stored history
    (no network); None when fewer than two points are known."""
    pid = watch_context.price_source_id_for(item, insts)
    closes = [
        c
        for _, c in watch_context.settled(
            watch_context.history_for(pid, now), snap.get("session_date")
        )
    ]
    if snap.get("price"):
        closes.append(snap["price"])
    closes = closes[-SPARK_POINTS:]
    return [round(c, 6) for c in closes] if len(closes) >= 2 else None


def _row(
    item: dict, snap: dict | None, position: dict | None, spark: list[float] | None = None
) -> dict:
    key = item["value"].strip().upper()
    snap = snap or {}
    price, prev = snap.get("price"), snap.get("prev_close")
    change = price - prev if price is not None and prev else None
    pct = rules.day_pct(snap) if snap else None
    vol, avg = snap.get("volume"), snap.get("avg_volume")
    return {
        "ticker": key,
        "name": item.get("label") or snap.get("name") or key,
        "instrument_id": item.get("instrument_id") or None,
        "asset_class": item.get("asset_class") or "equity",
        "watchlist_ids": item.get("watchlist_ids") or [],
        "exchange": snap.get("exchange"),
        "currency": snap.get("currency"),
        "market_state": snap.get("market_state"),
        "price": price,
        "prev_close": prev,
        "change": round(change, 6) if change is not None else None,
        "change_pct": round(pct, 4) if pct is not None else None,
        "open": snap.get("open"),
        "day_low": snap.get("day_low"),
        "day_high": snap.get("day_high"),
        "volume": vol,
        "avg_volume": avg,
        "volume_ratio": round(vol / avg, 2) if vol and avg else None,
        "market_cap": snap.get("market_cap"),
        "week52_low": snap.get("week52_low"),
        "week52_high": snap.get("week52_high"),
        "range_pos": rules.range_position(snap) if snap else None,
        "trailing_pe": snap.get("trailing_pe"),
        "earnings_at": snap.get("earnings_at"),
        "position": position,
        "spark": spark,
    }


def build_watchlist_quotes(now: datetime | None = None) -> dict:
    """``{generated_at, watchlists, rows}`` — rows in watchlist order; a ticker with no quote is
    still listed (its market fields null) so a list never silently loses a name."""
    from backend.services.prices import fetch_quote_snapshots
    from backend.services.watchlist import read_watchlist

    now = now or datetime.now().astimezone()
    wl = read_watchlist()
    watchlists = sorted(wl.get("watchlists") or [], key=lambda w: w.get("order") or 0)
    items = _ticker_items(wl)
    out: dict = {"generated_at": now.isoformat(), "watchlists": watchlists, "rows": []}
    if not items:
        return out
    insts = watch_context.instruments_for(items)
    quotes = {i["value"].strip().upper(): watch_context.quote_symbol_for(i, insts) for i in items}
    snaps = fetch_quote_snapshots(list(quotes.values()))
    try:
        positions = _positions_by_ticker()
    except Exception:
        logger.warning("watchlist_quotes: position fold failed", exc_info=True)
        positions = {}
    rows: list[dict] = []
    missing: list[dict] = []
    for i in items:
        snap = snaps.get(quotes[i["value"].strip().upper()])
        spark = _spark(i, snap or {}, insts, now)
        if spark is None:
            missing.append(i)
        rows.append(_row(i, snap, _position_for(i, positions), spark))
    if missing:
        # No stored closes yet (a just-added name): fetch in the background for the next poll.
        from backend.services import watch_history

        watch_history.request(missing)
    out["rows"] = rows
    return out
