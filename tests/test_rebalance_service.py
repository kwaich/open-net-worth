"""
Unit tests for the rebalancing service (P4-RB1).

Compute-only + offline: rows are passed in directly (no store), the profile target_allocation
is monkeypatched, and base==row currency so row_base_value is value-preserving (FX rate 1.0).
Covers: on-target → no trades; drifted → correct buy/sell amounts; cash_only mode; new-cash
full rebalance; no targets / empty book → clear status + zeros (never raises).

Run:
    .venv/bin/python3 -m pytest tests/test_rebalance_service.py -q
"""

from __future__ import annotations

import pytest

from backend.services import rebalance as rb


def _row(asset_class: str, value: float, base: str = "USD", vesting_state: str = "vested") -> dict:
    """A hydrated-shape row whose base value equals `value` (qty=value, price=1, native=base)."""
    return {
        "asset_class": asset_class,
        "quantity": value,
        "price": 1.0,
        "quote_per_unit": 1.0,
        "currency": base,
        "vesting_state": vesting_state,
    }


@pytest.fixture(autouse=True)
def _base(monkeypatch):
    monkeypatch.setattr(rb, "base_currency", lambda: "USD")


def _set_targets(monkeypatch, targets: dict):
    monkeypatch.setattr(rb, "_read_profile", lambda: {"target_allocation": targets})


# ── On-target → no trades ────────────────────────────────────────────────────


def test_on_target_no_trades(monkeypatch):
    _set_targets(monkeypatch, {"equity": 0.6, "crypto": 0.4})
    rows = [_row("equity", 6000), _row("crypto", 4000)]
    plan = rb.rebalance_plan(rows=rows)
    assert plan["status"] == "ok"
    assert plan["trades"] == []
    eq = next(a for a in plan["allocations"] if a["asset_class"] == "equity")
    assert eq["current_pct"] == 60.0
    assert eq["target_pct"] == 60.0
    assert eq["drift_pct"] == 0.0
    assert eq["off_target"] is False


# ── Drifted → correct sell/buy amounts ───────────────────────────────────────


def test_drifted_sell_to_target_amounts(monkeypatch):
    # 80/20 held vs 60/40 target on a 10k book → sell 2k equity, buy 2k crypto.
    _set_targets(monkeypatch, {"equity": 0.6, "crypto": 0.4})
    rows = [_row("equity", 8000), _row("crypto", 2000)]
    plan = rb.rebalance_plan(rows=rows)

    by_cls = {t["asset_class"]: t for t in plan["trades"]}
    assert by_cls["equity"]["direction"] == "sell"
    assert by_cls["equity"]["amount"] == 2000.0
    assert by_cls["crypto"]["direction"] == "buy"
    assert by_cls["crypto"]["amount"] == 2000.0
    eq = next(a for a in plan["allocations"] if a["asset_class"] == "equity")
    assert eq["drift_pct"] == 20.0
    assert eq["off_target"] is True


def test_partial_targets_are_renormalized(monkeypatch):
    # Only equity+crypto targeted at 30/10 → renormalize to 75/25 across them.
    _set_targets(monkeypatch, {"equity": 0.3, "crypto": 0.1})
    rows = [_row("equity", 5000), _row("crypto", 5000)]
    plan = rb.rebalance_plan(rows=rows)
    eq = next(a for a in plan["allocations"] if a["asset_class"] == "equity")
    assert eq["target_pct"] == 75.0  # 0.3/0.4
    cr = next(a for a in plan["allocations"] if a["asset_class"] == "crypto")
    assert cr["target_pct"] == 25.0


def test_percentages_accepted_as_targets(monkeypatch):
    # Targets given as 60/40 (percentages) are coerced to fractions.
    _set_targets(monkeypatch, {"equity": 60, "crypto": 40})
    rows = [_row("equity", 5000), _row("crypto", 5000)]
    plan = rb.rebalance_plan(rows=rows)
    eq = next(a for a in plan["allocations"] if a["asset_class"] == "equity")
    assert eq["target_pct"] == 60.0


# ── new_cash full rebalance ──────────────────────────────────────────────────


def test_new_cash_sell_to_target_scales_targets(monkeypatch):
    # 10k book + 2k new cash, 60/40 target on a 50/50 book → targets over 12k.
    _set_targets(monkeypatch, {"equity": 0.6, "crypto": 0.4})
    rows = [_row("equity", 5000), _row("crypto", 5000)]
    plan = rb.rebalance_plan(rows=rows, new_cash=2000)
    assert plan["target_total"] == 12000.0
    by_cls = {t["asset_class"]: t for t in plan["trades"]}
    # equity target 7200 − 5000 = 2200 buy; crypto target 4800 − 5000 = 200 sell.
    assert by_cls["equity"]["direction"] == "buy"
    assert by_cls["equity"]["amount"] == 2200.0
    assert by_cls["crypto"]["direction"] == "sell"
    assert by_cls["crypto"]["amount"] == 200.0


# ── cash_only mode (never sells) ─────────────────────────────────────────────


def test_cash_only_only_buys_into_underweight(monkeypatch):
    # 80/20 held, 60/40 target, +2k cash, cash_only → only a crypto buy, no equity sell.
    _set_targets(monkeypatch, {"equity": 0.6, "crypto": 0.4})
    rows = [_row("equity", 8000), _row("crypto", 2000)]
    plan = rb.rebalance_plan(rows=rows, new_cash=2000, mode="cash_only")
    assert all(t["direction"] == "buy" for t in plan["trades"])
    by_cls = {t["asset_class"]: t for t in plan["trades"]}
    assert "equity" not in by_cls  # overweight class is never sold
    assert by_cls["crypto"]["direction"] == "buy"
    # crypto target over 12k = 4800; shortfall 2800 capped at the 2k available.
    assert by_cls["crypto"]["amount"] == 2000.0


def test_cash_only_no_cash_no_trades(monkeypatch):
    _set_targets(monkeypatch, {"equity": 0.6, "crypto": 0.4})
    rows = [_row("equity", 8000), _row("crypto", 2000)]
    plan = rb.rebalance_plan(rows=rows, new_cash=0, mode="cash_only")
    assert plan["trades"] == []


def test_cash_only_splits_across_underweight_by_shortfall(monkeypatch):
    # Two underweight classes; 100 cash split in proportion to their shortfalls.
    _set_targets(monkeypatch, {"equity": 0.5, "crypto": 0.3, "bond": 0.2})
    rows = [_row("equity", 1000)]  # crypto + bond both at 0 → underweight
    plan = rb.rebalance_plan(rows=rows, new_cash=100, mode="cash_only")
    assert all(t["direction"] == "buy" for t in plan["trades"])
    total = sum(t["amount"] for t in plan["trades"])
    assert total == pytest.approx(100.0, abs=0.5)


# ── No targets / empty book → clear status, zeros, never 500 ──────────────────


def test_no_targets_status(monkeypatch):
    _set_targets(monkeypatch, {})
    rows = [_row("equity", 5000)]
    plan = rb.rebalance_plan(rows=rows)
    assert plan["status"] == "no_targets"
    assert plan["trades"] == []
    assert plan["allocations"] == []
    assert "message" in plan


def test_empty_book_status(monkeypatch):
    _set_targets(monkeypatch, {"equity": 0.6, "crypto": 0.4})
    plan = rb.rebalance_plan(rows=[])
    assert plan["status"] == "empty_book"
    assert plan["current_total"] == 0.0
    assert plan["trades"] == []


def test_cash_property_excluded_from_book(monkeypatch):
    # cash/property rows are not part of the rebalanced investment book.
    _set_targets(monkeypatch, {"equity": 1.0})
    rows = [_row("equity", 5000), _row("cash", 9999), _row("property", 9999)]
    plan = rb.rebalance_plan(rows=rows)
    assert plan["current_total"] == 5000.0
    assert plan["trades"] == []  # equity already 100% of the book → on target


def test_unvested_equity_grant_units_excluded_from_book(monkeypatch):
    """An unvested RSU tranche is not owned yet (ADR
    2026-07-31-unvested-equity-excluded-from-net-worth) — it is not part of the rebalanced
    investment book, same as cash/property. Left in, a huge unvested position would dominate
    `current_total` and suggest selling down a class that is actually on target."""
    _set_targets(monkeypatch, {"equity": 1.0})
    rows = [_row("equity", 5000), _row("equity", 999_999, vesting_state="unvested")]
    plan = rb.rebalance_plan(rows=rows)
    assert plan["current_total"] == 5000.0
    assert plan["trades"] == []  # equity already 100% of the (vested-only) book → on target


def test_held_untargeted_class_is_sold_to_zero(monkeypatch):
    # A class with holdings but no target → target 0% → full sell under sell_to_target.
    _set_targets(monkeypatch, {"equity": 1.0})
    rows = [_row("equity", 8000), _row("crypto", 2000)]
    plan = rb.rebalance_plan(rows=rows)
    by_cls = {t["asset_class"]: t for t in plan["trades"]}
    assert by_cls["crypto"]["direction"] == "sell"
    assert by_cls["crypto"]["amount"] == 2000.0


def test_invalid_mode_falls_back_to_sell_to_target(monkeypatch):
    _set_targets(monkeypatch, {"equity": 0.6, "crypto": 0.4})
    rows = [_row("equity", 8000), _row("crypto", 2000)]
    plan = rb.rebalance_plan(rows=rows, mode="nonsense")
    assert plan["mode"] == "sell_to_target"
    assert any(t["direction"] == "sell" for t in plan["trades"])
