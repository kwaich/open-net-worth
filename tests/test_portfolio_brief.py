"""
Unit + API smoke tests for backend/services/portfolio_brief.py.

Offline + isolated: all service calls are monkeypatched to return canned data.
Tests verify signal generation logic (threshold guards, narrative shape, actionability)
and the HTTP contract (200 + keys present).

Run:
    .venv/bin/python3 -m pytest tests/test_portfolio_brief.py -v
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api import investments as inv_api  # noqa: E402
from backend.api.investments import router
from backend.services import portfolio_brief as brief_svc

# ── Helpers ───────────────────────────────────────────────────────────────────

BASE = "SGD"


def make_row(
    *,
    symbol: str = "AAPL",
    label: str = "Apple Inc.",
    asset_class: str = "equity",
    quantity: float = 100.0,
    price: float = 100.0,
    quote_per_unit: float = 1.0,
    quote_currency: str = BASE,
    day_change: float | None = None,
    vesting_state: str = "vested",
) -> dict:
    return {
        # Hydrated position rows always carry their instrument (the invested-book filter keys on it).
        "instrument_id": f"ins_{symbol}",
        "symbol": symbol,
        "label": label,
        "asset_class": asset_class,
        "quantity": quantity,
        "price": price,
        "quote_per_unit": quote_per_unit,
        "quote_currency": quote_currency,
        "day_change": day_change,
        "vesting_state": vesting_state,
    }


def iso_days(n: int) -> str:
    """ISO date string n days from today."""
    return (date.today() + timedelta(days=n)).isoformat()


# ── Tests ──────────────────────────────────────────────────────────────────────


def test_ontrack_fallback(monkeypatch):
    """Empty book + no data → ontrack fallback, decisions_count=0."""
    monkeypatch.setattr(brief_svc.positions_service, "hydrated", lambda: [])
    monkeypatch.setattr(
        brief_svc.rebalance_service,
        "rebalance_plan",
        lambda **kw: {"status": "no_targets", "allocations": []},
    )
    monkeypatch.setattr(brief_svc.equity_service, "upcoming_vests", lambda window_days=None: [])
    monkeypatch.setattr(
        brief_svc, "upcoming_payments", lambda days=90, base=None: {"next_payment": None}
    )
    monkeypatch.setattr(brief_svc.profile_service, "read_profile", lambda: {})
    monkeypatch.setattr(brief_svc, "base_currency", lambda: BASE)

    result = brief_svc.compute_brief()
    assert result["decisions_count"] == 0
    assert len(result["signals"]) == 1
    assert result["signals"][0]["kind"] == "ontrack"
    assert result["signals"][0]["actionable"] is False


def test_concentration_signal(monkeypatch):
    """Top holding at 50% of investments → concentration signal fires."""
    rows = [
        make_row(symbol="AAPL", quantity=100, price=50, quote_per_unit=1),  # 5000
        make_row(
            symbol="GOOG", quantity=100, price=50, quote_per_unit=1
        ),  # 5000 total but same as AAPL
    ]
    # AAPL = 5000, GOOG = 5000, total = 10000, AAPL = 50% > 20%
    monkeypatch.setattr(brief_svc.positions_service, "hydrated", lambda: rows)
    monkeypatch.setattr(
        brief_svc.rebalance_service,
        "rebalance_plan",
        lambda **kw: {"status": "no_targets", "allocations": []},
    )
    monkeypatch.setattr(brief_svc.equity_service, "upcoming_vests", lambda window_days=None: [])
    monkeypatch.setattr(
        brief_svc, "upcoming_payments", lambda days=90, base=None: {"next_payment": None}
    )
    monkeypatch.setattr(
        brief_svc.profile_service, "read_profile", lambda: {"risk_concentration_guideline": 20.0}
    )
    monkeypatch.setattr(brief_svc, "base_currency", lambda: BASE)

    result = brief_svc.compute_brief()
    kinds = [s["kind"] for s in result["signals"]]
    assert "concentration" in kinds
    conc = next(s for s in result["signals"] if s["kind"] == "concentration")
    assert conc["actionable"] is True
    assert conc["tone"] == "warn"
    assert result["decisions_count"] >= 1


def test_concentration_below_threshold(monkeypatch):
    """Top holding at 10% → no concentration signal (no single holding exceeds 20%)."""
    # 10 holdings each at 10% of 10000 total — none exceeds the 20% guideline.
    all_rows = [
        make_row(symbol=f"S{i}", quantity=10, price=100)  # each = 1000 SGD
        for i in range(10)
    ]
    monkeypatch.setattr(brief_svc.positions_service, "hydrated", lambda: all_rows)
    monkeypatch.setattr(
        brief_svc.rebalance_service,
        "rebalance_plan",
        lambda **kw: {"status": "no_targets", "allocations": []},
    )
    monkeypatch.setattr(brief_svc.equity_service, "upcoming_vests", lambda window_days=None: [])
    monkeypatch.setattr(
        brief_svc, "upcoming_payments", lambda days=90, base=None: {"next_payment": None}
    )
    monkeypatch.setattr(
        brief_svc.profile_service, "read_profile", lambda: {"risk_concentration_guideline": 20.0}
    )
    monkeypatch.setattr(brief_svc, "base_currency", lambda: BASE)

    result = brief_svc.compute_brief()
    kinds = [s["kind"] for s in result["signals"]]
    assert "concentration" not in kinds


def test_concentration_signal_ignores_unvested_equity_grant_units(monkeypatch):
    """An unvested RSU tranche is not owned yet (ADR
    2026-07-31-unvested-equity-excluded-from-net-worth) — it must never be flagged as a
    concentration risk, and must never inflate the denominator other holdings are compared
    against. A huge unvested row alongside 10 EQUAL vested rows (each 10% of the vested-only
    book, under the 20% guideline) must fire NO signal — a bug that counts the unvested row
    would either flag IT directly (it dwarfs everything else) or skew the vested rows' shares."""
    rows = [
        make_row(symbol="RSU", quantity=1000, price=1000, vesting_state="unvested"),  # 1,000,000
        *[make_row(symbol=f"S{i}", quantity=10, price=100) for i in range(10)],  # 1,000 each
    ]
    monkeypatch.setattr(brief_svc.positions_service, "hydrated", lambda: rows)
    monkeypatch.setattr(
        brief_svc.rebalance_service,
        "rebalance_plan",
        lambda **kw: {"status": "no_targets", "allocations": []},
    )
    monkeypatch.setattr(brief_svc.equity_service, "upcoming_vests", lambda window_days=None: [])
    monkeypatch.setattr(
        brief_svc, "upcoming_payments", lambda days=90, base=None: {"next_payment": None}
    )
    monkeypatch.setattr(
        brief_svc.profile_service, "read_profile", lambda: {"risk_concentration_guideline": 20.0}
    )
    monkeypatch.setattr(brief_svc, "base_currency", lambda: BASE)

    result = brief_svc.compute_brief()
    kinds = [s["kind"] for s in result["signals"]]
    assert "concentration" not in kinds


def test_rebalance_signal(monkeypatch):
    """Allocation with off_target=True and drift_pct=10 → rebalance signal fires."""
    monkeypatch.setattr(brief_svc.positions_service, "hydrated", lambda: [])
    monkeypatch.setattr(
        brief_svc.rebalance_service,
        "rebalance_plan",
        lambda **kw: {
            "status": "ok",
            "allocations": [
                {"asset_class": "equity", "drift_pct": 10.0, "off_target": True},
            ],
        },
    )
    monkeypatch.setattr(brief_svc.equity_service, "upcoming_vests", lambda window_days=None: [])
    monkeypatch.setattr(
        brief_svc, "upcoming_payments", lambda days=90, base=None: {"next_payment": None}
    )
    monkeypatch.setattr(brief_svc.profile_service, "read_profile", lambda: {})
    monkeypatch.setattr(brief_svc, "base_currency", lambda: BASE)

    result = brief_svc.compute_brief()
    kinds = [s["kind"] for s in result["signals"]]
    assert "rebalance" in kinds
    reb = next(s for s in result["signals"] if s["kind"] == "rebalance")
    assert reb["actionable"] is True
    assert "Equities are 10.0 percentage points above your target." in reb["narrative"]


def test_rebalance_drift_below_threshold(monkeypatch):
    """drift_pct=4 (< 5) → no rebalance signal even if off_target=True."""
    monkeypatch.setattr(brief_svc.positions_service, "hydrated", lambda: [])
    monkeypatch.setattr(
        brief_svc.rebalance_service,
        "rebalance_plan",
        lambda **kw: {
            "status": "ok",
            "allocations": [
                {"asset_class": "equity", "drift_pct": 4.0, "off_target": True},
            ],
        },
    )
    monkeypatch.setattr(brief_svc.equity_service, "upcoming_vests", lambda window_days=None: [])
    monkeypatch.setattr(
        brief_svc, "upcoming_payments", lambda days=90, base=None: {"next_payment": None}
    )
    monkeypatch.setattr(brief_svc.profile_service, "read_profile", lambda: {})
    monkeypatch.setattr(brief_svc, "base_currency", lambda: BASE)

    result = brief_svc.compute_brief()
    kinds = [s["kind"] for s in result["signals"]]
    assert "rebalance" not in kinds


def test_vesting_signal(monkeypatch):
    """Next vest tomorrow → vesting signal fires, actionable=True."""
    monkeypatch.setattr(brief_svc.positions_service, "hydrated", lambda: [])
    monkeypatch.setattr(
        brief_svc.rebalance_service,
        "rebalance_plan",
        lambda **kw: {"status": "no_targets", "allocations": []},
    )
    monkeypatch.setattr(
        brief_svc.equity_service,
        "upcoming_vests",
        lambda window_days=None: [
            {
                "symbol": "NVDA",
                "vest_date": iso_days(1),
                "units": 50,
                "indicative_value_base": 10000.0,
            }
        ],
    )
    monkeypatch.setattr(
        brief_svc, "upcoming_payments", lambda days=90, base=None: {"next_payment": None}
    )
    monkeypatch.setattr(brief_svc.profile_service, "read_profile", lambda: {})
    monkeypatch.setattr(brief_svc, "base_currency", lambda: BASE)

    result = brief_svc.compute_brief()
    kinds = [s["kind"] for s in result["signals"]]
    assert "vesting" in kinds
    vest = next(s for s in result["signals"] if s["kind"] == "vesting")
    assert vest["actionable"] is True
    assert "NVDA" in vest["narrative"]
    assert "vests in 1 day" in vest["narrative"]
    assert vest["amount"] == 10000.0


def test_vesting_too_far(monkeypatch):
    """Vest date 50 days out → no vesting signal (beyond 45-day window)."""
    monkeypatch.setattr(brief_svc.positions_service, "hydrated", lambda: [])
    monkeypatch.setattr(
        brief_svc.rebalance_service,
        "rebalance_plan",
        lambda **kw: {"status": "no_targets", "allocations": []},
    )
    monkeypatch.setattr(
        brief_svc.equity_service,
        "upcoming_vests",
        lambda window_days=None: [
            {
                "symbol": "NVDA",
                "vest_date": iso_days(50),
                "units": 50,
                "indicative_value_base": 10000.0,
            }
        ],
    )
    monkeypatch.setattr(
        brief_svc, "upcoming_payments", lambda days=90, base=None: {"next_payment": None}
    )
    monkeypatch.setattr(brief_svc.profile_service, "read_profile", lambda: {})
    monkeypatch.setattr(brief_svc, "base_currency", lambda: BASE)

    result = brief_svc.compute_brief()
    kinds = [s["kind"] for s in result["signals"]]
    assert "vesting" not in kinds


def test_today_signal_ignores_unvested_equity_grant_units(monkeypatch):
    """An unvested RSU tranche's paper day-move must not be folded into "Portfolio is
    ±$X today" — neither as dollars gained/lost (the numerator) nor as extra denominator
    that dilutes the real, owned move. A vested holding up 5% today must read as +5%, not
    swamped by an unvested tranche's much larger simulated swing."""
    rows = [
        make_row(symbol="AAPL", quantity=10, price=100, day_change=50.0),  # value 1000, +5%
        make_row(
            symbol="RSU",
            quantity=100,
            price=1000,
            day_change=-20000.0,
            vesting_state="unvested",
        ),  # value 100,000 — not owned; must contribute neither dollars nor denominator
    ]
    monkeypatch.setattr(brief_svc.positions_service, "hydrated", lambda: rows)
    monkeypatch.setattr(
        brief_svc.rebalance_service,
        "rebalance_plan",
        lambda **kw: {"status": "no_targets", "allocations": []},
    )
    monkeypatch.setattr(brief_svc.equity_service, "upcoming_vests", lambda window_days=None: [])
    monkeypatch.setattr(
        brief_svc, "upcoming_payments", lambda days=90, base=None: {"next_payment": None}
    )
    monkeypatch.setattr(brief_svc.profile_service, "read_profile", lambda: {})
    monkeypatch.setattr(brief_svc, "base_currency", lambda: BASE)

    result = brief_svc.compute_brief()
    today = next(s for s in result["signals"] if s["kind"] == "today")
    assert today["amount"] == pytest.approx(50.0)
    assert today["tone"] == "good"


def test_income_signal(monkeypatch):
    """next_payment with ex_date 5 days out → income signal fires, actionable=False."""
    monkeypatch.setattr(brief_svc.positions_service, "hydrated", lambda: [])
    monkeypatch.setattr(
        brief_svc.rebalance_service,
        "rebalance_plan",
        lambda **kw: {"status": "no_targets", "allocations": []},
    )
    monkeypatch.setattr(brief_svc.equity_service, "upcoming_vests", lambda window_days=None: [])
    monkeypatch.setattr(
        brief_svc,
        "upcoming_payments",
        lambda days=30, base=None: {
            "next_payment": {
                "symbol": "VTI",
                "ex_date": iso_days(5),
                "amount_base": 250.0,
            }
        },
    )
    monkeypatch.setattr(brief_svc.profile_service, "read_profile", lambda: {})
    monkeypatch.setattr(brief_svc, "base_currency", lambda: BASE)

    result = brief_svc.compute_brief()
    kinds = [s["kind"] for s in result["signals"]]
    assert "income" in kinds
    inc = next(s for s in result["signals"] if s["kind"] == "income")
    assert inc["actionable"] is False
    assert inc["tone"] == "good"
    assert "VTI" in inc["narrative"]
    assert "ex-div in 5 days" in inc["narrative"]
    assert inc["amount"] == 250.0


def test_decisions_count_counts_only_actionable(monkeypatch):
    """concentration (actionable) + rebalance (actionable) + income (not actionable) → decisions_count=2."""
    rows = [
        make_row(symbol="AAPL", quantity=100, price=50),  # 5000
        make_row(symbol="REST", quantity=100, price=50),  # 5000 — AAPL = 50% > 20%
    ]
    monkeypatch.setattr(brief_svc.positions_service, "hydrated", lambda: rows)
    monkeypatch.setattr(
        brief_svc.rebalance_service,
        "rebalance_plan",
        lambda **kw: {
            "status": "ok",
            "allocations": [
                {"asset_class": "equity", "drift_pct": 10.0, "off_target": True},
            ],
        },
    )
    monkeypatch.setattr(brief_svc.equity_service, "upcoming_vests", lambda window_days=None: [])
    monkeypatch.setattr(
        brief_svc,
        "upcoming_payments",
        lambda days=30, base=None: {
            "next_payment": {
                "symbol": "VTI",
                "ex_date": iso_days(5),
                "amount_base": 100.0,
            }
        },
    )
    monkeypatch.setattr(
        brief_svc.profile_service, "read_profile", lambda: {"risk_concentration_guideline": 20.0}
    )
    monkeypatch.setattr(brief_svc, "base_currency", lambda: BASE)

    result = brief_svc.compute_brief()
    kinds = [s["kind"] for s in result["signals"]]
    assert "concentration" in kinds
    assert "rebalance" in kinds
    assert "income" in kinds
    # Only concentration + rebalance are actionable
    assert result["decisions_count"] == 2


def test_api_smoke(monkeypatch):
    """GET /api/investments/brief returns 200 with correct shape."""
    canned = {
        "signals": [
            {
                "id": "ontrack",
                "kind": "ontrack",
                "tone": "good",
                "actionable": False,
                "tag": "On track",
                "narrative": "All good.",
            }
        ],
        "decisions_count": 0,
        "base_currency": "SGD",
    }
    monkeypatch.setattr(inv_api.brief_service, "compute_brief", lambda: canned)

    app = FastAPI()
    app.include_router(router)
    r = TestClient(app).get("/api/investments/brief")

    assert r.status_code == 200
    body = r.json()
    assert "signals" in body
    assert "decisions_count" in body
    assert "base_currency" in body
    assert body["decisions_count"] == 0
    assert body["signals"][0]["kind"] == "ontrack"
