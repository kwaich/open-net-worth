"""Regression tests for three foundation money-math correctness fixes.

F1 — reconcile() bypasses the create_transaction() choke point
     → the adjustment must appear in the audit log as a "create" event.

F2 — account_balance_as_of() doesn't filter deleted transactions
     → a deleted leg must not affect the as-of balance.

F3 — get_transactions() totals skip rows with stored base_amount=None
     → income/expense rows with base_amount=None must be recomputed and
       included in total_income_base/total_expenses_base; the totals must
       equal get_ledger_summary() for the same period.

All tests are offline (monkeypatched stores; no network, no LLM, no live data).
"""

from __future__ import annotations

import pytest

from backend.services import ledger as svc

# ── F1: reconcile() must route the adjustment through create_transaction() ────


@pytest.fixture
def reconcile_env(tmp_path, monkeypatch, db_isolated):
    """Isolated stores for the reconcile audit test.

    Sets up accounts + transactions in tmp_path, redirects the audit store to
    tmp_path as well, and stubs rules/FX to avoid side-effects. accounts/transactions/
    txn_audit are DB-backed now (plan 3 task 4) — db_isolated redirects the process-wide
    DB singleton, so the fresh AccountStore()/TransactionStore() instances below (and
    services.audit's own TransactionAuditStore instance) resolve against it with no
    per-instance path-sync needed.
    """
    import backend.services.fx_cache as fx_mod
    from backend.db.account_store import AccountStore
    from backend.db.transaction_store import TransactionStore
    from backend.services import accounts as accounts_service
    from backend.services import audit

    acc_store = AccountStore()
    txn_store = TransactionStore()
    monkeypatch.setattr(svc, "accounts", acc_store)
    monkeypatch.setattr(svc, "transactions", txn_store)
    monkeypatch.setattr(svc, "base_currency", lambda: "SGD")
    monkeypatch.setattr(svc, "to_base", lambda amt, cur, base=None, rates=None: round(amt or 0, 2))
    monkeypatch.setattr(fx_mod, "load_fx_cache", lambda: {"SGD": 1.0})

    # accounts_service.create_account (not the raw store's .create()) mints the account AND
    # its Starting Balance row — the opening is now a transaction, not two account fields —
    # and reads ledger.accounts/ledger.transactions dynamically, so it picks up the
    # tmp_path-scoped stores patched above.
    acct = accounts_service.create_account(
        {
            "name": "Meridian Bank",
            "kind": "asset",
            "type": "cash",
            "currency": "SGD",
            "initial_balance": 100.0,
            "initial_balance_date": "2026-01-01",
        }
    )
    return acct, acc_store, txn_store, audit


def test_reconcile_adjustment_emits_create_audit_event(reconcile_env):
    """F1 regression: reconcile() must route the adjustment through create_transaction()
    so that every reconciliation mismatch produces a 'create' audit event.

    Before the fix, reconcile() called transactions.append(data) directly, bypassing
    the choke point and leaving the audit log empty for reconcile adjustments.
    """
    acct, _, txn_store, audit = reconcile_env

    # Seed a cleared expense so the cleared balance is 70 (100 − 30).
    txn_store.append(
        {
            "date": "2026-02-01",
            "kind": "expense",
            "from_account_id": acct["id"],
            "from_amount": 30.0,
            "from_currency": "SGD",
            "cleared": True,
        }
    )

    # Assert a real balance of 60 → delta = −10 → an adjustment must be written.
    adj = svc.reconcile(acct["id"], 60.0, "2026-03-01")

    assert adj is not None, "reconcile must return the adjustment txn"
    assert adj["kind"] == "adjustment"

    # F1 regression: the adjustment must appear in the audit log.
    events = audit._audit.for_txn(adj["id"])
    assert any(e.get("op") == "create" for e in events), (
        "reconcile adjustment must emit a 'create' audit event via create_transaction(); "
        "before the fix transactions.append() bypassed the choke point and produced no event"
    )


# ── F2: account_balance_as_of() must skip deleted legs ────────────────────────

_ACCT_AS_OF = {"id": "acc-asof-1", "currency": "USD", "opening_txn_id": "open-asof-1"}
_OPENING_DATE_AS_OF = "2026-01-01"
_OPENING_ROW_AS_OF = {
    "id": "open-asof-1",
    "date": _OPENING_DATE_AS_OF,
    "to_account_id": "acc-asof-1",
    "to_amount": 500.0,
}


def test_account_balance_as_of_skips_deleted_legs():
    """F2 regression: a deleted leg must not affect account_balance_as_of().

    Before the fix the fold had no deleted-row guard, so a soft-deleted
    leg inside the date window changed the computed as-of balance.
    Expected: 500 (opening) + 200 (live to-leg) = 700.
    The deleted +999 to-leg must be ignored.
    """
    txns = [
        _OPENING_ROW_AS_OF,
        {
            "date": "2026-02-01",
            "to_account_id": "acc-asof-1",
            "to_amount": 200.0,
            "deleted": False,
        },
        {
            "date": "2026-02-15",
            "to_account_id": "acc-asof-1",
            "to_amount": 999.0,
            "deleted": True,  # soft-deleted — must not count
        },
    ]
    result = svc.account_balance_as_of(_ACCT_AS_OF, "2026-03-01", txns, _OPENING_DATE_AS_OF)
    assert result == 700.0, (
        f"account_balance_as_of returned {result} but deleted leg must be excluded; "
        "expected 700.0 (opening 500 + live 200)"
    )


# ── F3: get_transactions() totals must recompute base_amount=None rows ─────────


@pytest.fixture
def ledger_env_f3(tmp_path, monkeypatch, db_isolated):
    """Isolated ledger stores for the base_amount=None recompute test.

    accounts/transactions are DB-backed now (plan 3 task 4) — db_isolated redirects the
    process-wide DB singleton, so the fresh AccountStore()/TransactionStore() instances
    below need no per-instance path-sync. from_account_id/to_account_id are now real FKs
    onto accounts.id — both callers of this fixture book against "acc-1", so stub it here
    once rather than at each call site.
    """
    import backend.services.fx_cache as fx_mod
    from backend.db.account_store import AccountStore
    from backend.db.transaction_store import TransactionStore
    from tests.conftest import seed_stub_accounts

    acc_store = AccountStore()
    txn_store = TransactionStore()
    monkeypatch.setattr(svc, "accounts", acc_store)
    monkeypatch.setattr(svc, "transactions", txn_store)
    monkeypatch.setattr(svc, "base_currency", lambda: "SGD")
    # Identity FX: SGD → SGD, no conversion needed.
    monkeypatch.setattr(fx_mod, "load_fx_cache", lambda: {"SGD": 1.0})
    seed_stub_accounts([{"from_account_id": "acc-1"}])
    return acc_store, txn_store


def test_get_transactions_totals_include_rows_with_null_base_amount(ledger_env_f3):
    """F3 regression: income and expense rows with stored base_amount=None must be
    recomputed and counted in total_income_base / total_expenses_base.

    Before the fix, the recompute loop guarded `if t.get("base_amount") is not None`,
    leaving None in place for old rows. _txn_base_totals then excluded them because
    `t.get("base_amount") is not None` was also False — so the totals were silently
    under-counted.
    """
    _, txn_store = ledger_env_f3

    # Seed rows with base_amount=None (simulating old/migrated data).
    txn_store.append(
        {
            "date": "2026-02-10",
            "kind": "income",
            "to_account_id": "acc-1",
            "to_amount": 300.0,
            "to_currency": "SGD",
            "base_amount": None,  # the bug: stored as None
            "base_currency": None,
        }
    )
    txn_store.append(
        {
            "date": "2026-02-15",
            "kind": "expense",
            "from_account_id": "acc-1",
            "from_amount": 120.0,
            "from_currency": "SGD",
            "base_amount": None,  # the bug: stored as None
            "base_currency": None,
        }
    )

    result = svc.get_transactions(limit=100, date_from="2026-02-01", date_to="2026-02-28")

    assert "total_income_base" in result, "limit query must include base totals"
    assert "total_expenses_base" in result

    assert result["total_income_base"] == pytest.approx(300.0), (
        f"income row with base_amount=None must be recomputed; "
        f"got {result['total_income_base']!r}, expected ~300.0"
    )
    assert result["total_expenses_base"] == pytest.approx(120.0), (
        f"expense row with base_amount=None must be recomputed; "
        f"got {result['total_expenses_base']!r}, expected ~120.0"
    )


def test_get_transactions_totals_agree_with_get_ledger_summary(ledger_env_f3):
    """F3 regression: list-endpoint totals must agree with get_ledger_summary() for the
    same date range. Before the fix they diverged because the list endpoint left
    base_amount=None rows uncounted while get_ledger_summary always recomputed.
    """
    _, txn_store = ledger_env_f3

    txn_store.append(
        {
            "date": "2026-03-05",
            "kind": "income",
            "to_account_id": "acc-1",
            "to_amount": 500.0,
            "to_currency": "SGD",
            "base_amount": None,
        }
    )
    txn_store.append(
        {
            "date": "2026-03-12",
            "kind": "expense",
            "from_account_id": "acc-1",
            "from_amount": 80.0,
            "from_currency": "SGD",
            "base_amount": None,
        }
    )

    txn_result = svc.get_transactions(limit=100, date_from="2026-03-01", date_to="2026-03-31")
    summary = svc.get_ledger_summary(month=3, year=2026)

    assert txn_result["total_income_base"] == pytest.approx(summary["total_in"]), (
        "get_transactions totals must equal get_ledger_summary for the same period"
    )
    assert txn_result["total_expenses_base"] == pytest.approx(summary["total_out"]), (
        "get_transactions expense totals must equal get_ledger_summary for the same period"
    )
