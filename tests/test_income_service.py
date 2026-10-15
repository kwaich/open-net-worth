"""
Unit tests for backend.services.income — income analytics + forecast (P2-INV2).

Pure/offline: stores monkeypatched onto the service, FX via a monkeypatched rate table.
Asserts the keystone invariant (base recompute ignores a stale stored base_amount) and the
three forecast fallback tiers.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from backend.services import income as svc


@pytest.fixture
def fx_usd_sgd(monkeypatch):
    # rates relative to base: SGD=1.0 pivot; USD=1.35 means 1 USD = 1.35 SGD.
    monkeypatch.setattr(svc.lots_service, "_load_rates", lambda: {"SGD": 1.0, "USD": 1.35})
    monkeypatch.setattr(svc, "base_currency", lambda: "SGD")


def _ev(gross, tax=0.0, ccy="USD", paid_at="2026-05-01", instrument_id="inst-1", kind="dividend"):
    return {
        "id": f"e-{gross}-{paid_at}",
        "position_id": "pos-1",
        "instrument_id": instrument_id,
        "account_id": "acc-1",
        "paid_at": paid_at,
        "income_kind": kind,
        "gross_amount": gross,
        "tax_withheld": tax,
        "currency": ccy,
        # A deliberately WRONG stored base snapshot — recompute must ignore it.
        "base_amount": 999999.0,
        "base_currency": "USD",
        "created_at": f"{paid_at}T00:00:00Z",
    }


# ── base recompute (the keystone invariant) ──────────────────────────────────────


def test_event_base_amount_recomputes_from_native(fx_usd_sgd):
    ev = _ev(100.0, tax=15.0)  # net 85 USD
    # 85 USD × 1.35 = 114.75 SGD — NOT the stored 999999.
    assert svc.event_base_amount(ev) == pytest.approx(114.75)


def test_summary_ignores_stale_base_amount(fx_usd_sgd, monkeypatch):
    monkeypatch.setattr(svc.events, "query_events", lambda **kw: [_ev(100.0, tax=15.0)])
    monkeypatch.setattr(svc.positions, "find_by_id", lambda _id: None)
    out = svc.income_summary()
    assert out["total_base"] == pytest.approx(114.75)
    assert out["base_currency"] == "SGD"


def test_event_base_amount_trusts_matching_stamp(monkeypatch):
    # Base is USD. Today's rate would convert 100 EUR -> 120 USD, but the event's stamp
    # (written at a different, historical rate) matches the CURRENT base — trust it.
    monkeypatch.setattr(svc, "base_currency", lambda: "USD")
    # rates relative to base: USD=1.0 pivot; EUR=1.20 means 1 EUR = 1.20 USD.
    rates = {"USD": 1.0, "EUR": 1.20}
    ev = {
        "currency": "EUR",
        "gross_amount": 100.0,
        "tax_withheld": 0.0,
        "base_amount": 110.0,
        "base_currency": "USD",
        "fx_rate": 1.10,
    }
    # Sanity: today's rate would NOT reproduce the stamp.
    assert svc.event_base_amount({**ev, "base_currency": "SGD"}, rates=rates) == pytest.approx(
        120.0
    )
    assert svc.event_base_amount(ev, rates=rates) == pytest.approx(110.0)


def test_event_base_amount_recomputes_on_base_change(monkeypatch):
    monkeypatch.setattr(svc, "base_currency", lambda: "USD")
    rates = {"USD": 1.0, "EUR": 1.20}
    ev = {
        "currency": "EUR",
        "gross_amount": 100.0,
        "tax_withheld": 0.0,
        "base_amount": 110.0,
        "base_currency": "SGD",
        "fx_rate": 1.10,
    }
    assert svc.event_base_amount(ev, rates=rates) == pytest.approx(120.0)


def test_event_base_amount_recomputes_when_unstamped(monkeypatch):
    monkeypatch.setattr(svc, "base_currency", lambda: "USD")
    rates = {"USD": 1.0, "EUR": 1.20}
    ev = {
        "currency": "EUR",
        "gross_amount": 100.0,
        "tax_withheld": 0.0,
        "base_amount": None,
        "base_currency": "USD",
    }
    assert svc.event_base_amount(ev, rates=rates) == pytest.approx(120.0)


# ── totals / by-instrument / by-kind / trailing ──────────────────────────────────


def test_summary_totals_split_and_trailing(fx_usd_sgd, monkeypatch):
    recent = date.today().isoformat()
    old = (date.today() - timedelta(days=500)).isoformat()
    evs = [
        _ev(100.0, ccy="SGD", paid_at=recent, instrument_id="inst-1", kind="dividend"),
        _ev(50.0, ccy="SGD", paid_at=recent, instrument_id="inst-2", kind="interest"),
        _ev(40.0, ccy="SGD", paid_at=old, instrument_id="inst-1", kind="dividend"),
    ]
    monkeypatch.setattr(svc.events, "query_events", lambda **kw: list(evs))
    monkeypatch.setattr(svc.positions, "find_by_id", lambda _id: None)
    out = svc.income_summary()
    assert out["total_base"] == pytest.approx(190.0)
    assert out["by_instrument"]["inst-1"] == pytest.approx(140.0)
    assert out["by_instrument"]["inst-2"] == pytest.approx(50.0)
    assert out["by_kind"]["dividend"] == pytest.approx(140.0)
    assert out["by_kind"]["interest"] == pytest.approx(50.0)
    # The 500-day-old event is outside the trailing-12m window.
    assert out["trailing_12m_base"] == pytest.approx(150.0)


def test_empty_ledger_no_crash(fx_usd_sgd, monkeypatch):
    monkeypatch.setattr(svc.events, "query_events", lambda **kw: [])
    out = svc.income_summary()
    assert out["total_base"] == 0.0
    assert out["yield_on_cost_pct"] is None


# ── yield-on-cost ────────────────────────────────────────────────────────────────


def test_yield_on_cost(fx_usd_sgd, monkeypatch):
    recent = date.today().isoformat()
    monkeypatch.setattr(
        svc.events, "query_events", lambda **kw: [_ev(100.0, ccy="SGD", paid_at=recent)]
    )
    monkeypatch.setattr(
        svc.positions,
        "find_by_id",
        lambda _id: {"id": "pos-1", "instrument_id": "inst-1", "cost_currency": "SGD"},
    )
    monkeypatch.setattr(svc.lots_service, "position_remaining_basis", lambda pid: 2000.0)
    out = svc.income_summary(position_id="pos-1")
    # 100 trailing / 2000 basis = 5%.
    assert out["yield_on_cost_pct"] == pytest.approx(5.0)


def test_zero_basis_does_not_require_an_fx_rate(monkeypatch):
    recent = date.today().isoformat()
    monkeypatch.setattr(svc, "base_currency", lambda: "SGD")
    monkeypatch.setattr(svc.lots_service, "_load_rates", lambda: {"SGD": 1.0})
    monkeypatch.setattr(
        svc.events,
        "query_events",
        lambda **kw: [_ev(100.0, ccy="SGD", paid_at=recent)],
    )
    monkeypatch.setattr(
        svc.positions,
        "find_by_id",
        lambda _id: {
            "id": "pos-1",
            "instrument_id": "inst-1",
            "cost_currency": "USD",
        },
    )
    monkeypatch.setattr(svc.lots_service, "position_remaining_basis", lambda pid: 0.0)

    out = svc.income_summary(position_id="pos-1")

    assert out["cost_basis_base"] == 0.0
    assert out["yield_on_cost_pct"] is None
    assert out["yield_on_cost_available"] is True
    assert out["missing_fx_currencies"] == []


# ── forecast: three fallback tiers ───────────────────────────────────────────────


def _pos(pid="pos-1", iid="inst-1", qty=100, ccy="SGD"):
    return {
        "id": pid,
        "instrument_id": iid,
        "account_id": "acc-1",
        "quantity": qty,
        "cost_currency": ccy,
    }


@pytest.mark.parametrize("source", ["manual", "enriched", "", None])
def test_forecast_declared_rate_tier(fx_usd_sgd, monkeypatch, source):
    """Any current-period instrument.dividend_rate_annual ⇒ "declared", regardless of
    dividend_rate_source. That field is a write-guard (protects a user's manual correction
    from being clobbered by the next price refresh) — not a real user-vs-provider trust
    distinction, since the price source IS the company's declared rate here; there's no
    more-authoritative source to ask the user to re-supply instead."""
    inst = {
        "id": "inst-1",
        "symbol": "ABC",
        "quote_currency": "SGD",
        "dividend_rate_annual": 2.0,
    }
    if source is not None:
        inst["dividend_rate_source"] = source
    monkeypatch.setattr(svc.positions, "all", lambda: [_pos(qty=100)])
    monkeypatch.setattr(svc.instruments, "all", lambda: [inst])
    monkeypatch.setattr(svc.lots_service, "position_remaining_basis", lambda pid: 1000.0)
    out = svc.forecast_income()
    row = out["holdings"][0]
    assert row["source"] == "declared"
    assert row["annual_income_base"] == pytest.approx(200.0)  # 100 × 2.0
    assert out["implied_forward_yield_pct"] == pytest.approx(20.0)  # 200 / 1000


def test_forecast_trailing_actual_tier(fx_usd_sgd, monkeypatch):
    # No declared rate ⇒ falls to trailing-12m actual income for the position.
    inst = {"id": "inst-1", "symbol": "ABC", "quote_currency": "SGD", "dividend_rate_annual": None}
    recent = date.today().isoformat()
    monkeypatch.setattr(svc.positions, "all", lambda: [_pos(qty=100)])
    monkeypatch.setattr(svc.instruments, "all", lambda: [inst])
    monkeypatch.setattr(svc.lots_service, "position_remaining_basis", lambda pid: 1000.0)
    monkeypatch.setattr(
        svc.events, "query_events", lambda **kw: [_ev(60.0, ccy="SGD", paid_at=recent)]
    )
    monkeypatch.setattr(svc.positions, "find_by_id", lambda _id: _pos())
    out = svc.forecast_income()
    row = out["holdings"][0]
    assert row["source"] == "trailing"
    assert row["annual_income_base"] == pytest.approx(60.0)


def test_forecast_empty_portfolio(fx_usd_sgd, monkeypatch):
    monkeypatch.setattr(svc.positions, "all", lambda: [])
    monkeypatch.setattr(svc.instruments, "all", lambda: [])
    out = svc.forecast_income()
    assert out["annual_total_base"] == 0.0
    assert out["implied_forward_yield_pct"] is None


def test_forecast_horizon_scaling(fx_usd_sgd, monkeypatch):
    inst = {"id": "inst-1", "symbol": "ABC", "quote_currency": "SGD", "dividend_rate_annual": 2.0}
    monkeypatch.setattr(svc.positions, "all", lambda: [_pos(qty=100)])
    monkeypatch.setattr(svc.instruments, "all", lambda: [inst])
    monkeypatch.setattr(svc.lots_service, "position_remaining_basis", lambda pid: 1000.0)
    out = svc.forecast_income(horizon_months=6)
    assert out["horizon_total_base"] == pytest.approx(100.0)  # 200 annual × 0.5
