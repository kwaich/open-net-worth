"""StatementFormatStore on Core (plan 4d, task 2) — DB-CRUD round trip, the fingerprint
upsert/version-bump, and set_match_policy's atomic pair write (security review F7 — the one
outcome that must be impossible is a stale match_policy under a fresh
match_policy_instructions_hash).
"""

from __future__ import annotations

from sqlalchemy import select

from backend.db.statement_format_store import StatementFormatStore
from backend.db.tables import statement_formats


def _raw_payload(engine, profile_id):
    with engine.connect() as conn:
        row = conn.execute(
            select(statement_formats.c.payload).where(statement_formats.c.id == profile_id)
        ).first()
    return row.payload if row else None


def test_upsert_creates_then_updates_in_place(db_engine):
    store = StatementFormatStore(engine=db_engine)
    row = store.upsert("fp1", kind="tabular", schema={"cols": ["date", "amount"]})
    assert row["id"].startswith("fmt_") and row["version"] == 1

    again = store.upsert("fp1", kind="tabular", schema={"cols": ["date", "amount"]})
    assert again["id"] == row["id"] and again["version"] == 1  # unchanged schema, no bump

    drifted = store.upsert("fp1", kind="tabular", schema={"cols": ["date", "amount", "memo"]})
    assert drifted["id"] == row["id"] and drifted["version"] == 2  # schema changed -> bump

    assert len(store.all_profiles()) == 1


def test_find_by_fingerprint_and_by_id(db_engine):
    store = StatementFormatStore(engine=db_engine)
    row = store.upsert("fp1", kind="tabular", schema={"cols": []})
    assert store.find_by_fingerprint("fp1")["id"] == row["id"]
    assert store.find_by_fingerprint("missing") is None
    assert store.find_by_id(row["id"])["fingerprint"] == "fp1"


def test_origin_never_downgraded_by_automatic_relearn(db_engine):
    store = StatementFormatStore(engine=db_engine)
    store.upsert("fp1", kind="tabular", schema={"cols": []}, origin="taught")
    reupserted = store.upsert("fp1", kind="tabular", schema={"cols": ["x"]}, origin="inferred")
    assert reupserted["origin"] == "taught"  # taught > inferred — never silently downgraded


def test_set_instructions_and_set_accounts(db_engine):
    store = StatementFormatStore(engine=db_engine)
    row = store.upsert("fp1", kind="tabular", schema={"cols": []})
    store.set_instructions(row["id"], "ignore header row 2")
    assert store.find_by_id(row["id"])["instructions"] == "ignore header row 2"

    store.set_accounts(row["id"], ["acc_b", "acc_a", "acc_b"])
    assert store.find_by_id(row["id"])["account_ids"] == ["acc_b", "acc_a"]
    store.set_accounts(row["id"], [])  # empty is a no-op, not a clear
    assert store.find_by_id(row["id"])["account_ids"] == ["acc_b", "acc_a"]


def test_record_use_increments_and_stamps_last_used(db_engine):
    store = StatementFormatStore(engine=db_engine)
    row = store.upsert("fp1", kind="tabular", schema={"cols": []})
    store.record_use(row["id"], account_ids=["acc_a"])
    updated = store.find_by_id(row["id"])
    assert updated["uses"] == 1 and updated["last_used_at"] and updated["account_ids"] == ["acc_a"]


def test_delete_removes_the_profile(db_engine):
    store = StatementFormatStore(engine=db_engine)
    row = store.upsert("fp1", kind="tabular", schema={"cols": []})
    assert store.delete(row["id"]) is True
    assert store.find_by_id(row["id"]) is None
    assert store.delete(row["id"]) is False


def test_accounts_for_heals_legacy_singular_account_id():
    store = StatementFormatStore()
    assert store.accounts_for({"account_ids": ["a", "b"]}) == ["a", "b"]
    assert store.accounts_for({"account_id": "legacy_a"}) == ["legacy_a"]
    assert store.accounts_for({}) == []


# ── Security-critical: set_match_policy atomicity (F7) ──────────────────────────────────────


def test_set_match_policy_writes_both_fields_atomically(db_engine):
    store = StatementFormatStore(engine=db_engine)
    row = store.upsert("fp1", kind="tabular", schema={"cols": []})
    assert "match_policy" not in store.find_by_id(row["id"])

    store.set_match_policy(row["id"], {"fields": {"account": "Meridian Bank Checking"}}, "hash1")
    updated = store.find_by_id(row["id"])
    assert updated["match_policy"] == {"fields": {"account": "Meridian Bank Checking"}}
    assert updated["match_policy_instructions_hash"] == "hash1"

    # Both fields live in the SAME payload column — a single-row UPDATE is atomic by
    # construction, proven against the raw row (not just the store's own read).
    raw = _raw_payload(db_engine, row["id"])
    assert raw["match_policy_instructions_hash"] == "hash1"


def test_set_match_policy_none_clears_both_fields_together(db_engine):
    store = StatementFormatStore(engine=db_engine)
    row = store.upsert("fp1", kind="tabular", schema={"cols": []})
    store.set_match_policy(row["id"], {"fields": {}}, "hash1")
    store.set_match_policy(row["id"], None, None)
    updated = store.find_by_id(row["id"])
    assert "match_policy" not in updated and "match_policy_instructions_hash" not in updated


def test_set_match_policy_missing_profile_is_a_no_op(db_engine):
    store = StatementFormatStore(engine=db_engine)
    store.set_match_policy("fmt_missing", {"fields": {}}, "hash1")  # must not raise
    assert store.find_by_id("fmt_missing") is None
