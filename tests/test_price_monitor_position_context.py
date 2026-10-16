"""_build_position_context sizes the WHOLE holding — incident 2026-10-16: a holding split across
accounts (a 1.46-share ESPP fraction listed first, beside broker and plan lots) was read as its
first row only, so the Analyst was told "0.0% of portfolio" and every narrative called a
quarter of the portfolio "a very small holding", right beside a live "25.82%" lead line."""

from __future__ import annotations

import backend.monitoring.price_monitor as pm
from backend.services import ledger


def _row(qty, cost, *, iid="inst_a", ticker="AAA", vesting="vested", price=10.0):
    return {
        "instrument_id": iid,
        "ticker": ticker,
        "quantity": qty,
        "price": price,
        "cost_basis": cost,
        "currency": "USD",
        "quote_currency": "USD",
        "quote_per_unit": 1.0,
        "vesting_state": vesting,
    }


def _ctx(monkeypatch, row, rows, price=10.0):
    monkeypatch.setattr(ledger, "base_currency", lambda: "USD")
    return pm._build_position_context("AAA", {"label": "Aaa"}, row, {}, rows, price)


def test_split_holding_is_sized_as_a_whole(monkeypatch):
    fraction, broker, plan = _row(1, 8.0), _row(60, 8.0), _row(39, 8.0)
    other = _row(300, 5.0, iid="inst_b", ticker="BBB")
    ctx = _ctx(monkeypatch, fraction, [fraction, broker, plan, other])

    assert "Position: 100 shares/units" in ctx
    assert "USD 1,000 (25.0% of portfolio)" in ctx
    assert "P&L: USD +200 (+25.0%)" in ctx


def test_unvested_rows_count_neither_in_the_holding_nor_the_denominator(monkeypatch):
    held = _row(100, 8.0)
    locked = _row(500, 0, vesting="unvested")
    other = _row(100, 5.0, iid="inst_b", ticker="BBB")
    ctx = _ctx(monkeypatch, held, [held, locked, other])

    assert "USD 1,000 (50.0% of portfolio)" in ctx


def test_one_unknown_cost_withholds_pnl_instead_of_inventing_it(monkeypatch):
    known, unknown = _row(50, 8.0), _row(50, 0)
    ctx = _ctx(monkeypatch, known, [known, unknown])

    assert "USD 1,000 (100.0% of portfolio)" in ctx
    assert "P&L" not in ctx
