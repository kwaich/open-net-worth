"""The Overview Cash flow card falls back to the newest month with money in it.

Persona QA round 7: a book with one imported September showed "0 income / 0 expense" for
October. Reports' Spending by category already says "September. Nothing spent in October yet.";
the Cash flow card now carries the same fallback, computed here so the browser never re-folds it.
"""

from __future__ import annotations

import pytest

from backend.services import planning_summary


@pytest.fixture
def ledger(monkeypatch):
    rows: list[dict] = []
    monkeypatch.setattr(planning_summary.ledger_service.transactions, "all", lambda: rows)
    monkeypatch.setattr(
        planning_summary.ledger_service, "txn_base_amount", lambda t, base, rates: t["amt"]
    )
    monkeypatch.setattr(planning_summary, "base_currency", lambda: "SGD")
    monkeypatch.setattr(planning_summary, "load_fx_cache", lambda: {})
    return rows


def _txn(date: str, kind: str, amt: float, **over) -> dict:
    return {"date": date, "kind": kind, "amt": amt, **over}


def test_a_month_with_activity_has_no_fallback(ledger):
    ledger += [_txn("2026-10-02", "income", 3000), _txn("2026-10-03", "expense", -120)]
    current, latest = planning_summary._cashflow("2026-10")
    assert (current["total_in"], current["total_out"], current["net"]) == (3000, 120, 2880)
    assert (current["in_count"], current["out_count"]) == (1, 1)
    assert latest is None


def test_an_empty_month_names_the_newest_earlier_month_with_money(ledger):
    ledger += [
        _txn("2026-08-10", "expense", -50),
        _txn("2026-09-05", "income", 2000),
        _txn("2026-09-20", "expense", -795),
        _txn("2026-09-21", "transfer", 400),
    ]
    current, latest = planning_summary._cashflow("2026-10")
    assert (current["in_count"], current["out_count"]) == (0, 0)
    assert latest is not None
    assert latest["month"] == "2026-09"
    assert (latest["total_in"], latest["total_out"], latest["net"]) == (2000, 795, 1205)
    assert (latest["in_count"], latest["out_count"]) == (1, 1)


def test_deleted_rows_and_a_book_with_nothing_in_the_window_give_no_fallback(ledger):
    ledger += [_txn("2026-09-05", "income", 2000, deleted=True), _txn("2026-01-05", "income", 9)]
    current, latest = planning_summary._cashflow("2026-10")
    assert (current["in_count"], current["out_count"]) == (0, 0)
    assert latest is None


def test_the_lookback_crosses_a_year_boundary(ledger):
    ledger += [_txn("2025-12-24", "expense", -60)]
    _, latest = planning_summary._cashflow("2026-02")
    assert latest is not None and latest["month"] == "2025-12"
