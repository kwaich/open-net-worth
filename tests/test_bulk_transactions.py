"""Service tests for bulk transaction operations in the Accounts register.

Covers backend.services.ledger.bulk_update_transactions / bulk_delete_transactions:
  - set category on many rows (validated against the live registry).
  - ADD tags is ADDITIVE (regression: never drops a row's existing tags).
  - set cleared in bulk.
  - transaction Automations still fire PER ROW (the bulk path loops the per-txn choke
    point, it does not bypass it).
  - adjustment never receives a category (skipped, not failed); a transfer DOES — it
    validates and sets like an expense/income row (owner decision 2026-07-03).
  - bulk delete by id; empty id list is refused (no whole-ledger wipe).

Offline: the account + transaction + Automation stores are isolated onto a tmp dir; the
correction recorder is stubbed so
nothing touches live data.
"""

from __future__ import annotations

import pytest

from backend.core.errors import ValidationError
from backend.services import ledger as svc


@pytest.fixture
def ledger_data(tmp_path, monkeypatch, db_isolated):
    """Isolated account + transaction stores; deterministic base/FX; Automations empty;
    correction recorder stubbed (records calls instead of writing).

    Accounts/transactions are DB-backed (plan 3 task 4) and automations is DB-backed
    (plan 4d task 2) — db_isolated redirects all of them (and every other unpinned store)
    to a fresh, empty database; no more per-instance `_path` to repoint. CategoryStore's
    BUILTIN_CATEGORIES seed (tests/conftest.py's shared autouse fixture) ran BEFORE
    db_isolated activated, so it landed in the old, now-orphaned default engine — reseed
    here since _expense() below references real builtin category ids."""
    from backend.db.account_store import AccountStore
    from backend.db.category_store import CategoryStore
    from backend.db.transaction_store import TransactionStore
    from backend.models.category_defs import BUILTIN_CATEGORIES

    CategoryStore().replace_all(list(BUILTIN_CATEGORIES))

    acc_store = AccountStore()
    txn_store = TransactionStore()
    monkeypatch.setattr(svc, "accounts", acc_store)
    monkeypatch.setattr(svc, "transactions", txn_store)
    monkeypatch.setattr(svc, "base_currency", lambda: "SGD")
    monkeypatch.setattr(svc, "to_base", lambda amt, cur, base=None, rates=None: round(amt or 0, 2))

    # Stub the correction recorder so update_transaction never touches the live store.
    corrections = []
    from backend.services import corrections as corrections_service

    monkeypatch.setattr(
        corrections_service,
        "record_correction",
        lambda old, new, **kw: corrections.append((old, new)),
    )

    acct = acc_store.create(
        {
            "name": "Meridian Bank",
            "kind": "asset",
            "type": "cash",
            "currency": "SGD",
            "initial_balance": 0.0,
        }
    )
    return acct, acc_store, txn_store, corrections


def _expense(txn_store, acct_id, amount, **extra):
    row = {
        "date": "2026-02-01",
        "kind": "expense",
        "category": "cat_a6b4e3bc0000",
        "from_account_id": acct_id,
        "from_amount": amount,
        "from_currency": "SGD",
    }
    row.update(extra)
    return txn_store.append(row)


# ── bulk_update: set category ────────────────────────────────────────────────────


def test_bulk_set_category(ledger_data):
    acct, _, txn_store, _ = ledger_data
    a = _expense(txn_store, acct["id"], 10.0)
    b = _expense(txn_store, acct["id"], 20.0)

    res = svc.bulk_update_transactions([a["id"], b["id"]], {"category": "cat_13095ba70000"})
    assert res["count"] == 2 and res["not_found"] == [] and res["skipped"] == []
    assert txn_store.find_by_id(a["id"])["category"] == "cat_13095ba70000"
    assert txn_store.find_by_id(b["id"])["category"] == "cat_13095ba70000"


def test_bulk_set_unknown_category_rejected(ledger_data):
    acct, _, txn_store, _ = ledger_data
    a = _expense(txn_store, acct["id"], 10.0)
    with pytest.raises(ValidationError):
        svc.bulk_update_transactions([a["id"]], {"category": "definitely-not-a-category"})


def test_bulk_set_category_applies_to_transfer(ledger_data):
    """Owner decision 2026-07-03: a transfer's category is no longer stripped — bulk-set
    validates and applies it exactly like an expense/income row's (docs/adr/2026-07-03-
    categorized-transfers.md). The old auto-skip now survives for adjustment only."""
    acct, acc_store, txn_store, _ = ledger_data
    other = acc_store.create(
        {"name": "Lighthouse Bank", "kind": "asset", "type": "cash", "currency": "SGD"}
    )
    transfer = txn_store.append(
        {
            "date": "2026-02-01",
            "kind": "transfer",
            "from_account_id": acct["id"],
            "from_amount": 50.0,
            "from_currency": "SGD",
            "to_account_id": other["id"],
            "to_amount": 50.0,
            "to_currency": "SGD",
        }
    )
    exp = _expense(txn_store, acct["id"], 10.0)

    res = svc.bulk_update_transactions(
        [transfer["id"], exp["id"]], {"category": "cat_13095ba70000"}
    )
    assert res["skipped"] == []
    assert set(res["updated"]) == {transfer["id"], exp["id"]}
    assert txn_store.find_by_id(transfer["id"])["category"] == "cat_13095ba70000"
    assert txn_store.find_by_id(exp["id"])["category"] == "cat_13095ba70000"


def test_bulk_set_category_skips_adjustment(ledger_data):
    """Adjustment is the ONE kind still skipped — a balance correction never carries a
    category, so bulk-set leaves it alone rather than failing the whole batch."""
    acct, _, txn_store, _ = ledger_data
    adj = txn_store.append(
        {
            "date": "2026-02-01",
            "kind": "adjustment",
            "from_account_id": acct["id"],
            "from_amount": 5.0,
            "from_currency": "SGD",
        }
    )
    exp = _expense(txn_store, acct["id"], 10.0)

    res = svc.bulk_update_transactions([adj["id"], exp["id"]], {"category": "cat_13095ba70000"})
    assert exp["id"] in res["updated"]
    assert any(s["id"] == adj["id"] for s in res["skipped"])
    assert txn_store.find_by_id(adj["id"]).get("category") is None
    assert txn_store.find_by_id(exp["id"])["category"] == "cat_13095ba70000"


# ── bulk_update: tags are ADDITIVE (regression) ──────────────────────────────────


def test_bulk_add_tags_is_additive(ledger_data):
    """REGRESSION: bulk-tag must UNION with each row's existing tags, never drop them."""
    acct, _, txn_store, _ = ledger_data
    a = _expense(txn_store, acct["id"], 10.0, tags=["existing"])
    b = _expense(txn_store, acct["id"], 20.0, tags=[])

    res = svc.bulk_update_transactions([a["id"], b["id"]], {"tags": ["work"]})
    assert res["count"] == 2
    # 'existing' is preserved, 'work' is added — no clobber.
    assert set(txn_store.find_by_id(a["id"])["tags"]) == {"existing", "work"}
    assert txn_store.find_by_id(b["id"])["tags"] == ["work"]


def test_bulk_add_tags_no_duplicates(ledger_data):
    acct, _, txn_store, _ = ledger_data
    a = _expense(txn_store, acct["id"], 10.0, tags=["work"])
    svc.bulk_update_transactions([a["id"]], {"tags": ["work"]})
    assert txn_store.find_by_id(a["id"])["tags"] == ["work"]  # no dup


# ── bulk_update: cleared ─────────────────────────────────────────────────────────


def test_bulk_set_cleared(ledger_data):
    acct, _, txn_store, _ = ledger_data
    a = _expense(txn_store, acct["id"], 10.0)
    b = _expense(txn_store, acct["id"], 20.0)
    res = svc.bulk_update_transactions([a["id"], b["id"]], {"cleared": True})
    assert res["count"] == 2
    assert txn_store.find_by_id(a["id"])["cleared"] is True
    assert txn_store.find_by_id(b["id"])["cleared"] is True


# ── bulk_update: Automations fire PER ROW (loops the choke point) ────────────────


# test_bulk_update_automations_fire_per_row was removed 2026-08-02: it pinned a declarative
# transform re-categorizing each row of a BULK edit inside the write. Rules are
# event-triggered Automations now — they fire per row still, but AFTER the commit, so
# nothing is observable at the write. See tests/test_rule_audit_provenance.py.


def test_bulk_update_records_corrections_per_row(ledger_data):
    """The correction recorder runs once per edited row (it's at the per-txn choke point)."""
    acct, _, txn_store, corrections = ledger_data
    a = _expense(txn_store, acct["id"], 10.0)
    b = _expense(txn_store, acct["id"], 20.0)
    svc.bulk_update_transactions([a["id"], b["id"]], {"category": "cat_13095ba70000"})
    assert len(corrections) == 2


# ── validation + not-found ───────────────────────────────────────────────────────


def test_bulk_update_empty_ids_rejected(ledger_data):
    with pytest.raises(ValidationError):
        svc.bulk_update_transactions([], {"category": "cat_13095ba70000"})


def test_bulk_update_empty_changes_rejected(ledger_data):
    acct, _, txn_store, _ = ledger_data
    a = _expense(txn_store, acct["id"], 10.0)
    with pytest.raises(ValidationError):
        svc.bulk_update_transactions([a["id"]], {})


def test_bulk_update_unknown_field_rejected(ledger_data):
    acct, _, txn_store, _ = ledger_data
    a = _expense(txn_store, acct["id"], 10.0)
    with pytest.raises(ValidationError):
        svc.bulk_update_transactions([a["id"]], {"not_a_real_field": "x"})


def test_bulk_update_not_found_reported(ledger_data):
    acct, _, txn_store, _ = ledger_data
    a = _expense(txn_store, acct["id"], 10.0)
    res = svc.bulk_update_transactions([a["id"], "ghost"], {"category": "cat_13095ba70000"})
    assert a["id"] in res["updated"]
    assert res["not_found"] == ["ghost"]


# ── bulk_delete ──────────────────────────────────────────────────────────────────


def test_bulk_delete(ledger_data):
    acct, _, txn_store, _ = ledger_data
    a = _expense(txn_store, acct["id"], 10.0)
    b = _expense(txn_store, acct["id"], 20.0)
    res = svc.bulk_delete_transactions([a["id"], b["id"]])
    assert res["count"] == 2 and res["not_found"] == []
    # Soft-deleted → excluded from the live list.
    assert txn_store.find_by_id(a["id"]) is None or txn_store.find_by_id(a["id"]).get("deleted")


def test_bulk_delete_empty_ids_rejected(ledger_data):
    with pytest.raises(ValidationError):
        svc.bulk_delete_transactions([])


def test_bulk_delete_not_found_reported(ledger_data):
    acct, _, txn_store, _ = ledger_data
    a = _expense(txn_store, acct["id"], 10.0)
    res = svc.bulk_delete_transactions([a["id"], "ghost"])
    assert a["id"] in res["deleted"] and res["not_found"] == ["ghost"]
