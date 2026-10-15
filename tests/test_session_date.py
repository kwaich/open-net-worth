"""
Tests for market-calendar awareness in the price history and day-change calculation.

The day-change anchor (prior close) is computed via PriceHistoryStore.prior_to_map(),
which defaults to today's calendar date.  On a non-trading day (weekend / holiday) the
"current" price (Friday's close, just re-fetched from Yahoo) and the prior close (also
Friday's close, the most recent recorded before today) are the same → day_change ≈ 0.

Tests:
  - PriceHistoryStore.latest_date() returns the most recent date across all series.
  - latest_date() returns None on an empty store.
  - Day-change is 0 on a non-trading day (current == prior-before-today).
  - Day-change is non-zero on an actual trading day.

Run:
    .venv/bin/python3 -m pytest tests/test_session_date.py -q
"""

from __future__ import annotations

from backend.db.price_history_store import PriceHistoryStore

# ── PriceHistoryStore.latest_date() ───────────────────────────────────────────


def test_latest_date_empty(db_isolated):
    """Empty store → latest_date() is None."""
    assert PriceHistoryStore().latest_date() is None


def test_latest_date_single_series(db_isolated):
    """Single instrument with two points → returns the newer date."""
    store = PriceHistoryStore()
    store.record("AAPL", 200.0, day="2026-06-17", source="yahoo")
    store.record("AAPL", 202.0, day="2026-06-18", source="yahoo")
    assert store.latest_date() == "2026-06-18"


def test_latest_date_multi_series_takes_max(db_isolated):
    """Multiple instruments with different latest dates → returns the most recent."""
    store = PriceHistoryStore()
    store.record("AAPL", 200.0, day="2026-06-17", source="yahoo")
    store.record("MSFT", 450.0, day="2026-06-20", source="yahoo")
    store.record("BTC", 65000.0, day="2026-06-19", source="coingecko")
    assert store.latest_date() == "2026-06-20"


def test_latest_date_weekend_scenario(db_isolated):
    """Prices last recorded on Friday (2026-06-19) → session_date is Friday,
    not the weekend date. This is the core market-calendar awareness check."""
    store = PriceHistoryStore()
    # Friday 2026-06-19 is the last trading day before the weekend
    store.record("SPY", 550.0, day="2026-06-18", source="yahoo")
    store.record("SPY", 552.0, day="2026-06-19", source="yahoo")
    store.record("AAPL", 200.0, day="2026-06-18", source="yahoo")
    store.record("AAPL", 201.5, day="2026-06-19", source="yahoo")
    assert store.latest_date() == "2026-06-19"  # Friday, not the Saturday/Sunday caller runs on


# ── Day-change anchor: prior_to_map(day=today) ───────────────────────────────


def test_day_change_zero_on_non_trading_day(db_isolated):
    """On a weekend the prior close before today == the latest close (both Friday).

    prior_to_map(day='2026-06-21') returns Friday 2026-06-19.
    latest_map() also returns Friday 2026-06-19.
    current_price − prior = 0, so day_change is 0 for every instrument.
    """
    store = PriceHistoryStore()
    store.record("SPY", 548.0, day="2026-06-18", source="yahoo")  # Thu
    store.record("SPY", 552.0, day="2026-06-19", source="yahoo")  # Fri
    store.record("AAPL", 198.0, day="2026-06-18", source="yahoo")  # Thu
    store.record("AAPL", 201.5, day="2026-06-19", source="yahoo")  # Fri

    today_sun = "2026-06-21"  # Sunday — a non-trading day
    latest = store.latest_map()
    prior = store.prior_to_map(day=today_sun)

    # Both resolve to Friday's close: latest == prior → day_change = 0
    assert latest["SPY"] == prior["SPY"], "SPY: latest and prior should both be Friday's close"
    assert latest["AAPL"] == prior["AAPL"], "AAPL: latest and prior should both be Friday's close"
    assert latest["SPY"] == 552.0
    assert prior["SPY"] == 552.0


def test_day_change_nonzero_on_trading_day(db_isolated):
    """On a real trading day the prior close (Wednesday) differs from the latest (Thursday).

    prior_to_map(day='2026-06-18') returns Wednesday 2026-06-17.
    latest_map() returns Thursday 2026-06-18 (the just-recorded close).
    """
    store = PriceHistoryStore()
    store.record("SPY", 548.0, day="2026-06-17", source="yahoo")  # Wed
    store.record("SPY", 552.0, day="2026-06-18", source="yahoo")  # Thu

    today_thu = "2026-06-18"  # Thursday — a real trading day
    latest = store.latest_map()
    prior = store.prior_to_map(day=today_thu)

    # Latest = Thursday; prior = Wednesday → non-zero move
    assert latest["SPY"] == 552.0
    assert prior["SPY"] == 548.0
    day_change_pct = (latest["SPY"] / prior["SPY"] - 1) * 100
    assert abs(day_change_pct - (552.0 / 548.0 - 1) * 100) < 0.001
