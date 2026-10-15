"""watch_context — the per-ticker inputs the watch rules read, beyond the live quote.

* **History** — dated daily closes from ``price_points`` (a LOCAL read, no network: the backfill
  sweep in ``prices.backfill_tracked_instruments`` fills it for held AND watched instruments),
  for the ticker and for the user's benchmark.
* **Sector** — the ticker's sector mapped to its SPDR sector ETF, whose day move rides in the same
  batched quote, so "moving with its sector" can be told apart from "company-specific".
* **Intent** — why the user watches it (``watch_intent``).

``evaluate_item`` is the one place a ticker's findings are computed, for both the watched pulse
(``watch_pulse``), the feed stories (``watch_signals``) and the explained alerts
(``monitoring.watch_analyst``) — so the three can never disagree.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

from backend.services import watch_rules as rules
from backend.services.watch_intent import thesis_of
from backend.services.watch_market import volume_pace
from backend.services.watch_stats import history_stats

logger = logging.getLogger(__name__)

HISTORY_DAYS = 400  # enough closes for a 200-day average plus the lookback

# Yahoo sector names → the SPDR sector ETF that tracks them.
SECTOR_ETF: dict[str, str] = {
    "Technology": "XLK",
    "Healthcare": "XLV",
    "Financial Services": "XLF",
    "Consumer Cyclical": "XLY",
    "Consumer Defensive": "XLP",
    "Energy": "XLE",
    "Industrials": "XLI",
    "Basic Materials": "XLB",
    "Real Estate": "XLRE",
    "Utilities": "XLU",
    "Communication Services": "XLC",
}


def instruments_for(items: list[dict]) -> dict[str, dict]:
    """instrument_id → instrument for the items that have one — ONE store, read once per poll."""
    from backend.db.instrument_store import InstrumentStore

    ids = {i["instrument_id"] for i in items if i.get("instrument_id")}
    if not ids:
        return {}
    store = InstrumentStore()
    return {iid: store.find_by_id(iid) or {} for iid in ids}


def _inst(item: dict, insts: dict[str, dict] | None) -> dict:
    iid = item.get("instrument_id")
    if not iid:
        return {}
    if insts is not None:
        return insts.get(iid) or {}
    return instruments_for([item]).get(iid) or {}


def price_source_id_for(item: dict, insts: dict[str, dict] | None = None) -> str:
    """The key price history is stored under: the instrument's own id, else the ticker text."""
    inst = _inst(item, insts)
    return inst.get("price_source_id") or (item.get("value") or "").strip()


def yahoo_symbol(symbol: str, asset_class: str | None, inst: dict | None = None) -> str:
    """The Yahoo symbol that quotes ``symbol``: its instrument's Yahoo listing, else ``<SYM>-USD``
    for a coin (a bare ``BTC`` / ``ETH`` on Yahoo is a US-listed trust, not the coin), else the
    symbol itself."""
    inst = inst or {}
    if (inst.get("price_source") or "").lower() == "yahoo" and inst.get("price_source_id"):
        return inst["price_source_id"]
    sym = (symbol or "").strip()
    crypto = (asset_class or inst.get("asset_class") or "").lower() == "crypto"
    return f"{sym.upper()}-USD" if crypto and "-" not in sym else sym


def quote_symbol_for(item: dict, insts: dict[str, dict] | None = None) -> str:
    return yahoo_symbol(item.get("value") or "", item.get("asset_class"), _inst(item, insts))


def sector_for(
    item: dict, insts: dict[str, dict] | None = None, cache: dict[str, str] | None = None
) -> str | None:
    """The item's sector: its instrument's, else the Yahoo sectors cache."""
    from backend.services.prices import load_sectors_cache

    sector = _inst(item, insts).get("sector")
    if sector:
        return sector
    cache = load_sectors_cache() if cache is None else cache
    return cache.get((item.get("value") or "").strip()) or None


def history_for(price_source_id: str, now: datetime) -> list[tuple[str, float]]:
    """Dated closes BEFORE today (today's move comes from the live quote)."""
    from backend.db.price_history_store import PriceHistoryStore

    if not price_source_id:
        return []
    today = now.date().isoformat()
    since = (now - timedelta(days=HISTORY_DAYS)).date().isoformat()
    try:
        points = PriceHistoryStore().series(price_source_id, since=since)
    except Exception:
        logger.debug("watch_context: history unavailable for %s", price_source_id, exc_info=True)
        return []
    return [(p["date"], p["price"]) for p in points if p["date"] < today and p.get("price")]


def settled(series: list[tuple[str, float]], session_date: str | None):
    """Closes dated before the quote's session — a bar ON that day is the move the live quote
    already reports (a user east of the exchange is a calendar day ahead of it; on a weekend the
    quote is still Friday's), so counting it would count today twice."""
    if not session_date:
        return series
    return [(d, c) for d, c in series if d < session_date]


def benchmark_history(symbol: str, now: datetime) -> list[tuple[str, float]]:
    """Closes for a reference fund by its display symbol — the benchmark, or a sector fund
    (both seeded as instruments by ``benchmark_seed``)."""
    from backend.db.instrument_store import InstrumentStore

    inst = InstrumentStore().find_by_symbol(symbol) or {}
    return history_for(inst.get("price_source_id") or symbol, now)


def fill_market_context(
    ctx: dict,
    snaps: dict[str, dict],
    quotes: dict[str, str],
    bench: tuple[str, str],
    etfs: list[str],
) -> None:
    """Split one batched quote into each ticker's snapshot (``quotes``: ticker → the Yahoo symbol
    it was quoted as), the benchmark's move and each sector fund's move. A symbol that is also a
    tracked ticker still feeds both."""
    upper = {k.upper(): v for k, v in snaps.items() if v is not None}
    bench_key, bench_symbol = bench
    b = upper.get(bench_symbol.upper())
    if b is not None:
        ctx["bench"] = {"symbol": bench_key, "day_pct": rules.day_pct(b), "price": b.get("price")}
        ctx["bench_session"] = b.get("session_date")
    ctx["sector_moves"] = {e: rules.day_pct(upper[e]) for e in etfs if e in upper}
    ctx["sector_prices"] = {e: upper[e].get("price") for e in etfs if e in upper}
    ctx["sector_sessions"] = {e: upper[e].get("session_date") for e in etfs if e in upper}
    ctx["snaps"] = {t: upper[q.upper()] for t, q in quotes.items() if q.upper() in upper}


def sector_etfs(sectors: dict[str, str | None]) -> dict[str, str]:
    """ticker → sector ETF symbol, for the tickers whose sector maps to one."""
    return {t: SECTOR_ETF[s] for t, s in sectors.items() if s and s in SECTOR_ETF}


def attach(ctx: dict, *, pids: dict[str, str], bench_key: str | None) -> None:
    """Load history for every ticker in ``pids`` (ticker → price_source_id), the benchmark, and
    the sector funds in ``ctx["sector_etf"]`` (the watched trend line's comparison)."""
    now = ctx["now"]
    sessions = ctx.get("sector_sessions") or {}
    ctx["history"] = {t: history_for(pid, now) for t, pid in pids.items()}
    bench = benchmark_history(bench_key, now) if bench_key else []
    ctx["bench_series"] = settled(bench, ctx.get("bench_session"))
    etfs = sorted(set((ctx.get("sector_etf") or {}).values()))
    ctx["sector_series"] = {e: settled(benchmark_history(e, now), sessions.get(e)) for e in etfs}


def _stats_for(key: str, snap: dict, ctx: dict) -> tuple[dict | None, dict | None]:
    d = rules.day_pct(snap)
    etf = (ctx.get("sector_etf") or {}).get(key)
    sector_day = (ctx.get("sector_moves") or {}).get(etf) if etf else None
    bench = ctx.get("bench") or {}
    stats = history_stats(
        settled((ctx.get("history") or {}).get(key, []), snap.get("session_date")),
        price=snap.get("price"),
        day_pct=d,
        bench_series=ctx.get("bench_series"),
        bench_day=bench.get("day_pct"),
        bench_price=bench.get("price"),
        sector_day=sector_day,
    )
    sector = {"symbol": etf, "day_pct": sector_day} if etf and sector_day is not None else None
    return stats, sector


def evaluate_item(item: dict, ctx: dict) -> tuple[dict, list[dict]] | None:
    """(snapshot, findings) for one ticker, or None when no quote resolved for it."""
    key = item["value"].strip().upper()
    snap = ctx["snaps"].get(key)
    if snap is None:
        return None
    equity = (item.get("asset_class") or "equity").lower() == "equity"
    stats, sector = _stats_for(key, snap, ctx)
    ctx.setdefault("stats", {})[key] = stats
    findings = rules.evaluate(
        snap,
        asset_class=item.get("asset_class"),
        alerts=ctx["alerts"].get(key, []),
        bench=ctx["bench"] if equity else None,
        now=ctx["now"],
        stats=stats,
        intent=item.get("intent"),
        thesis=thesis_of(item),
        sector=sector if equity else None,
    )
    return snap, findings


def pace_for(key: str, ctx: dict) -> float | None:
    snap = ctx["snaps"].get(key)
    return volume_pace(snap, ctx["now"]) if snap else None
