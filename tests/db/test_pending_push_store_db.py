"""PendingPushStore on PayloadTableStore (plan 4e, task 1) — a Tier-2 FULL MIGRATE (NO
book_id), ``checkpointed=False`` at construction (a construction-time opt-out of ever
firing the write-log hook — ``_EXCLUDED_TABLES`` would make it a structural no-op anyway,
cheaper to not fire it at all). ``due()`` filters/sorts in Python over ``self.all()``.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest


@pytest.fixture
def store(db_engine):
    from backend.db.pending_push_store import PendingPushStore

    return PendingPushStore(engine=db_engine)


def _iso(offset_sec: int = 0) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=offset_sec)).isoformat()


# ── CRUD parity with the JsonStore era ───────────────────────────────────────────


def test_add_stamps_id_attempts_and_created_at(store):
    row = store.add("Analysis — AAPL", ["telegram"], "Apple", "AAPL", _iso())
    assert row["id"].startswith("ppush_")
    assert row["attempts"] == 0
    assert row["created_at"]
    assert row["channels"] == ["telegram"]
    assert store.find_by_id(row["id"])["message"] == "Analysis — AAPL"


def test_reschedule_updates_attempts_and_next_attempt_at(store):
    row = store.add("m", ["telegram"], "l", "T", _iso())
    next_at = _iso(900)
    store.reschedule(row["id"], 2, next_at)
    updated = store.find_by_id(row["id"])
    assert updated["attempts"] == 2
    assert updated["next_attempt_at"] == next_at


def test_remove_deletes_the_row(store):
    row = store.add("m", ["telegram"], "l", "T", _iso())
    store.remove(row["id"])
    assert store.find_by_id(row["id"]) is None
    assert store.all() == []


def test_persisted_row_survives_a_fresh_store_instance(store, db_engine):
    from backend.db.pending_push_store import PendingPushStore

    store.add("m", ["telegram"], "l", "T", _iso())
    fresh = PendingPushStore(engine=db_engine)
    assert len(fresh.all()) == 1


# ── due(): filters to next_attempt_at <= now, sorted earliest-first ────────────────────


def test_due_filters_and_sorts_earliest_first(store):
    late = store.add("late", ["telegram"], "l", "T1", _iso(3600))
    now_ = store.add("now", ["telegram"], "l", "T2", _iso(-10))
    earlier = store.add("earlier", ["telegram"], "l", "T3", _iso(-3600))

    due = store.due(_iso())
    assert [r["id"] for r in due] == [earlier["id"], now_["id"]]
    assert late["id"] not in [r["id"] for r in due]


def test_due_excludes_rows_not_yet_due(store):
    store.add("future", ["telegram"], "l", "T1", _iso(3600))
    assert store.due(_iso()) == []


def test_due_empty_store_is_empty_list(store):
    assert store.due(_iso()) == []


# ── checkpointed=False — construction-time hook opt-out ────────────────────────────────


def test_pending_pushes_table_is_checkpoint_excluded():
    from backend.ai.checkpoint import _EXCLUDED_TABLES

    assert "pending_pushes" in _EXCLUDED_TABLES


def test_store_constructs_with_checkpointed_false(store):
    assert store._checkpointed is False


def test_checkpoint_does_not_capture_or_revert_pending_push_writes(db_isolated):
    """Behavioral proof, not just the frozenset/flag: a row added mid-turn is NOT
    captured by the checkpoint and NOT reverted by restore."""
    from backend.ai import checkpoint as cp
    from backend.db.pending_push_store import PendingPushStore

    store = PendingPushStore()

    ctx, token = cp.begin_turn("s1", 0, "retry a push")
    row = store.add("during-turn", ["telegram"], "l", "T", _iso())
    entry = cp.end_turn(ctx, token)

    assert entry is None or "pending_pushes" not in entry.get("db_tables", [])
    if entry is not None:
        cp.restore(entry["id"], mode="data")
    assert store.find_by_id(row["id"]) is not None  # unreverted
