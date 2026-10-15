"""Regression test: soft-deleted transactions must not affect historical cash-flow balances.

Before the fix, `_historical_months` called `svc.transactions.all()` which includes rows
where ``deleted=True``, causing ``account_balance_as_of`` to fold those deleted legs and
produce wrong historical "Real cash balance" bars.

After the fix: `all_txns = [t for t in svc.transactions.all() if not t.get("deleted")]`.

This test FAILS before the fix and PASSES after.
"""

from __future__ import annotations

from calendar import monthrange
from datetime import date, timedelta

import pytest

from backend.services import cashflow

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_txn(
    txn_id: str, account_id: str, amount: float, kind: str, d: str, deleted: bool = False
) -> dict:
    """Minimal two-leg transaction fixture."""
    return {
        "id": txn_id,
        "date": d,
        "kind": kind,
        "description": "test",
        "category": "food",
        "from_account_id": account_id if kind == "expense" else None,
        "from_amount": amount if kind == "expense" else None,
        "to_account_id": account_id if kind == "income" else None,
        "to_amount": amount if kind == "income" else None,
        "deleted": deleted,
    }


def _opening_txn(account_id: str, amount: float, d: str = "2000-01-01") -> dict:
    """A Starting Balance row seeding `account_id`'s fold, dated in the deep past. The new
    opening shape moved the seed off the account (`initial_balance` is no longer stored) and
    onto a real transaction — these fixtures don't bother wiring `opening_txn_id` on the
    account dict since none of these tests need an actual tracking-start CUTOFF (opening_date
    stays None below, so is_before_tracking_start never excludes anything — identical to
    every non-deleted leg, including this one, simply folding in from the start)."""
    return {
        "id": f"open_{account_id}",
        "date": d,
        "kind": "adjustment",
        "description": "Starting Balance",
        "category": None,
        "from_account_id": None,
        "from_amount": None,
        "to_account_id": account_id,
        "to_amount": amount,
        "to_currency": "USD",
        "deleted": False,
    }


def _prev_month_str() -> str:
    """Return YYYY-MM-DD for the first day of last month."""
    today = date.today()
    first_of_month = today.replace(day=1)
    last_month_last = first_of_month - timedelta(days=1)
    return last_month_last.strftime("%Y-%m-%d").rsplit("-", 1)[0] + "-01"


def _prev_month_end() -> str:
    """Return YYYY-MM-DD for the last day of last month."""
    today = date.today()
    first_of_month = today.replace(day=1)
    last_month_last = first_of_month - timedelta(days=1)
    y, m = last_month_last.year, last_month_last.month
    last_day = monthrange(y, m)[1]
    return f"{y:04d}-{m:02d}-{last_day:02d}"


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_deleted_txn_excluded_from_historical_balance(monkeypatch):
    """A soft-deleted transaction must NOT change account_balance_as_of result."""
    acct = {"id": "bank", "kind": "asset", "type": "cash", "currency": "USD"}
    opening = _opening_txn("bank", 1000.0)

    txn_date = _prev_month_str()
    live_txn = _make_txn("t1", "bank", 200.0, "income", txn_date, deleted=False)
    deleted_txn = _make_txn("t2", "bank", 500.0, "income", txn_date, deleted=True)

    all_txns_with_deleted = [opening, live_txn, deleted_txn]
    all_txns_without = [opening, live_txn]

    from backend.services.ledger import account_balance_as_of

    bal_with = account_balance_as_of(
        acct, _prev_month_end(), all_txns_with_deleted, opening["date"]
    )
    bal_without = account_balance_as_of(acct, _prev_month_end(), all_txns_without, opening["date"])

    # The deleted txn SHOULD NOT contribute to the balance.
    # bal_without = 1000 + 200 = 1200; bal_with must also be 1200 (deleted leg excluded).
    assert bal_without == pytest.approx(1200.0)
    assert bal_with == pytest.approx(1200.0), (
        "Deleted transaction must not affect account_balance_as_of; "
        f"expected 1200.0 but got {bal_with}"
    )


def test_historical_months_excludes_deleted_transactions(monkeypatch):
    """_historical_months passes only non-deleted txns to account_balance_as_of.

    With the fix, the deleted income txn of 500 does not inflate the month-end balance.
    """
    acct = {"id": "bank", "kind": "asset", "type": "cash", "currency": "USD"}
    opening = _opening_txn("bank", 1000.0)

    txn_date = _prev_month_str()
    live_txn = _make_txn("t1", "bank", 200.0, "income", txn_date, deleted=False)
    deleted_txn = _make_txn("t2", "bank", 500.0, "income", txn_date, deleted=True)

    monkeypatch.setattr(cashflow.svc.accounts, "query_accounts", lambda **kw: [acct])
    monkeypatch.setattr(cashflow.svc.transactions, "all", lambda: [opening, live_txn, deleted_txn])
    monkeypatch.setattr(
        cashflow.svc.transactions,
        "query_txns",
        lambda **kw: [live_txn] if kw.get("kind") == "income" else [],
    )
    monkeypatch.setattr(
        cashflow.svc,
        "txn_base_amount",
        lambda t, base, rates: float(t.get("to_amount") or t.get("from_amount") or 0),
    )
    monkeypatch.setattr(cashflow.svc, "to_base", lambda amt, frm, base: amt)
    monkeypatch.setattr(cashflow, "load_fx_cache", lambda: {})

    rows = cashflow._historical_months("USD", 1, 1000.0)

    assert len(rows) == 1
    # initial_balance(1000) + live_income(200) = 1200; deleted income(500) must NOT add.
    assert rows[0]["balance"] == pytest.approx(1200.0), (
        f"Expected 1200.0 but got {rows[0]['balance']}; "
        "the deleted txn likely leaked into account_balance_as_of."
    )


def test_historical_months_no_deletes_unchanged(monkeypatch):
    """When no deletions exist, the historical balance is exactly as expected (no regression)."""
    acct = {"id": "bank", "kind": "asset", "type": "cash", "currency": "USD"}
    opening = _opening_txn("bank", 500.0)

    txn_date = _prev_month_str()
    live_txn = _make_txn("t1", "bank", 300.0, "income", txn_date, deleted=False)

    monkeypatch.setattr(cashflow.svc.accounts, "query_accounts", lambda **kw: [acct])
    monkeypatch.setattr(cashflow.svc.transactions, "all", lambda: [opening, live_txn])
    monkeypatch.setattr(
        cashflow.svc.transactions,
        "query_txns",
        lambda **kw: [live_txn] if kw.get("kind") == "income" else [],
    )
    monkeypatch.setattr(
        cashflow.svc,
        "txn_base_amount",
        lambda t, base, rates: float(t.get("to_amount") or t.get("from_amount") or 0),
    )
    monkeypatch.setattr(cashflow.svc, "to_base", lambda amt, frm, base: amt)
    monkeypatch.setattr(cashflow, "load_fx_cache", lambda: {})

    rows = cashflow._historical_months("USD", 1, 1000.0)

    assert len(rows) == 1
    # initial_balance(500) + income(300) = 800
    assert rows[0]["balance"] == pytest.approx(800.0)
