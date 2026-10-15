"""EM1 — db.email_snapshot_store.EmailSnapshotStore CRUD.

Isolated: db_isolated redirects the store to a fresh, empty database (mirrors
tests/test_alert_store.py's convention).
"""

from __future__ import annotations

import pytest

from backend.db.email_snapshot_store import EmailSnapshotStore


@pytest.fixture
def store(db_isolated):
    return EmailSnapshotStore()


def _row(
    message_id: str, *, referenced: bool = False, stashed_at: str = "2026-07-01T00:00:00+00:00"
) -> dict:
    return {
        "message_id": message_id,
        "subject": "Your statement is ready",
        "from_addr": "alerts@bank.example",
        "received_at": "2026-07-01",
        "body_text": "Amount: $50.00 charged to your card.",
        "referenced": referenced,
        "stashed_at": stashed_at,
    }


# ── stash / get ──────────────────────────────────────────────────────────────


def test_stash_then_get_round_trips(store):
    store.stash(_row("<m-1@x>"))
    row = store.get("<m-1@x>")
    assert row is not None
    assert row["subject"] == "Your statement is ready"
    assert row["referenced"] is False


def test_get_missing_returns_none(store):
    assert store.get("<never-stashed@x>") is None


def test_stash_dedupes_by_message_id_as_a_no_op(store):
    """Re-stashing an already-known message_id is a stable no-op — the FIRST snapshot wins,
    a later re-stash (e.g. re-fetching the same email) never overwrites it."""
    store.stash(_row("<m-1@x>", stashed_at="2026-07-01T00:00:00+00:00"))
    # A second stash of the same id with different content must be ignored.
    changed = dict(_row("<m-1@x>", stashed_at="2026-07-05T00:00:00+00:00"))
    changed["subject"] = "A different subject"
    store.stash(changed)

    row = store.get("<m-1@x>")
    assert row["subject"] == "Your statement is ready"  # original wins
    assert row["stashed_at"] == "2026-07-01T00:00:00+00:00"
    assert len(store.all()) == 1


def test_stash_referenced_flag_survives_a_noop_restash(store):
    store.stash(_row("<m-1@x>"))
    assert store.promote("<m-1@x>") is True
    # Re-stash (e.g. the agent re-fetches the same message) must not clear referenced.
    store.stash(_row("<m-1@x>"))
    assert store.get("<m-1@x>")["referenced"] is True


# ── promote ──────────────────────────────────────────────────────────────────


def test_promote_flips_referenced_true(store):
    store.stash(_row("<m-1@x>"))
    assert store.promote("<m-1@x>") is True
    assert store.get("<m-1@x>")["referenced"] is True


def test_promote_is_idempotent(store):
    store.stash(_row("<m-1@x>"))
    assert store.promote("<m-1@x>") is True
    assert store.promote("<m-1@x>") is True  # second call, still True, no error
    assert store.get("<m-1@x>")["referenced"] is True


def test_promote_missing_message_id_returns_false(store):
    assert store.promote("<never-stashed@x>") is False


# ── prune_unreferenced ────────────────────────────────────────────────────────


def test_prune_keeps_referenced_and_newest_unreferenced(store):
    # 3 unreferenced rows, oldest→newest, plus one referenced row (older than all of them).
    store.stash(_row("<ref@x>", referenced=True, stashed_at="2026-01-01T00:00:00+00:00"))
    store.stash(_row("<old@x>", stashed_at="2026-01-02T00:00:00+00:00"))
    store.stash(_row("<mid@x>", stashed_at="2026-01-03T00:00:00+00:00"))
    store.stash(_row("<new@x>", stashed_at="2026-01-04T00:00:00+00:00"))

    removed = store.prune_unreferenced(keep=2)
    assert removed == 1

    remaining_ids = {r["message_id"] for r in store.all()}
    # referenced row always survives; the 2 NEWEST unreferenced rows survive; the oldest
    # unreferenced row is pruned.
    assert remaining_ids == {"<ref@x>", "<mid@x>", "<new@x>"}


def test_prune_below_bound_is_a_noop(store):
    store.stash(_row("<a@x>", stashed_at="2026-01-01T00:00:00+00:00"))
    store.stash(_row("<b@x>", stashed_at="2026-01-02T00:00:00+00:00"))
    removed = store.prune_unreferenced(keep=200)
    assert removed == 0
    assert len(store.all()) == 2


def test_prune_never_touches_referenced_rows_even_if_many(store):
    for i in range(5):
        store.stash(
            _row(f"<ref-{i}@x>", referenced=True, stashed_at=f"2026-01-0{i + 1}T00:00:00+00:00")
        )
    removed = store.prune_unreferenced(keep=0)
    assert removed == 0
    assert len(store.all()) == 5
