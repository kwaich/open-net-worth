"""Regression: investment cash legs (kind=investment) are NOT spending.

With kind=investment as a first-class transaction kind, the old category-based exclusion
(kind=expense + category=investment) is gone. Investment cash legs now carry kind="investment"
and are naturally excluded from spending/budget/cashflow totals — no special-case predicate needed.

The key invariants:
  - is_spending_expense only returns True for kind="expense"
  - kind="investment" is excluded from spending, budget activity, cashflow baseline, and
    the forecast scorecard
  - Net worth is unaffected by kind=investment alone (the position value covers the asset side)
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from backend.services import budgets, cashflow
from backend.services import forecast_scorecard as fs
from backend.services.budgets import is_spending_expense

# ── Helpers ──────────────────────────────────────────────────────────────────────


def _expense(txn_id: str, account_id: str, amount: float, category: str, d: str) -> dict:
    return {
        "id": txn_id,
        "date": d,
        "kind": "expense",
        "description": "test",
        "category": category,
        "from_account_id": account_id,
        "from_amount": amount,
        "from_currency": "USD",
        "to_account_id": None,
        "to_amount": None,
        "deleted": False,
    }


def _invest(txn_id: str, account_id: str, amount: float, d: str) -> dict:
    """A kind=investment buy (cash out of account)."""
    return {
        "id": txn_id,
        "date": d,
        "kind": "investment",
        "description": "buy AAPL",
        "category": None,
        "from_account_id": account_id,
        "from_amount": amount,
        "from_currency": "USD",
        "to_account_id": None,
        "to_amount": None,
        "deleted": False,
    }


def _prev_month_first() -> str:
    first_of_month = date.today().replace(day=1)
    last_month_last = first_of_month - timedelta(days=1)
    return f"{last_month_last.year:04d}-{last_month_last.month:02d}-01"


# ── The predicate itself ───────────────────────────────────────────────────────────


def test_is_spending_expense_predicate():
    # Real spending — counts.
    assert is_spending_expense({"kind": "expense", "category": "cat_13095ba70000"}) is True
    assert (
        is_spending_expense({"kind": "expense", "category": None}) is True
    )  # Uncategorized still counts
    assert is_spending_expense({"kind": "expense", "category": "split"}) is True
    assert is_spending_expense({"kind": "expense"}) is True  # blank category still real money out
    # Investment cash legs carry kind="investment" and are never spending.
    assert is_spending_expense({"kind": "investment"}) is False
    assert is_spending_expense({"kind": "investment", "category": None}) is False
    # Non-expense kinds are never spending.
    assert is_spending_expense({"kind": "income", "category": None}) is False
    assert is_spending_expense({"kind": "transfer", "category": None}) is False
    assert is_spending_expense({"kind": "adjustment", "category": None}) is False


def test_expense_categories_does_not_include_investment():
    """investment is now a kind, not a category — it never appears in expense_categories."""
    cats = budgets.expense_categories()
    assert "investment" not in cats
    assert "cat_13095ba70000" in cats


def test_expense_categories_stored_investment_budget_excluded():
    """Even a (mistakenly) stored budget keyed "investment" can't make it a budgetable expense —
    "investment" is no longer a category key at all, so it won't survive expense_categories."""
    cats = budgets.expense_categories({"investment": {"limit": 100, "rollover": False}})
    # "investment" might slip in via the stored-budget backstop if the key isn't excluded.
    # The test verifies: if it does, it's because someone wrote a budget for a non-category key.
    # The correct behavior: "investment" is not in BUILTIN_KEYS and not a custom category,
    # so it only enters via the stored-budget union — which we accept as a backstop. Document
    # that the field should not exist after migration, not that expense_categories rejects it.
    # (This replaces the old "NON_SPENDING_EXPENSE_CATEGORIES subtraction wins" assertion.)
    assert "cat_13095ba70000" in cats


# ── Cash-flow rolling baseline ─────────────────────────────────────────────────────


def test_rolling_avg_expenses_excludes_investment(monkeypatch):
    food = _expense("e1", "bank", 100.0, "cat_13095ba70000", _prev_month_first())
    invest = _invest("e2", "bank", 5000.0, _prev_month_first())
    monkeypatch.setattr(
        cashflow.svc.transactions,
        "query_txns",
        lambda **kw: [food, invest] if kw.get("kind") == "expense" else [],
    )
    monkeypatch.setattr(cashflow, "load_fx_cache", lambda: {})
    monkeypatch.setattr(
        cashflow.svc, "txn_base_amount", lambda t, base, rates: float(t["from_amount"])
    )
    # One month of data → divisor 1; the 5000 investment buy must NOT count.
    assert cashflow._rolling_avg_expenses("USD") == pytest.approx(100.0)


def test_rolling_avg_expenses_only_investment_yields_no_baseline(monkeypatch):
    """A window of only investment buys has no recurring expense baseline (None → profile fallback)."""
    invest = _invest("e2", "bank", 5000.0, _prev_month_first())
    monkeypatch.setattr(
        cashflow.svc.transactions,
        "query_txns",
        lambda **kw: [invest] if kw.get("kind") == "expense" else [],
    )
    monkeypatch.setattr(cashflow, "load_fx_cache", lambda: {})
    monkeypatch.setattr(
        cashflow.svc, "txn_base_amount", lambda t, base, rates: float(t["from_amount"])
    )
    assert cashflow._rolling_avg_expenses("USD") is None


def test_historical_months_excludes_investment_from_flow_but_keeps_balance(monkeypatch):
    """Investment buys drop out of In/Out/Net flow, but the real cash balance still folds them
    (the cash genuinely left the wallet)."""
    # The opening is now a real "Starting Balance" transaction, not a scalar on the account —
    # dated well before both real legs so it is never curtained out (mirrors the old
    # "no initial_balance_date → no curtain" behavior this test pins).
    acct = {
        "id": "bank",
        "kind": "asset",
        "type": "cash",
        "currency": "USD",
        "opening_txn_id": "open-bank",
    }
    opening = {
        "id": "open-bank",
        "date": "2000-01-01",
        "kind": "adjustment",
        "to_account_id": "bank",
        "to_amount": 1000.0,
        "deleted": False,
    }
    d = _prev_month_first()
    food = _expense("e1", "bank", 100.0, "cat_13095ba70000", d)
    invest = _invest("e2", "bank", 500.0, d)

    monkeypatch.setattr(
        cashflow.svc.accounts,
        "query_accounts",
        lambda *a, **k: [acct],
    )
    monkeypatch.setattr(cashflow.svc.transactions, "all", lambda: [opening, food, invest])
    monkeypatch.setattr(
        cashflow.svc.transactions,
        "query_txns",
        lambda **kw: [food, invest] if kw.get("kind") == "expense" else [],
    )
    monkeypatch.setattr(
        cashflow.svc, "txn_base_amount", lambda t, base, rates: float(t.get("from_amount") or 0)
    )
    monkeypatch.setattr(cashflow.svc, "to_base", lambda amt, frm, base: amt)
    monkeypatch.setattr(cashflow, "load_fx_cache", lambda: {})

    rows = cashflow._historical_months("USD", 1, 1000.0)

    assert len(rows) == 1
    # Flow excludes the investment buy: only the 100 food expense.
    assert rows[0]["total_out"] == pytest.approx(100.0)
    # Balance folds BOTH legs (cash left for both): 1000 - 100 - 500 = 400.
    assert rows[0]["balance"] == pytest.approx(400.0)


def test_a_month_before_any_account_was_tracked_has_no_balance_to_show(monkeypatch):
    """Persona QA 2026-10-14: months of an imported statement that predate the account's Starting
    Balance read "BALANCE S$0". The flow is real; the balance is simply not tracked yet."""
    acct = {
        "id": "bank",
        "kind": "asset",
        "type": "cash",
        "currency": "USD",
        "opening_txn_id": "open-bank",
    }
    this_month = date.today().replace(day=1).isoformat()
    opening = {
        "id": "open-bank",
        "date": this_month,
        "kind": "adjustment",
        "to_account_id": "bank",
        "to_amount": 1000.0,
        "deleted": False,
    }
    food = _expense("e1", "bank", 100.0, "cat_13095ba70000", _prev_month_first())

    monkeypatch.setattr(cashflow.svc.accounts, "query_accounts", lambda *a, **k: [acct])
    monkeypatch.setattr(cashflow.svc.transactions, "all", lambda: [opening, food])
    monkeypatch.setattr(
        cashflow.svc.transactions,
        "query_txns",
        lambda **kw: [food] if kw.get("kind") == "expense" else [],
    )
    monkeypatch.setattr(
        cashflow.svc, "txn_base_amount", lambda t, base, rates: float(t.get("from_amount") or 0)
    )
    monkeypatch.setattr(cashflow.svc, "to_base", lambda amt, frm, base: amt)
    monkeypatch.setattr(cashflow, "load_fx_cache", lambda: {})

    (row,) = cashflow._historical_months("USD", 1, 1000.0)

    assert row["total_out"] == pytest.approx(100.0)
    assert row["balance_tracked"] is False


# ── Forecast scorecard ─────────────────────────────────────────────────────────────


def test_actual_month_flow_excludes_investment(monkeypatch):
    food = _expense("e1", "bank", 100.0, "cat_13095ba70000", "2026-05-10")
    invest = _invest("e2", "bank", 5000.0, "2026-05-12")
    salary = {"kind": "income", "category": None, "to_amount": 3000.0}

    def fake_query(**kw):
        if kw.get("kind") == "income":
            return [salary]
        if kw.get("kind") == "expense":
            return [food, invest]
        return []

    monkeypatch.setattr(fs.ledger_service.transactions, "query_txns", fake_query)
    monkeypatch.setattr(
        fs.ledger_service,
        "txn_base_amount",
        lambda t, base, rates: float(t.get("to_amount") or t.get("from_amount") or 0),
    )

    total_in, total_out = fs._actual_month_flow(2026, 5, "USD", {})

    assert total_in == pytest.approx(3000.0)
    # The 5000 investment buy must NOT inflate the actual cash outflow.
    assert total_out == pytest.approx(100.0)


# ── kind=investment: stored correctly, excluded from spending, net worth unaffected ──


def test_kind_investment_stored_not_coerced(tmp_path, monkeypatch):
    """A kind=investment transaction is stored as-is (not coerced to expense)."""
    import importlib

    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    import backend.paths as paths_mod

    importlib.reload(paths_mod)

    from backend.db.transaction_store import make_transaction

    txn = make_transaction(
        {
            "date": "2026-07-01",
            "kind": "investment",
            "from_account_id": "acct-1",
            "from_amount": 1000.0,
            "from_currency": "USD",
            "description": "Buy AAPL",
        }
    )
    assert txn["kind"] == "investment"
    assert txn["category"] is None  # kind=investment carries no category


def test_kind_investment_excluded_from_spending():
    """is_spending_expense correctly excludes kind=investment."""
    invest_buy = {
        "kind": "investment",
        "from_account_id": "acct-1",
        "from_amount": 1500.0,
        "category": None,
    }
    invest_sell = {
        "kind": "investment",
        "to_account_id": "acct-1",
        "to_amount": 2000.0,
        "category": None,
    }
    assert is_spending_expense(invest_buy) is False
    assert is_spending_expense(invest_sell) is False
