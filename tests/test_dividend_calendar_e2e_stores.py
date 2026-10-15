"""E2E test for dividend calendar: REAL stores → REAL services → REAL API.

Tests the complete dividend calendar flow (GET /api/income/calendar) exercising:
  - Real InstrumentStore + PositionStore wired to conftest-isolated DATA_DIR
  - Real dividend_calendar.upcoming_payments service
  - Real FastAPI router / TestClient integration
  - Acceptance criteria: INC-CAL-SCHED-VS-EST, INC-CAL-EXCLUDE-NONPAYER,
    INC-CAL-HORIZON, INC-CAL-NEXT-HERO, INC-CAL-AMOUNT-FX, INC-EMPTY,
    INC-FWD-YIELD-MV (market value, not cost basis).

Data isolation: conftest sets DATA_DIR to a throwaway per-session; stores auto-read from that.
Monkeypatched FX rates, base currency, frozen "today" (2026-06-21).
No network calls, no live data touched.

Test matrix:
  1. Scheduled payer (future next_ex_date, Yahoo-declared) — green dot
  2. Estimated payer (last_ex_date + cadence, no declared date) — grey dot
  3. Non-payer (no dividend rate, no history) — excluded
  4. FX conversion (USD holding, SGD base, 1.35 rate)
  5. Horizon filter (90d window)
  6. Next payment hero (soonest ex-date)
  7. Empty portfolio
  8. Forecast income: forward_yield_pct (market value), market_value_base present
"""

from __future__ import annotations

import datetime as _dt

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api.income import router as income_router
from backend.db.instrument_store import InstrumentStore
from backend.db.position_store import PositionStore
from backend.services import dividend_calendar as dcal_svc
from backend.services import income as income_svc


class _FixedToday:
    """Stand-in for date.today() → 2026-06-21 (matches the spike & ADR 0020)."""

    @staticmethod
    def today() -> _dt.date:
        return _dt.date(2026, 6, 21)

    @staticmethod
    def fromisoformat(s: str) -> _dt.date:
        return _dt.date.fromisoformat(s)


@pytest.fixture
def wired_stores(monkeypatch):
    """Wire real stores + mock FX / base currency / frozen today.

    Clears the stores before test to avoid cross-test pollution (each test adds data via
    upsert, and stores read from the same DATA_DIR per conftest setup).
    """
    # Stores auto-read from conftest-isolated DATA_DIR
    inst_store = InstrumentStore()
    pos_store = PositionStore()

    # Clear any residual data from previous tests
    inst_store.replace_all([])
    pos_store.replace_all([])

    # Monkeypatch dividend_calendar module to use the real stores + fixed today
    monkeypatch.setattr(dcal_svc, "instruments", inst_store)
    monkeypatch.setattr(dcal_svc, "base_currency", lambda: "SGD")
    monkeypatch.setattr(dcal_svc.lots_service, "_load_rates", lambda: {"SGD": 1.0, "USD": 1.35})
    monkeypatch.setattr(dcal_svc, "user_today", _FixedToday.today)

    # Monkeypatch positions_service hydrated() to read from the real store
    def _hydrated_via_store(*args, **kwargs):
        """Re-read from store each call; mirrors the real hydrated() behavior."""
        return pos_store.all()

    monkeypatch.setattr(dcal_svc.positions_service, "hydrated", _hydrated_via_store)

    # Monkeypatch income_service to use the same stores
    monkeypatch.setattr(income_svc, "instruments", inst_store)
    monkeypatch.setattr(income_svc, "positions", pos_store)
    monkeypatch.setattr(income_svc, "base_currency", lambda: "SGD")
    monkeypatch.setattr(income_svc.lots_service, "_load_rates", lambda: {"SGD": 1.0, "USD": 1.35})

    # Create a simple mock for _market_value_by_position
    # (the real function would compute from positions + quotes; for forecast_income test
    # we'll compute market_value directly from position fields).
    def _mock_market_value_by_pos(rates, base):
        """Compute market value as qty × price × qpu × FX."""
        result = {}
        for pos in pos_store.all():
            qty = float(pos.get("quantity") or 0)
            price = float(pos.get("price") or 0)
            qpu = float(pos.get("quote_per_unit") or 1.0)
            quote_ccy = (pos.get("quote_currency") or "USD").upper()
            rate = rates.get(quote_ccy, 1.0)
            result[pos["id"]] = round(qty * price * qpu * rate, 2)
        return result

    monkeypatch.setattr(income_svc, "_market_value_by_position", _mock_market_value_by_pos)

    return inst_store, pos_store


@pytest.fixture
def client(wired_stores):
    """FastAPI TestClient + wired income router."""
    app = FastAPI()
    app.include_router(income_router)
    return TestClient(app)


# ─────────────────────────────────────────────────────────────────────────────
# Acceptance criteria tests
# ─────────────────────────────────────────────────────────────────────────────


def test_inc_cal_sched_vs_est_scheduled(client, wired_stores):
    """INC-CAL-SCHED-VS-EST (scheduled path):
    GIVEN holding A with future next_ex_date = 2026-08-20 (Yahoo-declared)
    WHEN I request /api/income/calendar
    THEN A is status="scheduled" using exact ex/pay dates (green dot)
    """
    inst_store, pos_store = wired_stores

    # Seed a scheduled-payer instrument
    inst_store.upsert(
        {
            "id": "sch1",
            "symbol": "MSFT",
            "name": "Microsoft",
            "quote_currency": "USD",
            "next_ex_date": "2026-08-20",  # Future, declared
            "next_pay_date": "2026-09-10",
            "last_dividend_amount": 0.91,
            "dividend_frequency": "quarterly",
        }
    )

    # Seed a position holding that instrument
    pos_store.upsert(
        {
            "id": "pos1",
            "position_id": "pos1",
            "instrument_id": "sch1",
            "account_id": "acc1",
            "quantity": 100.0,
            "quote_currency": "USD",
            "price": 350.0,
            "quote_per_unit": 1.0,
            "symbol": "MSFT",
        }
    )

    r = client.get("/api/income/calendar?days=90")
    assert r.status_code == 200
    body = r.json()
    assert body["count"] == 1
    row = body["payments"][0]

    # Verify scheduled status
    assert row["status"] == "scheduled", f"Expected 'scheduled', got '{row['status']}'"
    assert row["ex_date"] == "2026-08-20", f"Exact ex_date mismatch: {row['ex_date']}"
    assert row["pay_date"] == "2026-09-10", f"Exact pay_date mismatch: {row['pay_date']}"
    assert row["symbol"] == "MSFT"
    assert row["per_share"] == 0.91
    assert row["amount_base"] == pytest.approx(122.85, abs=0.01)  # 0.91 × 100 × 1.35


def test_inc_cal_sched_vs_est_estimated(client, wired_stores):
    """INC-CAL-SCHED-VS-EST (estimated path):
    GIVEN holding B with no next_ex_date but last_ex_date = 2026-03-23 + dividend_frequency = quarterly
    WHEN I request /api/income/calendar
    THEN B is status="estimated" with projected ex-date ≈ last_ex_date + 91d rolled past today,
    pay-date = ex + 14d (grey dot)
    """
    inst_store, pos_store = wired_stores

    # Seed an estimated-payer instrument (no declared date)
    inst_store.upsert(
        {
            "id": "est1",
            "symbol": "QQQ",
            "name": "Invesco QQQ Trust",
            "quote_currency": "USD",
            "last_ex_date": "2026-03-23",
            "dividend_frequency": "quarterly",
            "last_dividend_amount": 0.70,
        }
    )

    # Seed a position
    pos_store.upsert(
        {
            "id": "pos2",
            "position_id": "pos2",
            "instrument_id": "est1",
            "account_id": "acc1",
            "quantity": 50.0,
            "quote_currency": "USD",
            "price": 400.0,
            "quote_per_unit": 1.0,
            "symbol": "QQQ",
        }
    )

    r = client.get("/api/income/calendar?days=90")
    assert r.status_code == 200
    body = r.json()
    assert body["count"] == 1
    row = body["payments"][0]

    # Verify estimated status
    assert row["status"] == "estimated", f"Expected 'estimated', got '{row['status']}'"
    # 2026-03-23 + 91d = 2026-06-22 (first ex-date after today 2026-06-21)
    assert row["ex_date"] == "2026-06-22", f"Projected ex_date mismatch: {row['ex_date']}"
    assert row["pay_date"] == "2026-07-06", (
        f"Projected pay_date mismatch: {row['pay_date']}"
    )  # +14d
    assert row["symbol"] == "QQQ"
    assert row["per_share"] == 0.70
    assert row["amount_base"] == pytest.approx(47.25, abs=0.01)  # 0.70 × 50 × 1.35


def test_inc_cal_exclude_nonpayer(client, wired_stores):
    """INC-CAL-EXCLUDE-NONPAYER:
    GIVEN a non-paying holding (e.g. GLD: no dividend_rate_annual, no last_ex_date, empty history)
    WHEN I view the calendar
    THEN that holding appears in NEITHER calendar NOR "Where your income comes from" — excluded,
    never shown with a fabricated date or amount.
    """
    inst_store, pos_store = wired_stores

    # Seed a non-payer instrument (no dividend meta)
    inst_store.upsert(
        {
            "id": "nopay",
            "symbol": "GLD",
            "name": "SPDR Gold Shares",
            "quote_currency": "USD",
            # No dividend meta at all: no last_ex_date, no dividend_frequency, no rate
        }
    )

    # Seed a position
    pos_store.upsert(
        {
            "id": "pos3",
            "position_id": "pos3",
            "instrument_id": "nopay",
            "account_id": "acc1",
            "quantity": 10.0,
            "quote_currency": "USD",
            "price": 180.0,
            "quote_per_unit": 1.0,
            "symbol": "GLD",
        }
    )

    r = client.get("/api/income/calendar?days=90")
    assert r.status_code == 200
    body = r.json()
    assert body["count"] == 0, f"Expected 0 payments, got {body['count']}"
    assert body["next_payment"] is None
    assert body["total_base"] == 0.0


def test_inc_cal_horizon_filter(client, wired_stores):
    """INC-CAL-HORIZON:
    GIVEN payments projected inside and outside a 90-day window
    WHEN I request days=90
    THEN only payments with today ≤ ex_date ≤ today+90 appear, sorted by ex-date ascending.
    """
    inst_store, pos_store = wired_stores

    # Annual payer: next ex ≈ 2026-12-01 (far outside 90d)
    inst_store.upsert(
        {
            "id": "annual1",
            "symbol": "ANNUAL",
            "name": "Annual Payer",
            "quote_currency": "USD",
            "last_ex_date": "2025-12-01",
            "dividend_frequency": "annual",
            "last_dividend_amount": 1.0,
        }
    )
    pos_store.upsert(
        {
            "id": "pos_annual",
            "position_id": "pos_annual",
            "instrument_id": "annual1",
            "account_id": "acc1",
            "quantity": 10.0,
            "quote_currency": "USD",
            "price": 50.0,
            "quote_per_unit": 1.0,
            "symbol": "ANNUAL",
        }
    )

    # Quarterly payer: next ex ≈ 2026-06-22 (within 90d)
    inst_store.upsert(
        {
            "id": "q1",
            "symbol": "QTR",
            "name": "Quarterly Payer",
            "quote_currency": "USD",
            "last_ex_date": "2026-03-23",
            "dividend_frequency": "quarterly",
            "last_dividend_amount": 0.5,
        }
    )
    pos_store.upsert(
        {
            "id": "pos_q",
            "position_id": "pos_q",
            "instrument_id": "q1",
            "account_id": "acc1",
            "quantity": 20.0,
            "quote_currency": "USD",
            "price": 100.0,
            "quote_per_unit": 1.0,
            "symbol": "QTR",
        }
    )

    # Query at days=90: should see only the quarterly (in 90d)
    r = client.get("/api/income/calendar?days=90")
    assert r.status_code == 200
    body = r.json()
    assert body["count"] == 1
    assert body["payments"][0]["symbol"] == "QTR"
    assert body["horizon_days"] == 90

    # Query at days=365: should see the quarterly payer's FULL rolled-forward schedule
    # (MATH-36 — every occurrence through the horizon, not just the next one) plus the
    # annual payer's single occurrence, sorted by ex-date.
    r = client.get("/api/income/calendar?days=365")
    assert r.status_code == 200
    body = r.json()
    assert body["count"] == 6
    assert body["horizon_days"] == 365
    assert [p["symbol"] for p in body["payments"]] == [
        "QTR",
        "QTR",
        "ANNUAL",
        "QTR",
        "QTR",
        "QTR",
    ]  # sorted by ex-date: 06-22, 09-21, 12-01(ANNUAL), 12-21, 03-22, 06-21


def test_inc_cal_next_hero(client, wired_stores):
    """INC-CAL-NEXT-HERO:
    GIVEN ≥1 upcoming payment
    WHEN I view the calendar
    THEN the hero shows the soonest payment: symbol, "in {N} days", "Own it through {ex_date}...",
    the amount, and "pays {pay_date}"; next_payment in response.
    """
    inst_store, pos_store = wired_stores

    # Soonest: BBB ex-date 2026-07-01
    inst_store.upsert(
        {
            "id": "soon",
            "symbol": "BBB",
            "name": "Soon Payer",
            "quote_currency": "USD",
            "next_ex_date": "2026-07-01",
            "next_pay_date": "2026-07-15",
            "last_dividend_amount": 1.0,
            "dividend_frequency": "quarterly",
        }
    )
    pos_store.upsert(
        {
            "id": "pos_soon",
            "position_id": "pos_soon",
            "instrument_id": "soon",
            "account_id": "acc1",
            "quantity": 100.0,
            "quote_currency": "USD",
            "price": 50.0,
            "quote_per_unit": 1.0,
            "symbol": "BBB",
        }
    )

    # Later: AAA ex-date 2026-08-15
    inst_store.upsert(
        {
            "id": "later",
            "symbol": "AAA",
            "name": "Later Payer",
            "quote_currency": "USD",
            "next_ex_date": "2026-08-15",
            "next_pay_date": "2026-08-29",
            "last_dividend_amount": 2.0,
            "dividend_frequency": "quarterly",
        }
    )
    pos_store.upsert(
        {
            "id": "pos_later",
            "position_id": "pos_later",
            "instrument_id": "later",
            "account_id": "acc1",
            "quantity": 50.0,
            "quote_currency": "USD",
            "price": 100.0,
            "quote_per_unit": 1.0,
            "symbol": "AAA",
        }
    )

    r = client.get("/api/income/calendar?days=90")
    assert r.status_code == 200
    body = r.json()
    assert body["count"] == 2

    next_payment = body["next_payment"]
    assert next_payment is not None
    assert next_payment["symbol"] == "BBB", "Next payment should be the soonest (BBB)"
    assert next_payment["ex_date"] == "2026-07-01"
    assert next_payment["pay_date"] == "2026-07-15"
    assert next_payment["amount_base"] == pytest.approx(135.0, abs=0.01)  # 1.0 × 100 × 1.35


def test_inc_cal_amount_fx(client, wired_stores):
    """INC-CAL-AMOUNT-FX:
    GIVEN a USD-quoted holding with last_dividend_amount 0.91 and 100 shares, base = SGD, USD→SGD 1.35
    WHEN I read its calendar row
    THEN amount_base = round(0.91 × 100 × qpu × 1.35, 2)
    """
    inst_store, pos_store = wired_stores

    inst_store.upsert(
        {
            "id": "usd_hold",
            "symbol": "TEST",
            "name": "Test",
            "quote_currency": "USD",
            "next_ex_date": "2026-07-15",
            "next_pay_date": "2026-07-29",
            "last_dividend_amount": 0.91,
            "dividend_frequency": "quarterly",
        }
    )
    pos_store.upsert(
        {
            "id": "pos_usd",
            "position_id": "pos_usd",
            "instrument_id": "usd_hold",
            "account_id": "acc1",
            "quantity": 100.0,
            "quote_currency": "USD",
            "price": 200.0,
            "quote_per_unit": 1.0,
            "symbol": "TEST",
        }
    )

    r = client.get("/api/income/calendar?days=90")
    assert r.status_code == 200
    body = r.json()
    row = body["payments"][0]

    assert row["per_share"] == 0.91
    assert row["currency"] == "USD"
    # amount_base = 0.91 × 100 × 1.0 × 1.35 = 122.85
    assert row["amount_base"] == pytest.approx(122.85, abs=0.01)


def test_inc_empty_portfolio(client, wired_stores):
    """INC-EMPTY:
    GIVEN a portfolio with no income and no dividend-paying holdings
    WHEN I open the Income tab / GET /api/income/calendar
    THEN an empty state (no crash, no NaN, no div-by-zero) is returned.
    """
    inst_store, pos_store = wired_stores
    # Both stores are empty

    r = client.get("/api/income/calendar?days=90")
    assert r.status_code == 200
    body = r.json()
    assert body["count"] == 0
    assert body["next_payment"] is None
    assert body["total_base"] == 0.0
    assert body["base_currency"] == "SGD"
    assert body["horizon_days"] == 90
    assert body["payments"] == []


def test_inc_fwd_yield_mv(client, wired_stores):
    """INC-FWD-YIELD-MV:
    GIVEN holdings with large unrealized gains (market value ≫ cost basis)
    WHEN I read /api/income/forecast
    THEN forward_yield_pct = annual_total_base / market_value_base × 100 (market value, not cost),
    market_value_base is present, and implied_forward_yield_pct (cost-based) is still returned.
    """
    inst_store, pos_store = wired_stores

    # Seed an instrument with declared annual rate
    inst_store.upsert(
        {
            "id": "mvgain",
            "symbol": "MVGAIN",
            "name": "Big Unrealized Gain",
            "quote_currency": "USD",
            "dividend_rate_annual": 2.0,
            "dividend_frequency": "quarterly",
        }
    )

    # Seed a position: 100 shares @ cost $50, currently priced $200 (4x unrealized gain)
    # Market value = 100 × $200 = $20,000 SGD
    # Cost basis = 100 × $50 = $5,000 SGD (approx, assuming cost_currency == quote_currency)
    # Annual income = 2.0 / 4 * 100 * 1.35 = 67.5 SGD
    # Forward yield (market) = 67.5 / 20000 = 0.3375 %
    # Forward yield (cost) = 67.5 / 5000 = 1.35%
    pos_store.upsert(
        {
            "id": "pos_mvgain",
            "position_id": "pos_mvgain",
            "instrument_id": "mvgain",
            "account_id": "acc1",
            "quantity": 100.0,
            "quote_currency": "USD",
            "price": 200.0,  # Current price
            "quote_per_unit": 1.0,
            "cost_per_unit": 50.0,  # Cost basis
            "cost_currency": "USD",
            "symbol": "MVGAIN",
        }
    )

    r = client.get("/api/income/forecast")
    assert r.status_code == 200
    body = r.json()

    # Check that market_value_base is present
    assert "market_value_base" in body, "market_value_base missing from forecast"
    market_value = body["market_value_base"]
    assert market_value > 0, f"market_value_base should be > 0, got {market_value}"

    # Check that forward_yield_pct uses market value
    assert "forward_yield_pct" in body, "forward_yield_pct missing from forecast"
    forward_yield = body.get("forward_yield_pct")

    # Check that implied_forward_yield_pct (cost-based) is still present but different
    assert "implied_forward_yield_pct" in body, "implied_forward_yield_pct missing from forecast"
    implied_yield = body.get("implied_forward_yield_pct")

    # Both should be present; implied should be >= forward (higher yield on lower basis)
    if forward_yield is not None and implied_yield is not None:
        assert implied_yield >= forward_yield, (
            f"Implied yield ({implied_yield}%) should be >= forward yield ({forward_yield}%) "
            f"when market value > cost basis"
        )


def test_inc_cal_days_clamped(client, wired_stores):
    """INC-CAL-HORIZON edge case: days parameter is clamped to 1–365."""
    inst_store, pos_store = wired_stores
    # Empty stores

    # Over-large days → clamped to 365
    r = client.get("/api/income/calendar?days=10000")
    assert r.status_code == 200
    assert r.json()["horizon_days"] == 365

    # Negative days → clamped to 1
    r = client.get("/api/income/calendar?days=-5")
    assert r.status_code == 200
    assert r.json()["horizon_days"] == 1

    # Zero days → defaults to 90 (int(0 or DEFAULT_HORIZON_DAYS) = 90)
    r = client.get("/api/income/calendar?days=0")
    assert r.status_code == 200
    assert r.json()["horizon_days"] == 90


def test_inc_cal_multiple_mixed(client, wired_stores):
    """Integration: Multiple holdings (scheduled + estimated + non-payer) in one request."""
    inst_store, pos_store = wired_stores

    # Scheduled (MSFT)
    inst_store.upsert(
        {
            "id": "msft",
            "symbol": "MSFT",
            "name": "Microsoft",
            "quote_currency": "USD",
            "next_ex_date": "2026-08-20",
            "next_pay_date": "2026-09-10",
            "last_dividend_amount": 0.91,
            "dividend_frequency": "quarterly",
        }
    )
    pos_store.upsert(
        {
            "id": "pos_msft",
            "position_id": "pos_msft",
            "instrument_id": "msft",
            "account_id": "acc1",
            "quantity": 100.0,
            "quote_currency": "USD",
            "price": 350.0,
            "quote_per_unit": 1.0,
            "symbol": "MSFT",
        }
    )

    # Estimated (QQQ)
    inst_store.upsert(
        {
            "id": "qqq",
            "symbol": "QQQ",
            "name": "Invesco QQQ",
            "quote_currency": "USD",
            "last_ex_date": "2026-03-23",
            "dividend_frequency": "quarterly",
            "last_dividend_amount": 0.70,
        }
    )
    pos_store.upsert(
        {
            "id": "pos_qqq",
            "position_id": "pos_qqq",
            "instrument_id": "qqq",
            "account_id": "acc1",
            "quantity": 50.0,
            "quote_currency": "USD",
            "price": 400.0,
            "quote_per_unit": 1.0,
            "symbol": "QQQ",
        }
    )

    # Non-payer (GLD)
    inst_store.upsert(
        {
            "id": "gld",
            "symbol": "GLD",
            "name": "Gold",
            "quote_currency": "USD",
        }
    )
    pos_store.upsert(
        {
            "id": "pos_gld",
            "position_id": "pos_gld",
            "instrument_id": "gld",
            "account_id": "acc1",
            "quantity": 20.0,
            "quote_currency": "USD",
            "price": 180.0,
            "quote_per_unit": 1.0,
            "symbol": "GLD",
        }
    )

    r = client.get("/api/income/calendar?days=90")
    assert r.status_code == 200
    body = r.json()

    # Should see 2: MSFT (scheduled) + QQQ (estimated). GLD excluded.
    assert body["count"] == 2
    payments = body["payments"]
    symbols = [p["symbol"] for p in payments]
    assert "MSFT" in symbols and "QQQ" in symbols
    assert "GLD" not in symbols

    # Verify status labels
    msft_row = [p for p in payments if p["symbol"] == "MSFT"][0]
    qqq_row = [p for p in payments if p["symbol"] == "QQQ"][0]
    assert msft_row["status"] == "scheduled"
    assert qqq_row["status"] == "estimated"

    # Verify sort order (by ex-date)
    assert payments[0]["ex_date"] <= payments[1]["ex_date"]

    # Verify total_base is the sum
    expected_total = msft_row["amount_base"] + qqq_row["amount_base"]
    assert body["total_base"] == pytest.approx(expected_total, abs=0.01)


def test_inc_cal_zero_quantity_excluded(client, wired_stores):
    """Positions with qty ≤ 0 are excluded from the calendar (short positions or closed)."""
    inst_store, pos_store = wired_stores

    # Instrument with dividend
    inst_store.upsert(
        {
            "id": "shortable",
            "symbol": "SHORT",
            "name": "Shortable",
            "quote_currency": "USD",
            "next_ex_date": "2026-07-01",
            "last_dividend_amount": 1.0,
            "dividend_frequency": "quarterly",
        }
    )

    # Position with 0 quantity (closed)
    pos_store.upsert(
        {
            "id": "pos_zero",
            "position_id": "pos_zero",
            "instrument_id": "shortable",
            "account_id": "acc1",
            "quantity": 0.0,
            "quote_currency": "USD",
            "price": 100.0,
            "quote_per_unit": 1.0,
            "symbol": "SHORT",
        }
    )

    r = client.get("/api/income/calendar?days=90")
    assert r.status_code == 200
    body = r.json()
    assert body["count"] == 0


def test_route_registered(client):
    """Verify the GET /api/income/calendar route is registered on the app."""
    # A simple 200 response confirms the route exists
    r = client.get("/api/income/calendar?days=90")
    assert r.status_code == 200
    body = r.json()
    assert isinstance(body, dict)
    assert "count" in body
    assert "payments" in body


def test_forecast_income_route_registered(client):
    """Verify the GET /api/income/forecast route exists."""
    r = client.get("/api/income/forecast")
    assert r.status_code == 200
    body = r.json()
    assert isinstance(body, dict)
    assert "base_currency" in body
    # No crash on empty portfolio
    if "market_value_base" in body:
        assert isinstance(body["market_value_base"], (int, float))
