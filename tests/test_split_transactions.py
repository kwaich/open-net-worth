"""
Unit tests for split transactions (P3-SP, slice 1) — the category-allocation overlay.

Covers: ``_validate_splits`` (sum-mismatch refused / never re-scaled, kind gate, unknown
category, 1-line→unsplit normalisation, sentinel rejection); ``txn_split_base_amount`` (lines
sum EXACTLY to ``txn_base_amount``, incl. after a base-currency change); the two category folds
(``budgets._spent_by_category`` / ``planning._activity_by_category`` attribute each line, month
total unchanged vs the same amount as one category — the no-double-count proof); the cardinal
invariant (net worth / account balance identical with vs without splitting the same txn — legs
unchanged); and ``query_txns`` finding a split by line category (the deep-link).

Fully offline + isolated: stores/FX/profile monkeypatched into tmp_path, mirroring
tests/test_planning_service.py + tests/test_char_ledger_math.py.
"""

from __future__ import annotations

import pytest

from backend.core.errors import ValidationError
from backend.db.category_store import CategoryStore
from backend.db.profile_store import ProfileStore
from backend.models.category_defs import BUILTIN_CATEGORIES
from backend.services import budgets as budgets_service
from backend.services import ledger as svc
from backend.services import planning as planning

_FX = {"SGD": 1.0, "USD": 1.35}


@pytest.fixture
def env(tmp_path, monkeypatch, db_isolated):
    """Isolate every disk dependency of the ledger/budget/planning core into tmp_path."""
    profile_store = ProfileStore()

    # budget_assignments is DB-backed now (plan 4a task 4) — db_isolated already covers it.
    monkeypatch.setattr(planning, "load_fx_cache", lambda: dict(_FX))
    monkeypatch.setattr(budgets_service, "load_fx_cache", lambda: dict(_FX))
    # profile is DB-backed now (Wave 5, JSON->DB) — categories_service.budgets_service is
    # the SAME module object as budgets_service above, so no separate patch is needed for it.
    monkeypatch.setattr(svc, "base_currency", lambda: "SGD")
    import backend.services.fx_cache as fx_cache_mod

    monkeypatch.setattr(fx_cache_mod, "load_fx_cache", lambda: dict(_FX))

    profile_store.set_raw({"base_currency": "SGD"})
    # CategoryStore is DB-backed (plan 4a task 2); db_isolated redirects it to a fresh, empty
    # database — reseed BUILTIN_CATEGORIES here (tests/conftest.py's shared autouse seed ran
    # BEFORE db_isolated activated, so it landed in the old, now-orphaned default engine).
    CategoryStore().replace_all(list(BUILTIN_CATEGORIES))

    # An expense must name its source account (ledger leg invariants, aa856a607).
    account = svc.accounts.create(
        {"name": "Cash", "kind": "asset", "type": "cash", "currency": "SGD", "initial_balance": 0}
    )
    return type("Env", (), {"profile": profile_store, "account_id": account["id"]})


# ── _validate_splits / _apply_splits ─────────────────────────────────────────────
def test_split_sum_must_equal_leg_amount_refused(env):
    with pytest.raises(ValidationError, match="sum"):
        svc.create_transaction(
            {
                "date": "2026-06-10",
                "kind": "expense",
                "from_account_id": env.account_id,
                "from_amount": 100.0,
                "from_currency": "SGD",
                "splits": [
                    {"category": "cat_13095ba70000", "amount": 60.0},
                    {"category": "cat_6c4bc0170000", "amount": 30.0},  # 90 ≠ 100
                ],
            }
        )


def test_split_never_silently_rescaled(env):
    # A mismatch must RAISE, not quietly rescale the lines to fit (would drift a budget).
    with pytest.raises(ValidationError):
        svc.create_transaction(
            {
                "date": "2026-06-10",
                "kind": "expense",
                "from_account_id": env.account_id,
                "from_amount": 100.0,
                "from_currency": "SGD",
                "splits": [
                    {"category": "cat_13095ba70000", "amount": 50.0},
                    {"category": "cat_6c4bc0170000", "amount": 60.0},
                ],
            }
        )


def test_split_on_transfer_rejected(env):
    # A transfer with splits is always rejected: the split-kind gate refuses splits on
    # non-expense/income kinds. Create real accounts first so the transfer integrity check
    # passes and the split gate actually fires.
    a = svc.accounts.create(
        {
            "name": "A",
            "kind": "asset",
            "type": "cash",
            "currency": "SGD",
            "initial_balance": 0,
        }
    )
    b = svc.accounts.create(
        {
            "name": "B",
            "kind": "asset",
            "type": "cash",
            "currency": "SGD",
            "initial_balance": 0,
        }
    )
    with pytest.raises(ValidationError, match="expense/income"):
        svc.create_transaction(
            {
                "date": "2026-06-10",
                "kind": "transfer",
                "from_account_id": a["id"],
                "from_amount": 100.0,
                "from_currency": "SGD",
                "to_account_id": b["id"],
                "to_amount": 100.0,
                "to_currency": "SGD",
                "splits": [{"category": "cat_13095ba70000", "amount": 100.0}],
            }
        )


def test_split_unknown_category_rejected(env):
    with pytest.raises(ValidationError, match="Unknown split category"):
        svc.create_transaction(
            {
                "date": "2026-06-10",
                "kind": "expense",
                "from_account_id": env.account_id,
                "from_amount": 100.0,
                "from_currency": "SGD",
                "splits": [
                    {"category": "cat_13095ba70000", "amount": 60.0},
                    {"category": "nonsense_cat", "amount": 40.0},
                ],
            }
        )


def test_split_sentinel_as_line_rejected(env):
    with pytest.raises(ValidationError, match="cannot be a split category"):
        svc.create_transaction(
            {
                "date": "2026-06-10",
                "kind": "expense",
                "from_account_id": env.account_id,
                "from_amount": 100.0,
                "from_currency": "SGD",
                "splits": [
                    {"category": "split", "amount": 60.0},
                    {"category": "cat_13095ba70000", "amount": 40.0},
                ],
            }
        )


def test_one_line_split_normalised_to_unsplit(env):
    stored = svc.create_transaction(
        {
            "date": "2026-06-10",
            "kind": "expense",
            "from_account_id": env.account_id,
            "from_amount": 100.0,
            "from_currency": "SGD",
            "splits": [{"category": "cat_13095ba70000", "amount": 100.0}],
        }
    )
    assert stored["category"] == "cat_13095ba70000"
    assert stored["splits"] is None


def test_valid_split_stores_sentinel_and_lines(env):
    stored = svc.create_transaction(
        {
            "date": "2026-06-10",
            "kind": "expense",
            "from_account_id": env.account_id,
            "from_amount": 100.0,
            "from_currency": "SGD",
            "splits": [
                {"category": "cat_13095ba70000", "amount": 60.0},
                {"category": "cat_6c4bc0170000", "amount": 40.0},
            ],
        }
    )
    assert stored["category"] == "split"
    assert [s["category"] for s in stored["splits"]] == ["cat_13095ba70000", "cat_6c4bc0170000"]
    assert [s["amount"] for s in stored["splits"]] == [60.0, 40.0]


# ── txn_split_base_amount — sums EXACTLY to the leg base, base-change-safe ────────
def test_split_base_amount_sums_to_leg_base():
    t = {"kind": "expense", "from_amount": 100.0, "from_currency": "SGD", "base_amount": 100.0}
    s1 = {"category": "cat_13095ba70000", "amount": 60.0}
    s2 = {"category": "cat_6c4bc0170000", "amount": 40.0}
    leg = svc.txn_base_amount(t, "SGD", _FX)
    parts = svc.txn_split_base_amount(t, s1, "SGD", _FX) + svc.txn_split_base_amount(
        t, s2, "SGD", _FX
    )
    assert parts == pytest.approx(leg)


def test_split_base_amount_base_change_safe():
    # A USD leg split 60/40; pin pre/post base change — lines still sum to the recomputed leg.
    t = {"kind": "expense", "from_amount": 100.0, "from_currency": "USD", "base_amount": 100.0}
    s1 = {"category": "cat_13095ba70000", "amount": 60.0}
    s2 = {"category": "cat_6c4bc0170000", "amount": 40.0}
    for base in ("SGD", "USD"):
        leg = svc.txn_base_amount(t, base, _FX)
        parts = svc.txn_split_base_amount(t, s1, base, _FX) + svc.txn_split_base_amount(
            t, s2, base, _FX
        )
        assert parts == pytest.approx(leg)
    # SGD: 100 USD = 135 SGD; lines 81 + 54.
    assert svc.txn_split_base_amount(t, s1, "SGD", _FX) == pytest.approx(81.0)
    assert svc.txn_split_base_amount(t, s2, "SGD", _FX) == pytest.approx(54.0)


# ── _spent_by_category fold — attributes lines, month total unchanged ─────────────


def _split_expense(date, amount, lines, currency="SGD"):
    return {
        "kind": "expense",
        "date": date,
        "category": "split",
        "from_account_id": "bank",
        "from_amount": amount,
        "from_currency": currency,
        "base_amount": amount,
        "splits": [{"category": c, "amount": a} for c, a in lines],
    }


def _plain_expense(date, category, amount, currency="SGD"):
    return {
        "kind": "expense",
        "date": date,
        "category": category,
        "from_account_id": "bank",
        "from_amount": amount,
        "from_currency": currency,
        "base_amount": amount,
    }


def test_spent_by_category_attributes_each_split_line():
    txns = [
        _split_expense(
            "2026-06-10", 100.0, [("cat_13095ba70000", 60.0), ("cat_6c4bc0170000", 40.0)]
        )
    ]
    out = budgets_service._spent_by_category(txns, 2026, 6, "SGD", _FX)
    assert out["cat_13095ba70000"] == pytest.approx(60.0)
    assert out["cat_6c4bc0170000"] == pytest.approx(40.0)
    assert "split" not in out  # the sentinel never appears as a category row


def test_spent_by_category_month_total_unchanged_vs_single_category():
    split_book = [
        _split_expense(
            "2026-06-10", 100.0, [("cat_13095ba70000", 60.0), ("cat_6c4bc0170000", 40.0)]
        )
    ]
    plain_book = [_plain_expense("2026-06-10", "cat_13095ba70000", 100.0)]
    split_total = sum(budgets_service._spent_by_category(split_book, 2026, 6, "SGD", _FX).values())
    plain_total = sum(budgets_service._spent_by_category(plain_book, 2026, 6, "SGD", _FX).values())
    assert split_total == pytest.approx(plain_total) == pytest.approx(100.0)


# ── _activity_by_category fold — split expense subtracts per line ─────────────────
def test_activity_by_category_attributes_each_split_line(env):
    txns = [
        _split_expense(
            "2026-06-10", 100.0, [("cat_13095ba70000", 60.0), ("cat_6c4bc0170000", 40.0)]
        )
    ]
    out = planning._activity_by_category(txns, "2026-06", "SGD", _FX, frozenset({"bank"}))
    assert out["cat_13095ba70000"] == pytest.approx(-60.0)
    assert out["cat_6c4bc0170000"] == pytest.approx(-40.0)
    assert "split" not in out


def test_activity_total_unchanged_vs_single_category(env):
    split_book = [
        _split_expense(
            "2026-06-10", 100.0, [("cat_13095ba70000", 60.0), ("cat_6c4bc0170000", 40.0)]
        )
    ]
    plain_book = [_plain_expense("2026-06-10", "cat_13095ba70000", 100.0)]
    s = sum(
        planning._activity_by_category(
            split_book, "2026-06", "SGD", _FX, frozenset({"bank"})
        ).values()
    )
    p = sum(
        planning._activity_by_category(
            plain_book, "2026-06", "SGD", _FX, frozenset({"bank"})
        ).values()
    )
    assert s == pytest.approx(p) == pytest.approx(-100.0)


# ── The cardinal invariant: net worth / balance unchanged by splitting ───────────
def test_account_balance_identical_split_vs_unsplit(env):
    # The opening is now a real "Starting Balance" transaction (opening_txn_id pointer), not
    # an account scalar — mint it here so the fold still starts from 1,000.
    opening = {
        "id": "open-bank",
        "date": "2026-01-01",
        "kind": "adjustment",
        "to_account_id": "bank",
        "to_amount": 1_000.0,
    }
    acct = {
        "id": "bank",
        "currency": "SGD",
        "opening_txn_id": "open-bank",
    }
    split_txn = {
        **_split_expense(
            "2026-06-10", 100.0, [("cat_13095ba70000", 60.0), ("cat_6c4bc0170000", 40.0)]
        ),
        "from_account_id": "bank",
    }
    plain_txn = {
        **_plain_expense("2026-06-10", "cat_13095ba70000", 100.0),
        "from_account_id": "bank",
    }
    # The leg is identical → the balance fold (which reads legs, never category/splits) is identical.
    assert svc.account_balance(acct, [opening, split_txn], "2026-01-01") == svc.account_balance(
        acct, [opening, plain_txn], "2026-01-01"
    )
    assert svc.account_balance(acct, [opening, split_txn], "2026-01-01") == pytest.approx(900.0)


def test_net_worth_identical_split_vs_unsplit(env, monkeypatch):
    # The opening is now a real "Starting Balance" transaction (opening_txn_id pointer), not
    # an account scalar — mint it here so the fold still starts from 1,000.
    opening = {
        "id": "open-bank",
        "date": "2026-01-01",
        "kind": "adjustment",
        "to_account_id": "bank",
        "to_amount": 1_000.0,
    }
    accts = [
        {
            "id": "bank",
            "kind": "asset",
            "type": "cash",
            "currency": "SGD",
            "opening_txn_id": "open-bank",
        }
    ]
    monkeypatch.setattr(svc.accounts, "query_accounts", lambda include_archived=False: accts)
    from backend.services import positions as ps

    monkeypatch.setattr(ps, "hydrated", lambda: [])

    split_txn = {
        **_split_expense(
            "2026-06-10", 100.0, [("cat_13095ba70000", 60.0), ("cat_6c4bc0170000", 40.0)]
        ),
        "from_account_id": "bank",
    }
    plain_txn = {
        **_plain_expense("2026-06-10", "cat_13095ba70000", 100.0),
        "from_account_id": "bank",
    }

    monkeypatch.setattr(svc.transactions, "all", lambda: [opening, split_txn])
    nw_split = svc.net_worth()["net_worth"]
    monkeypatch.setattr(svc.transactions, "all", lambda: [opening, plain_txn])
    nw_plain = svc.net_worth()["net_worth"]
    assert nw_split == nw_plain == pytest.approx(900.0)


# ── query_txns finds a split by line category (the deep-link) ─────────────────────
def test_query_txns_finds_split_by_line_category(env):
    svc.create_transaction(
        {
            "date": "2026-06-10",
            "kind": "expense",
            "from_account_id": env.account_id,
            "from_amount": 100.0,
            "from_currency": "SGD",
            "splits": [
                {"category": "cat_13095ba70000", "amount": 60.0},
                {"category": "cat_6c4bc0170000", "amount": 40.0},
            ],
        }
    )
    food = svc.transactions.query_txns(category="cat_13095ba70000")
    shopping = svc.transactions.query_txns(category="cat_6c4bc0170000")
    transport = svc.transactions.query_txns(category="cat_db5642ab0000")
    assert len(food) == 1 and food[0]["category"] == "split"
    assert len(shopping) == 1
    assert transport == []


# ── Rules interplay: a split keeps its allocation; tags still apply ───────────────
