"""PayeeStore CRUD + alias/usage queries (P4-PAYEE phase 1)."""

from __future__ import annotations

import pytest

from backend.db.payee_store import PayeeStore


@pytest.fixture
def store(db_isolated):
    # db_engine is tests/db/-scoped, not re-exported here (see tests/conftest.py) — this
    # file is top-level, so it uses db_isolated (the redirected process-wide singleton)
    # instead, like every other top-level DB-backed store test.
    return PayeeStore()


def test_create_mints_prefixed_id_and_defaults(store):
    p = store.create("Anthropic", aliases=["anthropic"])
    assert p["id"].startswith("pay_")
    assert p["name"] == "Anthropic"
    assert p["default_categories"] == {}
    assert p["autocategorize"] is True
    assert p["hidden"] is False
    assert p["aliases"] == ["anthropic"]
    assert "txn_count" not in p  # stored field removed; live count is in list_registry()
    assert p["last_used"] is None
    assert store.find_by_id(p["id"]) is not None


def test_create_blank_name_falls_back(store):
    p = store.create("   ")
    assert p["name"] == "Unknown payee"


def test_find_by_alias(store):
    p = store.create("Amazon", aliases=["amazon", "amzn mktp"])
    assert store.find_by_alias("amazon")["id"] == p["id"]
    assert store.find_by_alias("amzn mktp")["id"] == p["id"]
    assert store.find_by_alias("netflix") is None
    assert store.find_by_alias("") is None


def test_create_dedups_aliases(store):
    p = store.create("X", aliases=["a", "a", " a ", "", "b"])
    assert p["aliases"] == ["a", "b"]


def test_add_alias_appends_once(store):
    p = store.create("X", aliases=["a"])
    store.add_alias(p["id"], "b")
    store.add_alias(p["id"], "b")  # dup → no-op
    assert store.find_by_id(p["id"])["aliases"] == ["a", "b"]


def test_set_default_category_for_kind(store):
    p = store.create("X")
    store.set_default_category_for_kind(p["id"], "expense", "food")
    assert store.find_by_id(p["id"])["default_categories"]["expense"] == "food"
    store.set_default_category_for_kind(p["id"], "expense", None)
    assert "expense" not in store.find_by_id(p["id"])["default_categories"]


def test_bump_usage_updates_last_used_only(store):
    p = store.create("X")
    store.bump_usage(p["id"], when="2026-01-05")
    store.bump_usage(p["id"], when="2026-03-09")
    store.bump_usage(p["id"], when="2026-02-01")  # older → last_used unchanged
    got = store.find_by_id(p["id"])
    assert "txn_count" not in got  # never stored; live count comes from list_registry()
    assert got["last_used"] == "2026-03-09"


def test_bump_usage_missing_payee_is_noop(store):
    assert store.bump_usage("pay_nope", when="2026-01-01") is None


def test_txn_count_never_stored_after_bump_or_write(store):
    """Regression: txn_count must not reappear in stored rows after bump_usage or any write."""
    p = store.create("X")
    store.bump_usage(p["id"], when="2026-05-01")
    store.bump_usage(p["id"], when="2026-06-01")
    store.set_default_category_for_kind(p["id"], "expense", "food")
    got = store.find_by_id(p["id"])
    assert "txn_count" not in got
    # Confirm it is truly absent from raw persisted data too
    raw = [r for r in store.all() if r["id"] == p["id"]][0]
    assert "txn_count" not in raw
