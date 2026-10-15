"""forecast_income now reports forward yield on MARKET VALUE (ADR 0020) — network-free.

Proves the new `forward_yield_pct` / `market_value_base` exist and that the market-value yield
differs from the legacy cost-basis `implied_forward_yield_pct` (the comparability fix)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from backend.services import income as svc
from backend.services import positions as positions_mod


@pytest.fixture
def wired(monkeypatch):
    monkeypatch.setattr(svc, "base_currency", lambda: "SGD")
    monkeypatch.setattr(svc.lots_service, "_load_rates", lambda: {"SGD": 1.0, "USD": 1.35})
    monkeypatch.setattr(svc.lots_service, "position_remaining_basis", lambda pid: 100.0)

    pos = {"id": "p1", "instrument_id": "i1", "quantity": 10, "cost_currency": "USD"}
    inst = {
        "id": "i1",
        "symbol": "ABC",
        "name": "ABC",
        "quote_currency": "USD",
        "dividend_rate_annual": 2.0,
    }
    monkeypatch.setattr(svc, "positions", SimpleNamespace(all=lambda: [pos]))
    monkeypatch.setattr(svc, "instruments", SimpleNamespace(all=lambda: [inst]))
    # Market value: 10 sh × 50 USD = 500 USD → 675 SGD.
    monkeypatch.setattr(
        positions_mod,
        "hydrated",
        lambda *a, **k: [
            {
                "position_id": "p1",
                "quantity": 10,
                "price": 50.0,
                "quote_per_unit": 1.0,
                "quote_currency": "USD",
            }
        ],
    )


def test_forecast_reports_market_value_forward_yield(wired):
    out = svc.forecast_income(horizon_months=12)
    # annual income = 2.0 × 10 = 20 USD → 27 SGD.
    assert out["annual_total_base"] == pytest.approx(27.0)
    assert out["market_value_base"] == pytest.approx(675.0)
    # forward yield on MARKET value: 27 / 675 = 4.0%
    assert out["forward_yield_pct"] == pytest.approx(4.0)
    # legacy cost-basis yield: 27 / 135 = 20.0% — retained, and demonstrably different.
    assert out["implied_forward_yield_pct"] == pytest.approx(20.0)
    assert out["forward_yield_pct"] != out["implied_forward_yield_pct"]


def test_forecast_market_value_zero_yields_none(wired, monkeypatch):
    monkeypatch.setattr(positions_mod, "hydrated", lambda *a, **k: [])
    out = svc.forecast_income()
    assert out["market_value_base"] == 0.0
    assert out["forward_yield_pct"] is None


def test_forecast_excludes_unvested_equity_grant_units(monkeypatch):
    """An unvested RSU tranche is not owned yet (ADR
    2026-07-31-unvested-equity-excluded-from-net-worth) — it must not project dividend
    income the holder will not receive. `forecast_income` iterates the RAW position store
    (`positions.all()`), not `positions_service.hydrated()`, so it must resolve entitlement
    via `vesting_state_of()` — a plain `pos.get("vesting_state")` misses the real-world shape
    (legacy `bucket="locked"`, no `vesting_state` key at all) that unvested grant positions
    are actually stored with."""
    monkeypatch.setattr(svc, "base_currency", lambda: "SGD")
    monkeypatch.setattr(svc.lots_service, "_load_rates", lambda: {"SGD": 1.0, "USD": 1.35})
    monkeypatch.setattr(svc.lots_service, "position_remaining_basis", lambda pid: 100.0)

    vested_pos = {"id": "p1", "instrument_id": "i1", "quantity": 10, "cost_currency": "USD"}
    # The real shape an unvested equity-grant position is stored with: legacy `bucket`, no
    # `vesting_state` key — see backend/db/position_store.py::vesting_state_of.
    unvested_pos = {
        "id": "p2",
        "instrument_id": "i1",
        "quantity": 1000,
        "cost_currency": "USD",
        "bucket": "locked",
    }
    inst = {
        "id": "i1",
        "symbol": "ABC",
        "name": "ABC",
        "quote_currency": "USD",
        "dividend_rate_annual": 2.0,
    }
    monkeypatch.setattr(svc, "positions", SimpleNamespace(all=lambda: [vested_pos, unvested_pos]))
    monkeypatch.setattr(svc, "instruments", SimpleNamespace(all=lambda: [inst]))
    monkeypatch.setattr(
        positions_mod,
        "hydrated",
        lambda *a, **k: [
            {
                "position_id": "p1",
                "quantity": 10,
                "price": 50.0,
                "quote_per_unit": 1.0,
                "quote_currency": "USD",
                "vesting_state": "vested",
            }
        ],
    )

    out = svc.forecast_income(horizon_months=12)
    # Unchanged from the vested-only baseline: the 1000-unit unvested tranche (which would
    # add 2.0 x 1000 = 2000 USD annual if wrongly counted) contributes nothing.
    assert out["annual_total_base"] == pytest.approx(27.0)
    assert all(r["position_id"] != "p2" for r in out["holdings"])
