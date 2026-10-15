"""Regression tests for two Cash Flow sub-tab bugs found on 2026-07-07:

1. Actual-transaction events carried the transaction's raw OPAQUE category id
   (e.g. ``cat_abc123``) straight through to the UI instead of resolving it to
   its human label — `cashflow.py`'s ``_historical_months`` never went through
   `CategoryStore`, unlike every other category-label site in the codebase.

2. The in-progress ("TODAY") month never looked at the month's real
   transactions at all — it used the flat rolling-average income/expense
   baseline like any future month, which both hid real events that already
   posted this month AND double-counted them in the ending balance (the
   starting `liquid_balance_base` is TODAY's real cash position, which
   already reflects the elapsed days' actual flow; adding a full month's
   average net on top double-counts those days).

Fix: `_category_label()` resolves the opaque id via `CategoryStore` (mirrors
`services.planning._category_label`); `build_projection`'s current-month
iteration folds real day-1-through-today transactions into `events` and
prorates the recurring baseline over the remaining days only, with `balance`
adjusted so the elapsed actual flow isn't double-counted.
"""

from __future__ import annotations

from datetime import date

import pytest

from backend.services import cashflow

# ---------------------------------------------------------------------------
# _category_label
# ---------------------------------------------------------------------------


class _FakeCategoryStore:
    _ROWS = {"cat_abc123": {"label": "Groceries"}}

    def get(self, cat_id):
        return self._ROWS.get(cat_id)

    def all(self):
        return [{"id": cid, **row} for cid, row in self._ROWS.items()]


def test_category_label_resolves_known_id(monkeypatch):
    import backend.db.category_store as cat_store_mod

    monkeypatch.setattr(cat_store_mod, "CategoryStore", _FakeCategoryStore)
    assert cashflow._category_label("cat_abc123") == "Groceries"


def test_category_label_falls_back_for_unknown_id(monkeypatch):
    import backend.db.category_store as cat_store_mod

    monkeypatch.setattr(cat_store_mod, "CategoryStore", _FakeCategoryStore)
    assert cashflow._category_label("cat_unknown_id") == "Cat Unknown Id"


# ---------------------------------------------------------------------------
# _historical_months no longer leaks the raw opaque category id
# ---------------------------------------------------------------------------


def test_historical_months_resolves_category_label_not_raw_id(monkeypatch):
    """Before the fix, ``event["category"]`` was the raw ``cat_abc123`` id (matching
    the screenshot's ``CAT_188BBF1E8E48``-style rows). It must now be the label."""
    import backend.db.category_store as cat_store_mod

    monkeypatch.setattr(cat_store_mod, "CategoryStore", _FakeCategoryStore)

    acct = {
        "id": "bank",
        "kind": "asset",
        "type": "cash",
        "currency": "USD",
        "initial_balance": 0.0,
    }
    txn = {
        "id": "t1",
        "date": "2026-06-15",
        "kind": "expense",
        "description": "Whole Foods",
        "category": "cat_abc123",
        "from_account_id": "bank",
        "from_amount": 50.0,
        "deleted": False,
    }

    monkeypatch.setattr(cashflow.svc.accounts, "query_accounts", lambda **kw: [acct])
    monkeypatch.setattr(cashflow.svc.transactions, "all", lambda: [txn])
    monkeypatch.setattr(
        cashflow.svc.transactions,
        "query_txns",
        lambda **kw: [txn] if kw.get("kind") == "expense" else [],
    )
    monkeypatch.setattr(cashflow.svc, "txn_base_amount", lambda t, base, rates: t["from_amount"])
    monkeypatch.setattr(cashflow.svc, "to_base", lambda amt, frm, base, rates=None: amt)
    monkeypatch.setattr(cashflow, "load_fx_cache", lambda: {})

    class _FixedDate(date):
        @classmethod
        def today(cls):
            return date(2026, 7, 7)

    monkeypatch.setattr(cashflow, "user_today", _FixedDate.today)

    rows = cashflow._historical_months("USD", 1, 1000.0)

    assert len(rows) == 1
    [event] = rows[0]["events"]
    assert event["category"] == "Groceries"


# ---------------------------------------------------------------------------
# Current-month hybrid: actual-to-date events + prorated remainder, no double count
# ---------------------------------------------------------------------------


@pytest.fixture
def _pinned_july_7(monkeypatch):
    """Pin 'today' to 2026-07-07 (24 days remaining in a 31-day July)."""

    class _FixedDate(date):
        @classmethod
        def today(cls):
            return date(2026, 7, 7)

    monkeypatch.setattr(cashflow, "user_today", _FixedDate.today)
    return date(2026, 7, 7)


def _patch_projection_inputs(monkeypatch, *, income_txns, expense_txns):
    monkeypatch.setattr(cashflow, "cash_balance_base", lambda base, *_a, **_k: 10_000.0)
    monkeypatch.setattr(cashflow, "fixed_deposit_base", lambda base, *_a, **_k: 0.0)
    monkeypatch.setattr(cashflow, "investments_runway_base", lambda base, *_a, **_k: 0.0)
    monkeypatch.setattr(cashflow, "_rolling_avg_income", lambda base, *_a, **_k: 3_000.0)
    monkeypatch.setattr(cashflow, "_rolling_avg_expenses", lambda base, *_a, **_k: 1_000.0)
    monkeypatch.setattr(cashflow, "_load_profile", lambda: {})

    # build_projection loads the ledger ONCE and filters the loaded list in memory
    # (_filter_txns over all_txns — the 2026-08-12 single-read fix), so the actuals are
    # fed through transactions.all() with an explicit kind, not through query_txns.
    all_txns = [{**t, "kind": "income"} for t in income_txns] + [
        {**t, "kind": "expense"} for t in expense_txns
    ]
    monkeypatch.setattr(cashflow.svc.transactions, "all", lambda: list(all_txns))
    monkeypatch.setattr(cashflow.svc, "txn_base_amount", lambda t, base, rates: t["amount_base"])
    monkeypatch.setattr(cashflow, "load_fx_cache", lambda: {})
    monkeypatch.setattr(cashflow, "is_spending_expense", lambda t: True)

    # Stream classification (.claude/rfcs/2026-07-27-cashflow-stream-model.md) is what the
    # projection now extrapolates, so stub it rather than the retired rolling averages: no
    # detected streams, and the same flat 3,000/1,000 as the VARIABLE baseline. Proration —
    # what these tests are actually about — applies to it identically.
    from backend.services import recurring_baseline

    monkeypatch.setattr(
        recurring_baseline,
        "resolve_baseline",
        lambda base, rates, today=None, all_txns=None: {
            "streams": [],
            "candidates": [],
            "variable_in": 3_000.0,
            "variable_out": 1_000.0,
            "one_offs": [],
            "detection_window": {"from": "", "to": ""},
            "residual_window": {"from": "", "to": ""},
        },
    )


def test_current_month_surfaces_actual_transactions_as_events(monkeypatch, _pinned_july_7):
    income_txns = [
        {"description": "Bonus", "category": None, "amount_base": 500.0, "date": "2026-07-03"}
    ]
    expense_txns = [
        {"description": "Rent", "category": None, "amount_base": 1_200.0, "date": "2026-07-01"}
    ]
    _patch_projection_inputs(monkeypatch, income_txns=income_txns, expense_txns=expense_txns)

    result = cashflow.build_projection(months=1, history_months=0)
    [current] = result["months"]

    assert current["is_current"] is True
    actual = {e["label"]: e for e in current["events"] if e["status"] == "actual"}
    assert actual.keys() == {"Bonus", "Rent"}
    assert actual["Bonus"]["amount_base"] == pytest.approx(500.0)
    assert actual["Rent"]["amount_base"] == pytest.approx(1_200.0)


def test_current_month_recurring_baseline_is_prorated_to_remaining_days(
    monkeypatch, _pinned_july_7
):
    _patch_projection_inputs(monkeypatch, income_txns=[], expense_txns=[])

    result = cashflow.build_projection(months=1, history_months=0)
    [current] = result["months"]

    recurring = {e["label"]: e for e in current["events"] if e["status"] == "recurring"}
    # 24 of 31 days remain after Jul 7 — the recurring baseline must be scaled down,
    # not the full monthly average (which the bug used unconditionally).
    expected_income = 3_000.0 * 24 / 31
    expected_expenses = 1_000.0 * 24 / 31
    # Labels are the classified ones now — the flat remainder is the VARIABLE bucket
    # ("everyday spending"), not a pretend single "Salary"/"Living expenses" line.
    assert recurring["Irregular income"]["amount_base"] == pytest.approx(expected_income, rel=1e-3)
    assert recurring["Everyday spending"]["amount_base"] == pytest.approx(
        expected_expenses, rel=1e-3
    )
    assert recurring["Irregular income"]["amount_base"] < 3_000.0
    assert recurring["Everyday spending"]["amount_base"] < 1_000.0


def test_current_month_balance_does_not_double_count_elapsed_actuals(monkeypatch, _pinned_july_7):
    """The starting liquid balance (10,000) is TODAY's REAL cash position, which already
    reflects the Rent/Bonus flow below. The buggy version added the full month's average
    net (3000 - 1000 = +2000) on top, landing on 12,000; the fix must add only the
    prorated remainder's net, landing well short of that."""
    income_txns = [
        {"description": "Bonus", "category": None, "amount_base": 500.0, "date": "2026-07-03"}
    ]
    expense_txns = [
        {"description": "Rent", "category": None, "amount_base": 1_200.0, "date": "2026-07-01"}
    ]
    _patch_projection_inputs(monkeypatch, income_txns=income_txns, expense_txns=expense_txns)

    result = cashflow.build_projection(months=1, history_months=0)
    [current] = result["months"]

    prorated_net = (3_000.0 * 24 / 31) - (1_000.0 * 24 / 31)
    expected_balance = 10_000.0 + prorated_net
    assert current["balance"] == pytest.approx(expected_balance, rel=1e-3)
    assert current["balance"] != pytest.approx(12_000.0, rel=1e-3)


def test_current_month_no_remaining_days_skips_recurring_event(monkeypatch):
    """On the last day of the month, the recurring remainder is zero — the month is
    entirely actuals, so no synthetic recurring event should appear."""

    class _LastDayOfJuly(date):
        @classmethod
        def today(cls):
            return date(2026, 7, 31)

    monkeypatch.setattr(cashflow, "user_today", _LastDayOfJuly.today)
    _patch_projection_inputs(monkeypatch, income_txns=[], expense_txns=[])

    result = cashflow.build_projection(months=1, history_months=0)
    [current] = result["months"]

    assert [e for e in current["events"] if e["status"] == "recurring"] == []
    assert current["balance"] == pytest.approx(10_000.0)
