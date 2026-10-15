"""Unit + smoke tests for the coupon/principal extension to dividend_calendar (lane/income).

Covers the RFC test plan:
  - Coupon bond: dates within horizon, kind="coupon", per_share == annual/2, status="scheduled".
  - Bond maturing in horizon: principal row at maturity_date, final coupon principal_at_maturity.
  - T-bill (zero-coupon): single principal row, no coupon rows.
  - No bonds → existing dividend rows byte-identical (kind="dividend", principal_at_maturity=False).
  - Bond maturing outside horizon: no principal row, coupons only.
"""

from __future__ import annotations

import datetime as _dt
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api.income import router
from backend.services import dividend_calendar as svc
from backend.services import fixed_income


class _FixedToday:
    """Stand-in so date.today() is deterministic (matches frozen 2026-06-21)."""

    @staticmethod
    def today() -> _dt.date:
        return _dt.date(2026, 6, 21)

    @staticmethod
    def fromisoformat(s: str) -> _dt.date:
        return _dt.date.fromisoformat(s)


@pytest.fixture
def wired(monkeypatch):
    monkeypatch.setattr(svc, "base_currency", lambda: "USD")
    monkeypatch.setattr(svc.lots_service, "_load_rates", lambda: {"USD": 1.0})
    monkeypatch.setattr(svc, "user_today", _FixedToday.today)

    def _set(holdings, insts):
        monkeypatch.setattr(svc.positions_service, "hydrated", lambda *a, **k: holdings)
        monkeypatch.setattr(svc, "instruments", SimpleNamespace(all=lambda: insts))

    return _set


def _bond_inst(
    inst_id, subtype="market", coupon_rate=0.05, freq=6, maturity="2028-06-21", face=1000.0
):
    return {
        "id": inst_id,
        "symbol": "BOND",
        "name": "Test Bond",
        "quote_currency": "USD",
        "asset_class": "bond",
        "bond_terms": {
            "subtype": subtype,
            "coupon_rate_annual": coupon_rate,
            "coupon_freq_months": freq,
            "maturity_date": maturity,
            "face_value": face,
        },
    }


def _hold(pos_id, inst_id, qty=10.0, ccy="USD", qpu=1.0, symbol="BOND"):
    return {
        "position_id": pos_id,
        "instrument_id": inst_id,
        "quantity": qty,
        "quote_currency": ccy,
        "price": 1000.0,
        "quote_per_unit": qpu,
        "symbol": symbol,
    }


# ── Coupon rows ─────────────────────────────────────────────────────────────────────


def test_coupon_bond_emits_coupon_rows_in_horizon(wired):
    """Semi-annual coupon bond: coupons within 90d window are kind="coupon", status="scheduled"."""
    # Maturity 2028-06-21 → coupons count back from maturity: 2028-06-21, 2027-12-21, 2027-06-21,
    # 2026-12-21.  Of those, 2026-12-21 is within 90 days of 2026-06-21? No. 90d horizon =
    # 2026-09-19.  Let's use a bond maturity that puts a coupon in-horizon.
    # coupon_freq_months=6, maturity=2026-09-15 → next coupon back: 2026-09-15 (in-horizon).
    inst = _bond_inst(
        "i1", subtype="market", coupon_rate=0.06, freq=6, maturity="2026-09-15", face=1000.0
    )
    wired([_hold("p1", "i1", qty=5)], [inst])

    out = svc.upcoming_payments(days=90)
    coupon_rows = [p for p in out["payments"] if p["kind"] == "coupon"]
    assert len(coupon_rows) >= 1, "Expected at least one coupon row"
    row = coupon_rows[0]
    assert row["status"] == "scheduled"
    assert row["kind"] == "coupon"
    # annual_coupon_per_unit = face * rate = 1000 * 0.06 = 60; semi-annual = 30 per unit
    expected_per = fixed_income.annual_coupon_per_unit(inst) / 2.0  # freq=6 → 2/year
    assert abs(row["per_share"] - expected_per) < 0.01
    assert row["amount_base"] == pytest.approx(row["per_share"] * 5 * 1.0, abs=0.01)


def test_bond_maturity_in_horizon_emits_principal_row(wired):
    """Bond maturing within 45d → principal row at maturity_date, final coupon flags principal_at_maturity."""
    # Maturity 2026-07-31 (40d out). Semi-annual coupon: schedule back from 2026-07-31:
    # 2026-07-31, 2026-01-31 (past). So 2026-07-31 is the only coupon in the 90d window.
    inst = _bond_inst(
        "i1", subtype="market", coupon_rate=0.04, freq=6, maturity="2026-07-31", face=1000.0
    )
    wired([_hold("p1", "i1", qty=2)], [inst])

    out = svc.upcoming_payments(days=90)
    kinds = {p["kind"] for p in out["payments"]}
    assert "principal" in kinds, "Expected a principal row"
    assert "coupon" in kinds, "Expected coupon row(s)"

    principal_row = next(p for p in out["payments"] if p["kind"] == "principal")
    assert principal_row["ex_date"] == "2026-07-31"
    # face * qty * qpu = 1000 * 2 * 1 = 2000
    assert principal_row["amount_base"] == pytest.approx(2000.0, abs=0.01)

    final_coupon = next(p for p in out["payments"] if p["kind"] == "coupon")
    assert final_coupon["principal_at_maturity"] is True


def test_tbill_no_coupon_freq_emits_principal_only(wired):
    """T-bill (zero-coupon, no coupon_freq_months) maturing in-horizon → one principal row, no coupons."""
    inst = {
        "id": "i1",
        "symbol": "TBILL",
        "name": "US T-Bill",
        "quote_currency": "USD",
        "asset_class": "bond",
        "bond_terms": {
            "subtype": "tbill",
            "maturity_date": "2026-08-01",
            "face_value": 1000.0,
            "issue_price": 98.5,
        },
    }
    wired([_hold("p1", "i1", qty=3, symbol="TBILL")], [inst])

    out = svc.upcoming_payments(days=90)
    assert all(p["kind"] != "coupon" for p in out["payments"]), "No coupon rows expected"
    principal_rows = [p for p in out["payments"] if p["kind"] == "principal"]
    assert len(principal_rows) == 1
    assert principal_rows[0]["ex_date"] == "2026-08-01"
    # face * qty * qpu = 1000 * 3 * 1 = 3000
    assert principal_rows[0]["amount_base"] == pytest.approx(3000.0, abs=0.01)


def test_dividend_rows_regression_no_bonds(wired):
    """Non-bond holdings produce kind="dividend", principal_at_maturity=False — byte-identical."""
    wired(
        [
            {
                "position_id": "p1",
                "instrument_id": "i1",
                "quantity": 100,
                "quote_currency": "USD",
                "price": 50.0,
                "quote_per_unit": 1.0,
                "symbol": "AAPL",
            }
        ],
        [
            {
                "id": "i1",
                "symbol": "AAPL",
                "name": "Apple",
                "quote_currency": "USD",
                "next_ex_date": "2026-08-20",
                "next_pay_date": "2026-09-10",
                "last_dividend_amount": 0.25,
                "dividend_frequency": "quarterly",
            }
        ],
    )
    out = svc.upcoming_payments(days=90)
    assert out["count"] == 1
    row = out["payments"][0]
    assert row["kind"] == "dividend"
    assert row["principal_at_maturity"] is False


def test_bond_maturity_outside_horizon_no_principal(wired):
    """Bond maturing 2y out: no principal row in a 90d window.

    Maturity 2028-06-21 with semi-annual coupons: counting back from maturity by 6-month steps
    lands a coupon on 2026-06-21 (frozen today) which IS within [today, horizon=2026-09-19],
    so one coupon row is expected — but NO principal row because maturity is outside the horizon.
    """
    inst = _bond_inst(
        "i1", subtype="market", coupon_rate=0.05, freq=6, maturity="2028-06-21", face=1000.0
    )
    wired([_hold("p1", "i1", qty=5)], [inst])

    out = svc.upcoming_payments(days=90)
    principal_rows = [p for p in out["payments"] if p["kind"] == "principal"]
    assert len(principal_rows) == 0, "No principal row expected when maturity is 2y out"
    # Coupon dated 2026-06-21 (today) does appear in-window — this is correct; only principal absent.
    for row in out["payments"]:
        assert row["kind"] == "coupon", f"Expected coupon only, got {row['kind']}"
        assert row["principal_at_maturity"] is False, (
            "principal_at_maturity must be False when maturity outside horizon"
        )


# ── Endpoint smoke ───────────────────────────────────────────────────────────────────


def test_calendar_api_kind_and_principal_fields(wired, monkeypatch):
    """Every payment row has 'kind' ∈ {dividend,coupon,principal} and 'principal_at_maturity' bool."""
    # Seed a dividend payer + a coupon bond.
    div_hold = {
        "position_id": "p1",
        "instrument_id": "i1",
        "quantity": 10,
        "quote_currency": "USD",
        "price": 100.0,
        "quote_per_unit": 1.0,
        "symbol": "MSFT",
    }
    div_inst = {
        "id": "i1",
        "symbol": "MSFT",
        "name": "Microsoft",
        "quote_currency": "USD",
        "next_ex_date": "2026-08-20",
        "next_pay_date": "2026-09-10",
        "last_dividend_amount": 0.91,
        "dividend_frequency": "quarterly",
    }
    bond_hold = _hold("p2", "i2", qty=5, symbol="BOND")
    bond_inst = _bond_inst("i2", subtype="market", coupon_rate=0.04, freq=6, maturity="2026-09-01")

    monkeypatch.setattr(svc.positions_service, "hydrated", lambda *a, **k: [div_hold, bond_hold])
    monkeypatch.setattr(svc, "instruments", SimpleNamespace(all=lambda: [div_inst, bond_inst]))

    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)

    r = client.get("/api/income/calendar?days=120")
    assert r.status_code == 200
    body = r.json()
    for payment in body["payments"]:
        assert payment["kind"] in {"dividend", "coupon", "principal"}, payment
        assert isinstance(payment["principal_at_maturity"], bool), payment


# ── qpu double-scale regression (was: amount × qty × qpu; correct: amount × qty) ────────


def test_coupon_amount_ssb_qpu(wired):
    """SSB-style bond: qpu=0.01 (100 bonds = face 100 each, quoted at 1/100 = 0.01).
    Coupon per-unit = face * rate = 100 * 0.03 = 3; per-payment (semi-annual) = 1.5.
    Holding: qty=100 at qpu=0.01.
    amount_base must be per_payment * qty = 1.5 * 100 = 150 — NOT 1.5 * 100 * 0.01 = 1.5."""
    inst = _bond_inst(
        "i-ssb", subtype="ssb", coupon_rate=0.03, freq=6, maturity="2026-09-15", face=100.0
    )
    # qpu=0.01 simulates SSB pricing (face 100, quoted in cents-like fractional units)
    hold = _hold("p-ssb", "i-ssb", qty=100.0, qpu=0.01)
    wired([hold], [inst])

    out = svc.upcoming_payments(days=90)
    coupon_rows = [p for p in out["payments"] if p["kind"] == "coupon"]
    assert len(coupon_rows) >= 1, "Expected at least one coupon row"
    row = coupon_rows[0]
    per_payment = fixed_income.annual_coupon_per_unit(inst) / 2.0  # semi-annual
    expected = pytest.approx(per_payment * 100.0, abs=0.01)  # qty=100, NOT * qpu
    assert row["amount_base"] == expected, (
        f"SSB qpu=0.01 double-scale: expected {float(expected)}, got {row['amount_base']}"
    )


def test_coupon_amount_market_bond_qpu(wired):
    """Market bond: qpu=10 (e.g. bond quoted in units of 10).
    Coupon per-unit = face * rate = 1000 * 0.05 = 50; per-payment (semi-annual) = 25.
    Holding: qty=5 at qpu=10.
    amount_base must be per_payment * qty = 25 * 5 = 125 — NOT 25 * 5 * 10 = 1250."""
    inst = _bond_inst(
        "i-mkt", subtype="market", coupon_rate=0.05, freq=6, maturity="2026-09-15", face=1000.0
    )
    hold = _hold("p-mkt", "i-mkt", qty=5.0, qpu=10.0)
    wired([hold], [inst])

    out = svc.upcoming_payments(days=90)
    coupon_rows = [p for p in out["payments"] if p["kind"] == "coupon"]
    assert len(coupon_rows) >= 1, "Expected at least one coupon row"
    row = coupon_rows[0]
    per_payment = fixed_income.annual_coupon_per_unit(inst) / 2.0
    expected = pytest.approx(per_payment * 5.0, abs=0.01)  # qty=5, NOT * qpu
    assert row["amount_base"] == expected, (
        f"Market bond qpu=10 double-scale: expected {float(expected)}, got {row['amount_base']}"
    )


def test_principal_amount_no_qpu_scaling(wired):
    """Principal redemption amount = face * qty (not * qpu).
    Bond: face=1000, qty=3, qpu=5.
    proceeds must be 1000 * 3 = 3000, not 1000 * 3 * 5 = 15000."""
    inst = _bond_inst(
        "i-p", subtype="market", coupon_rate=0.0, freq=0, maturity="2026-07-01", face=1000.0
    )
    hold = _hold("p-p", "i-p", qty=3.0, qpu=5.0)
    wired([hold], [inst])

    out = svc.upcoming_payments(days=90)
    principal_rows = [p for p in out["payments"] if p["kind"] == "principal"]
    assert len(principal_rows) == 1
    assert principal_rows[0]["amount_base"] == pytest.approx(3000.0, abs=0.01), (
        f"Principal qpu double-scale: expected 3000, got {principal_rows[0]['amount_base']}"
    )


def test_total_base_excludes_principal(wired):
    """total_base is income-only (coupons); principal is counted in principal_base."""
    inst = _bond_inst(
        "i1", subtype="market", coupon_rate=0.04, freq=6, maturity="2026-07-31", face=1000.0
    )
    wired([_hold("p1", "i1", qty=2)], [inst])

    out = svc.upcoming_payments(days=90)
    coupon_total = sum(p["amount_base"] for p in out["payments"] if p["kind"] == "coupon")
    principal_total = sum(p["amount_base"] for p in out["payments"] if p["kind"] == "principal")
    assert out["total_base"] == pytest.approx(coupon_total, abs=0.01)
    assert out.get("principal_base", 0) == pytest.approx(principal_total, abs=0.01)
