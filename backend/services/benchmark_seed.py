"""
benchmark_seed — idempotent startup seeding of the benchmark-roster instruments.

The benchmark roster (SPY/VWRA/QQQ/ACWI/BTC) is canonical in risk_metrics.BENCHMARK_ROSTER
but the resolver relies on InstrumentStore entries with matching symbols — they are never
auto-created by the price-fetch path.  This module creates the five entries on first boot and
is a no-op on every subsequent boot (find_or_create dedupes on price_source × price_source_id).

Called from main.py lifespan in a best-effort try/except (same pattern as skill seeding).
Price history is backfilled afterwards via the standard backfill_history() mechanism.

Layer: services → db only (no api/ai import).
"""

from __future__ import annotations

import logging

_log = logging.getLogger(__name__)

# Roster definition.  Keys match BENCHMARK_ROSTER in risk_metrics.py.
# price_source_id is the provider-side identifier:
#   yahoo  → the Yahoo Finance ticker (e.g. "VWRA.L" for the LSE-listed share class)
#   coingecko → the CoinGecko coin id (e.g. "bitcoin")
# symbol is the human-facing display ticker that _resolve_benchmark_instrument uses for
# find_by_symbol — must match the roster keys in risk_metrics.BENCHMARK_ROSTER exactly.
_ROSTER: list[dict] = [
    {
        "symbol": "SPY",
        "name": "SPDR S&P 500 ETF Trust",
        "asset_class": "equity",
        "asset_subclass": "etf",
        "quote_currency": "USD",
        "price_source": "yahoo",
        "price_source_id": "SPY",
    },
    {
        "symbol": "VWRA",
        "name": "Vanguard FTSE All-World UCITS ETF (USD, Acc)",
        "asset_class": "equity",
        "asset_subclass": "etf",
        "quote_currency": "USD",
        # LSE-listed accumulating share class; Yahoo Finance uses the .L suffix.
        "price_source": "yahoo",
        "price_source_id": "VWRA.L",
    },
    {
        "symbol": "QQQ",
        "name": "Invesco QQQ Trust (Nasdaq-100)",
        "asset_class": "equity",
        "asset_subclass": "etf",
        "quote_currency": "USD",
        "price_source": "yahoo",
        "price_source_id": "QQQ",
    },
    {
        "symbol": "ACWI",
        "name": "iShares MSCI ACWI ETF",
        "asset_class": "equity",
        "asset_subclass": "etf",
        "quote_currency": "USD",
        "price_source": "yahoo",
        "price_source_id": "ACWI",
    },
    {
        "symbol": "BTC",
        "name": "Bitcoin",
        "asset_class": "crypto",
        "asset_subclass": "coin",
        "quote_currency": "USD",
        # CoinGecko is the project-standard source for crypto price history.
        # "bitcoin" is the canonical CoinGecko coin id (same as _COINGECKO_SYMBOL_MAP in
        # prices.py which maps "BTC-USD" → "bitcoin").
        "price_source": "coingecko",
        "price_source_id": "bitcoin",
    },
]


_SECTOR_FUND_NAMES = {
    "XLB": "Materials Select Sector SPDR Fund",
    "XLC": "Communication Services Select Sector SPDR Fund",
    "XLE": "Energy Select Sector SPDR Fund",
    "XLF": "Financial Select Sector SPDR Fund",
    "XLI": "Industrial Select Sector SPDR Fund",
    "XLK": "Technology Select Sector SPDR Fund",
    "XLP": "Consumer Staples Select Sector SPDR Fund",
    "XLRE": "Real Estate Select Sector SPDR Fund",
    "XLU": "Utilities Select Sector SPDR Fund",
    "XLV": "Health Care Select Sector SPDR Fund",
    "XLY": "Consumer Discretionary Select Sector SPDR Fund",
}


def _sector_funds() -> list[dict]:
    """The SPDR sector funds the Watching pulse compares a stock with (``watch_context``'s
    sector map) — seeded and backfilled like the roster so their trend lines have history.
    Not benchmarks: they never appear in the benchmark picker."""
    from backend.services.watch_context import SECTOR_ETF

    return [
        {
            "symbol": etf,
            "name": _SECTOR_FUND_NAMES.get(etf, f"{sector} Select Sector SPDR Fund"),
            "asset_class": "equity",
            "asset_subclass": "etf",
            "quote_currency": "USD",
            "price_source": "yahoo",
            "price_source_id": etf,
        }
        for sector, etf in sorted(SECTOR_ETF.items(), key=lambda kv: kv[1])
    ]


def _specs() -> list[dict]:
    return [*_ROSTER, *_sector_funds()]


def reference_price_ids() -> list[str]:
    """price_source_ids of every benchmark and sector fund — the comparison lines on the pulse,
    topped up with the watched names so they stay current between restarts."""
    return [s["price_source_id"] for s in _specs()]


def seed_benchmark_instruments() -> dict[str, int]:
    """Idempotently ensure all benchmark roster instruments (and the sector funds) exist in
    InstrumentStore.

    Returns {"created": N, "skipped": N} where skipped = already-present entries.
    Never raises — exceptions from the store are logged and propagated so the caller's
    try/except can handle them gracefully.
    """
    from backend.db.instrument_store import InstrumentStore

    store = InstrumentStore()
    created = 0
    skipped = 0
    for spec in _specs():
        _, was_created = store.find_or_create(spec)
        if was_created:
            created += 1
            _log.info(
                "benchmark_seed: created instrument %s (%s:%s)",
                spec["symbol"],
                spec["price_source"],
                spec["price_source_id"],
            )
        else:
            skipped += 1
    return {"created": created, "skipped": skipped}


def backfill_benchmark_prices() -> dict[str, int]:
    """Trigger price-history backfill for every roster instrument via the standard mechanism.

    Uses backfill_history() (the same function used by backfill_held_instruments) so the
    idempotent upsert-skip behaviour is inherited — a re-run adds zero new points.
    Returns {price_source_id: points_added}.  Individual failures are logged and skipped;
    the returned dict omits entries that errored.
    """
    from backend.services.prices import backfill_history

    results: dict[str, int] = {}
    for spec in _specs():
        pid = spec["price_source_id"]
        try:
            added = backfill_history(pid)
            results[pid] = added
        except Exception:
            _log.warning("benchmark_seed: backfill failed for %s", pid, exc_info=True)
    return results
