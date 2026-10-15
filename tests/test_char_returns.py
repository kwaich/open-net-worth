"""
CHARACTERIZATION test for backend.services.returns — the TWR + XIRR result over a FIXED
flow/value fixture (P2-INV3).

Like tests/test_char_ledger_math.py, this freezes what the math PRODUCES today over a
hand-built fixture so a later refactor — daily (not monthly) value granularity, INV2
dividend cash flows, a different solver tolerance — can't silently shift the reported
return number. If a future change LEGITIMATELY moves the figure, the expected constant here
is updated INTENTIONALLY (with the reasoning), exactly like the tool-dispatch snapshot.

The fixture is a 1-year window with one mid-year deposit and one disposal — enough that
TWR (flow-neutralized) and XIRR (flow-timed) are distinct, non-trivial numbers.

All offline: value series passed directly, lots/realized stores monkeypatched into tmp_path.
"""

from __future__ import annotations

import pytest

from backend.services import returns as r

# A fixed value series (FX-normalized, market-band fold already done) over a 1Y window:
# open 10,000 → mid 12,000 → close 18,000 (after a 5,000 deposit at mid).
_SERIES = [
    {"date": "2025-06-05", "value": 10_000.0},
    {"date": "2025-12-05", "value": 12_000.0},
    {"date": "2026-06-05", "value": 18_000.0},
]
# The deposit (pocket outflow) lands between the mid and close points.
_FLOWS = [("2025-12-06", -5_000.0)]


def test_char_twr_frozen():
    # Sub-period 1: (12000 - 0)/10000 - 1 = 0.20
    # Sub-period 2: (18000 - 5000)/12000 - 1 = 13000/12000 - 1 = 0.083333…
    # Chain: 1.20 × 1.0833… - 1 = 0.30 exactly.
    out = r.twr(_SERIES, _FLOWS, annualize=False)
    assert out == {"period_return": 0.3, "annualized": 0.3}


def test_char_twr_annualized_frozen():
    # The window spans 365 days → annualized == cumulative for a 1Y window.
    out = r.twr(_SERIES, _FLOWS, annualize=True)
    assert out["period_return"] == pytest.approx(0.3)
    assert out["annualized"] == pytest.approx(0.3, abs=1e-3)


def test_char_xirr_frozen():
    # Cash-flow vector: -10000 @ open, -5000 @ mid+1d, +18000 @ close.
    # Frozen money-weighted return over the fixture (recompute if the solver changes).
    flows = [("2025-06-05", -10_000.0)] + _FLOWS
    rate = r.xirr(flows, 18_000.0, "2026-06-05")
    assert rate == pytest.approx(0.24304, abs=1e-5)


def test_char_portfolio_returns_frozen(tmp_path, monkeypatch, db_isolated):
    """End-to-end frozen figure flowing through portfolio_returns with all stores + FX isolated.

    Phase 4 change: portfolio_returns now sources the value series from
    portfolio_history.portfolio_value_series (daily, reconstructed) rather than
    ledger_service.net_worth_history. The test now injects via that primitive directly.

    The frozen TWR / MWR / period_gain figures are UNCHANGED: the math is the same, only the
    data path changed. test_char_twr_frozen and test_char_xirr_frozen above still pin the
    underlying functions; this test pins the end-to-end portfolio_returns() payload shape.

    lots is DB-backed (plan 4b task 2) — db_isolated gives LotStore() a fresh per-test
    database; seed via LotStore().bulk_insert(...) (verbatim rows, no _make_lot coercion),
    not a raw lots.json write.
    """
    from backend.db import price_history_store
    from backend.db.lot_store import LotStore
    from backend.services import portfolio_history as ph

    # One acquisition (the open 10k) + one mid deposit acquisition (5k), no disposals — so the
    # cash-flow vector matches _SERIES + _FLOWS without needing a proceeds event.
    LotStore().bulk_insert(
        [
            {
                "id": "L1",
                "position_id": "p1",
                "acquired_at": "2025-06-05",
                "original_qty": 100.0,
                "remaining_qty": 100.0,
                "unit_cost": 100.0,  # 100 × 100 = 10,000
                "cost_currency": "USD",
                "kind": "buy",
            },
            {
                "id": "L2",
                "position_id": "p1",
                "acquired_at": "2025-12-06",
                "original_qty": 50.0,
                "remaining_qty": 50.0,
                "unit_cost": 100.0,  # 50 × 100 = 5,000
                "cost_currency": "USD",
                "kind": "buy",
            },
        ],
    )
    monkeypatch.setattr(r, "base_currency", lambda: "USD")
    monkeypatch.setattr(r, "_load_rates_safe", lambda loader: {"USD": 1.0})

    # Phase 4: inject via portfolio_history.portfolio_value_series. The shape must include the
    # series points that the old net_worth_history provided plus the data_quality block.
    ph_result = {
        "series": [
            {"date": "2025-06-05", "value_base": 10_000.0, "classes": {"equity": 10_000.0}},
            {"date": "2025-12-05", "value_base": 12_000.0, "classes": {"equity": 12_000.0}},
            {"date": "2026-06-05", "value_base": 18_000.0, "classes": {"equity": 18_000.0}},
        ],
        "data_quality": {
            "market_since": "2025-01-01",
            "inception": "2025-06-05",
            "tiers": {},
            "has_unrecorded_risk": False,
            "has_unknown_cost": False,
            "fx_approximate": False,
        },
    }
    monkeypatch.setattr(ph, "portfolio_value_series", lambda *a, **k: ph_result)
    monkeypatch.setattr(ph, "inception_date", lambda *a, **k: "2025-06-05")
    monkeypatch.setattr(
        price_history_store.PriceHistoryStore, "earliest_date", lambda self: "2025-01-01"
    )

    from datetime import date

    out = r.portfolio_returns("1Y", today=date(2026, 6, 5))
    assert out["data_quality"]["sufficient"] is True
    assert out["twr"]["period_return"] == pytest.approx(0.3)
    # XIRR: the 1Y span is ≥ 360 days → MWR is present and annualized=True.
    # Vector: -10000 @ 2025-06-05 (opening), -5000 @ 2025-12-06 (L2 in-window), +18000 @ end.
    # This is the same vector as before → frozen 0.24304.
    assert out["mwr"] is not None
    assert out["mwr"]["xirr"] == pytest.approx(0.24304, abs=1e-5)
    # period $ gain = end − start + IN-WINDOW external flows = 18000 − 10000 + (−5000) = 3000.
    # L1 (acquired on the window start) is part of start_value (not an in-window flow);
    # only L2's mid-window 5000 deposit nets out.
    assert out["period_gain_base"] == pytest.approx(3_000.0)
    assert out["start_value"] == 10_000.0
    assert out["end_value"] == 18_000.0
    # Phase 4 additions: frozen values (inception=2025-06-05 == requested start → unclamped).
    assert out["effective_start"] == "2025-06-05"  # not clamped: inception <= requested_start
    assert out["label"] == "Last 1 year"  # label is standard (not "Since …")
    assert out["annualized"] is True  # 1Y span (365d) ≥ _SPAN_ANNUALIZE (360d)
    # data_quality: assert the full Phase 4 key set is present and frozen.
    dq = out["data_quality"]
    assert dq["inception"] == "2025-06-05"
    assert dq["effective_start"] == "2025-06-05"
    assert dq["has_unrecorded_risk"] is False
    assert dq["fx_approximate"] is False
    assert isinstance(dq["tiers"], dict)
