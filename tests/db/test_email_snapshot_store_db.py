"""EmailSnapshotStore on PayloadTableStore (plan 4e, task 1) — a Tier-2 FULL MIGRATE (NO
book_id, no blob dir: every field, including body_text, lives inline in payload).
Checkpoint-INCLUDED (unlike its three misc-store siblings this wave) — durable user
evidence a /rewind should correctly cover, mirroring its JsonStore-era
``fail_closed_on_corrupt=True`` posture ("not a disposable cache").

``stash`` is FIRST-SEEN-WINS — a check-then-insert, never a raw ``upsert()`` (which
would silently start overwriting ``referenced=True`` rows).
"""

from __future__ import annotations

import pytest


@pytest.fixture
def store(db_engine):
    from backend.db.email_snapshot_store import EmailSnapshotStore

    return EmailSnapshotStore(engine=db_engine)


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


# ── stash / get — id_field="message_id" mapped onto the physical "id" column ──────────


def test_stash_then_get_round_trips(store):
    store.stash(_row("<m-1@x>"))
    row = store.get("<m-1@x>")
    assert row is not None
    assert row["subject"] == "Your statement is ready"
    assert row["referenced"] is False


def test_get_missing_returns_none(store):
    assert store.get("<never-stashed@x>") is None


def test_id_field_is_message_id_not_a_literal_id_key(store):
    """message_id IS the physical row's id column — there is no separate "id" key
    anywhere in the stored payload."""
    from sqlalchemy import select

    from backend.db.tables import email_snapshots

    store.stash(_row("<m-1@x>"))
    with store._eng().connect() as conn:
        row = conn.execute(select(email_snapshots).where(email_snapshots.c.id == "<m-1@x>")).one()
    assert row.payload["message_id"] == "<m-1@x>"
    assert "id" not in row.payload


# ── stash is FIRST-SEEN-WINS, never an upsert ──────────────────────────────────────────


def test_stash_dedupes_by_message_id_as_a_noop(store):
    """Re-stashing an already-known message_id is a stable no-op — the FIRST snapshot
    wins, a later re-stash (e.g. re-fetching the same email) never overwrites it."""
    store.stash(_row("<m-1@x>", stashed_at="2026-07-01T00:00:00+00:00"))
    changed = dict(_row("<m-1@x>", stashed_at="2026-07-05T00:00:00+00:00"))
    changed["subject"] = "A different subject"
    store.stash(changed)

    row = store.get("<m-1@x>")
    assert row["subject"] == "Your statement is ready"  # original wins
    assert row["stashed_at"] == "2026-07-01T00:00:00+00:00"
    assert len(store.all()) == 1


def test_stash_returns_the_existing_row_on_a_noop_restash(store):
    original = store.stash(_row("<m-1@x>"))
    again = store.stash({**_row("<m-1@x>"), "subject": "different"})
    assert again == original


def test_stash_referenced_flag_survives_a_noop_restash(store):
    store.stash(_row("<m-1@x>"))
    assert store.promote("<m-1@x>") is True
    store.stash(_row("<m-1@x>"))
    assert store.get("<m-1@x>")["referenced"] is True


def test_stash_never_calls_upsert(store, monkeypatch):
    """A structural guard against regressing back to upsert() — a raw upsert() would
    corrupt referenced=True rows (module docstring)."""
    calls: list = []
    orig_upsert = store.upsert

    def _spy(*a, **kw):
        calls.append((a, kw))
        return orig_upsert(*a, **kw)

    monkeypatch.setattr(store, "upsert", _spy)
    store.stash(_row("<m-1@x>"))
    store.stash(_row("<m-1@x>"))  # a re-stash too
    assert calls == []


# ── promote ──────────────────────────────────────────────────────────────────


def test_promote_flips_referenced_true(store):
    store.stash(_row("<m-1@x>"))
    assert store.promote("<m-1@x>") is True
    assert store.get("<m-1@x>")["referenced"] is True


def test_promote_is_idempotent(store):
    store.stash(_row("<m-1@x>"))
    assert store.promote("<m-1@x>") is True
    assert store.promote("<m-1@x>") is True
    assert store.get("<m-1@x>")["referenced"] is True


def test_promote_missing_message_id_returns_false(store):
    assert store.promote("<never-stashed@x>") is False


# ── prune_unreferenced — bulk delete-by-filter, keep-newest-N ──────────────────────────


def test_prune_keeps_referenced_and_newest_unreferenced(store):
    store.stash(_row("<ref@x>", referenced=True, stashed_at="2026-01-01T00:00:00+00:00"))
    store.stash(_row("<old@x>", stashed_at="2026-01-02T00:00:00+00:00"))
    store.stash(_row("<mid@x>", stashed_at="2026-01-03T00:00:00+00:00"))
    store.stash(_row("<new@x>", stashed_at="2026-01-04T00:00:00+00:00"))

    removed = store.prune_unreferenced(keep=2)
    assert removed == 1

    remaining_ids = {r["message_id"] for r in store.all()}
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


# ── Checkpoint INCLUSION — the one misc store this wave that stays covered ─────────────


def test_email_snapshots_table_is_not_checkpoint_excluded():
    from backend.ai.checkpoint import _EXCLUDED_TABLES

    assert "email_snapshots" not in _EXCLUDED_TABLES


def test_checkpoint_captures_and_reverts_a_stash(db_isolated):
    """Behavioral proof: unlike artifacts/uploads/pending_pushes, a snapshot stashed
    mid-turn IS captured and IS reverted by restore — durable user evidence."""
    from backend.ai import checkpoint as cp
    from backend.db.email_snapshot_store import EmailSnapshotStore

    store = EmailSnapshotStore()

    ctx, token = cp.begin_turn("s1", 0, "fetch inbox")
    store.stash(_row("<m-1@x>"))
    entry = cp.end_turn(ctx, token)

    assert entry is not None
    assert "email_snapshots" in entry["db_tables"]

    cp.restore(entry["id"], mode="data")
    assert store.get("<m-1@x>") is None  # reverted
