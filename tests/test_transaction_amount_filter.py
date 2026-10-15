"""
Unit tests for the get_transactions min_amount / max_amount filter
(backend/api/ledger.py).

The filter must:
  - compare against the current-base amount when no account is specified;
  - compare against the *native* leg amount when an account_id is given
    (so ">350M VND in Riverside Bank" compares VND-to-VND, never base currency);
  - be all-time (skip the implicit current-month filter) when only an amount
    filter is set, with no explicit month/year.

get_transactions re-bases each txn's reporting amount into the current base from
its native leg (the stored base_amount is a snapshot, stale after a base change),
so the fixtures carry explicit leg currencies and we isolate base + FX.
"""

from __future__ import annotations

import pytest

from backend.api import ledger

# Base = SGD; 1 VND = 0.00005 SGD. Fixtures dated in 2024 (far from the current month)
# to prove an amount filter alone makes the query all-time.
_FX = {"SGD": 1.0, "VND": 0.00005}

TXNS = [
    # SGD 100 expense → base 100
    {
        "id": "a",
        "date": "2024-01-05",
        "kind": "expense",
        "category": "food",
        "from_account_id": "sgd",
        "from_amount": 100.0,
        "from_currency": "SGD",
        "base_amount": 100.0,
        "to_account_id": None,
        "to_amount": None,
    },
    # 90,000,000 VND expense → base 4,500
    {
        "id": "b",
        "date": "2024-02-10",
        "kind": "expense",
        "category": "rent",
        "from_account_id": "vnd",
        "from_amount": 90_000_000.0,
        "from_currency": "VND",
        "base_amount": 4500.0,
        "to_account_id": None,
        "to_amount": None,
    },
    # 8,000,000 VND income → base 400
    {
        "id": "c",
        "date": "2024-03-01",
        "kind": "income",
        "category": "income",
        "from_account_id": None,
        "from_amount": None,
        "from_currency": None,
        "base_amount": 400.0,
        "to_account_id": "vnd",
        "to_amount": 8_000_000.0,
        "to_currency": "VND",
    },
]


@pytest.fixture
def stub_txns(monkeypatch):
    """Make svc.transactions.query_txns return a copy of TXNS (honouring account_id
    narrowing like the real store), and isolate base currency + FX rates."""

    def fake_query(**kwargs):
        acct = kwargs.get("account_id")
        rows = [dict(t) for t in TXNS]
        if acct:  # mirror the real store: only txns touching that account
            rows = [t for t in rows if acct in (t.get("from_account_id"), t.get("to_account_id"))]
        return rows

    monkeypatch.setattr(ledger.svc.transactions, "query_txns", fake_query)
    monkeypatch.setattr(ledger.svc, "base_currency", lambda: "SGD")
    monkeypatch.setattr(ledger, "load_fx_cache", lambda: dict(_FX))
    monkeypatch.setattr(ledger.svc.accounts, "find_by_id", lambda _id: None)


def _ids(result):
    rows = result["transactions"] if isinstance(result, dict) else result
    return {t["id"] for t in rows}


def test_min_amount_compares_base_when_no_account(stub_txns):
    # >= base 1000 → only the VND-90M (base 4,500) txn b. a (100) and c (400) are below.
    out = ledger.get_transactions(min_amount=1000)
    assert _ids(out) == {"b"}


def test_amount_filter_is_all_time(stub_txns):
    # No month/year, only an amount filter → all-time. min 50 (base) matches all three
    # despite all being in 2024 (proves the current-month default was skipped).
    out = ledger.get_transactions(min_amount=50)
    assert _ids(out) == {"a", "b", "c"}


def test_min_amount_uses_native_leg_for_account(stub_txns):
    # In the VND account, compare in VND. >= 50M VND keeps b (90M), drops c (8M).
    out = ledger.get_transactions(account_id="vnd", min_amount=50_000_000)
    assert _ids(out) == {"b"}


def test_max_amount_native_leg_for_account(stub_txns):
    # <= 10M VND in the VND account → only c (8M); b (90M) excluded.
    out = ledger.get_transactions(account_id="vnd", max_amount=10_000_000)
    assert _ids(out) == {"c"}


def test_min_and_max_window_base(stub_txns):
    # base window [200, 1000] → only c (400). a (100) below, b (4,500) above.
    out = ledger.get_transactions(min_amount=200, max_amount=1000)
    assert _ids(out) == {"c"}


def test_response_base_amount_is_rebased(stub_txns):
    # The returned base_amount must be the current-base recomputation from the native leg,
    # not the stored snapshot. b: 90,000,000 VND × 0.00005 = 4,500 (stored was already 4,500;
    # prove it survives), c: 8,000,000 × 0.00005 = 400.
    out = ledger.get_transactions(min_amount=50)
    by_id = {t["id"]: t for t in out}
    assert by_id["b"]["base_amount"] == pytest.approx(4500.0)
    assert by_id["c"]["base_amount"] == pytest.approx(400.0)
    assert by_id["b"]["base_currency"] == "SGD"
