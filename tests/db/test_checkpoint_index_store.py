"""CheckpointIndexStore on PayloadTableStore (plan 4e, task 2) — the checkpoint manifest's
new DB home, replacing the old ``CHECKPOINTS_INDEX`` JSON file. ``checkpointed=False`` at
construction (mirrors ``PendingPushStore``'s precedent), ``payload`` is the whole entry
dict verbatim — no promoted columns (table is hard-capped at ``MAX_CHECKPOINTS``).

Also covers the one genuine end-to-end behavior change this task makes: a turn that
touches BOTH a still-Tier-4 file and a DB row lands its manifest in the new ``checkpoints``
table, and ``restore()`` reverts both halves reading that manifest back.
"""

from __future__ import annotations

import json

import pytest


@pytest.fixture
def store(db_engine):
    from backend.db.checkpoint_index_store import CheckpointIndexStore

    return CheckpointIndexStore(engine=db_engine)


def _entry(cp_id: str, **overrides) -> dict:
    row = {
        "id": cp_id,
        "session_id": "sess-1",
        "anchor_history_len": 0,
        "created_at": "2026-08-09T00:00:00+00:00",
        "message_preview": "did a thing",
        "files": {"accounts.json": "sha256:deadbeef"},
        "status": "active",
        "kind": "turn",
    }
    row.update(overrides)
    return row


# ── CRUD parity with the JsonStore/index.json era ───────────────────────────────


def test_insert_then_find_by_id(store):
    store.insert(_entry("ckpt_a"))
    found = store.find_by_id("ckpt_a")
    assert found is not None
    assert found["session_id"] == "sess-1"
    assert found["status"] == "active"


def test_find_by_id_missing_is_none(store):
    assert store.find_by_id("ckpt_nope") is None


def test_all_returns_every_row_in_insertion_order(store):
    store.insert(_entry("ckpt_1"))
    store.insert(_entry("ckpt_2"))
    store.insert(_entry("ckpt_3"))
    assert [e["id"] for e in store.all()] == ["ckpt_1", "ckpt_2", "ckpt_3"]


def test_update_by_id_patches_in_place(store):
    store.insert(_entry("ckpt_a"))
    updated = store.update_by_id("ckpt_a", {"status": "orphaned"})
    assert updated["status"] == "orphaned"
    assert store.find_by_id("ckpt_a")["status"] == "orphaned"
    # every other field survives the patch untouched
    assert store.find_by_id("ckpt_a")["session_id"] == "sess-1"


def test_update_by_id_missing_id_is_noop_returns_none(store):
    assert store.update_by_id("ckpt_ghost", {"status": "orphaned"}) is None


def test_delete_removes_the_row(store):
    store.insert(_entry("ckpt_a"))
    assert store.delete("ckpt_a") is True
    assert store.find_by_id("ckpt_a") is None
    assert store.all() == []


def test_delete_missing_id_returns_false(store):
    assert store.delete("ckpt_ghost") is False


def test_query_filters_by_payload_field(store):
    store.insert(_entry("ckpt_a", session_id="sess-A"))
    store.insert(_entry("ckpt_b", session_id="sess-B"))
    store.insert(_entry("ckpt_c", session_id="sess-A"))
    matches = store.query(session_id="sess-A")
    assert {e["id"] for e in matches} == {"ckpt_a", "ckpt_c"}


def test_persisted_row_survives_a_fresh_store_instance(store, db_engine):
    from backend.db.checkpoint_index_store import CheckpointIndexStore

    store.insert(_entry("ckpt_a"))
    fresh = CheckpointIndexStore(engine=db_engine)
    assert len(fresh.all()) == 1
    assert fresh.find_by_id("ckpt_a")["id"] == "ckpt_a"


# ── payload is the WHOLE entry verbatim — no promoted columns ──────────────────


def test_payload_round_trips_every_field_verbatim(store):
    """The manifest carries fields no other Tier-2 store does (files/db_tables/receipt) —
    prove they all survive a write + re-read with no promoted-column loss."""
    entry = _entry(
        "ckpt_full",
        files={"transactions.json": "sha256:abc123", "accounts.json": None},
        db_tables=["books", "transactions"],
        receipt={"checkpoint_id": "ckpt_full", "kind": "transaction", "count": 2},
    )
    store.insert(entry)
    found = store.find_by_id("ckpt_full")
    assert found == entry


# ── checkpointed=False — construction-time hook opt-out ─────────────────────────


def test_store_constructs_with_checkpointed_false(store):
    assert store._checkpointed is False


def test_checkpoints_table_is_checkpoint_excluded():
    from backend.ai.checkpoint import _EXCLUDED_TABLES

    assert "checkpoints" in _EXCLUDED_TABLES


def test_checkpoint_does_not_capture_or_revert_its_own_index_writes(db_isolated):
    """Behavioral proof, not just the frozenset/flag: the checkpoints table itself is
    never recorded into write_log even while a turn is active — a restore must never try
    to revert the checkpoint mechanism's own bookkeeping (that would recurse)."""
    from backend.ai import checkpoint as cp
    from backend.db import tables as t
    from backend.db.engine import get_engine

    ctx, token = cp.begin_turn("s1", 0, "a turn")
    cp.end_turn(ctx, token)

    with get_engine().connect() as conn:
        from sqlalchemy import func, select

        n = conn.execute(
            select(func.count())
            .select_from(t.write_log)
            .where(t.write_log.c.table_name == "checkpoints")
        ).scalar_one()
    assert n == 0, "the checkpoints table must never appear in write_log — it would recurse"


# ── End-to-end: a turn touching a Tier-4 FILE and a DB ROW together ─────────────


def test_mixed_turn_manifest_lands_in_new_table_and_restore_reverts_both(
    db_isolated, tmp_path, monkeypatch
):
    """The one genuine end-to-end proof this task requires: begin a turn, write a
    still-Tier-4 file (a skill-shaped JSON file under DATA_DIR) AND a DB row, end the
    turn, confirm the manifest entry now lives in the CheckpointIndexStore-backed
    `checkpoints` table (not a JSON file), then restore() and confirm BOTH halves
    revert — proving _index.py's DB-backed find_checkpoint/insert/prune wiring is
    correctly threaded through the real begin_turn/end_turn/restore lifecycle."""
    from sqlalchemy import insert, select

    from backend.ai import checkpoint as cp
    from backend.core.io import fire_db_write_hook, fire_write_hook
    from backend.db import engine as eng
    from backend.db import tables as t
    from backend.db.checkpoint_index_store import CheckpointIndexStore

    f = cp.DATA_DIR / "mixed_e2e_probe.json"
    f.write_text(json.dumps([{"id": "old"}]))

    with eng.get_engine().begin() as c:
        c.execute(
            insert(t.books).values(id="book_e2e", name="Before", base_currency="", created_at="")
        )

    ctx, token = cp.begin_turn("sess-e2e", anchor_history_len=0, message_preview="mixed turn")
    fire_write_hook(f)  # file half — pre-write snapshot captured
    f.write_text(json.dumps([{"id": "new"}]))
    fire_db_write_hook(
        "books",
        [("book_e2e", {"id": "book_e2e", "name": "Before", "base_currency": "", "created_at": ""})],
    )
    with eng.get_engine().begin() as c:
        c.execute(t.books.update().where(t.books.c.id == "book_e2e").values(name="After"))
    entry = cp.end_turn(ctx, token)

    assert entry is not None
    assert "mixed_e2e_probe.json" in entry["files"]
    assert "books" in entry["db_tables"]

    # The manifest is now a row in the checkpoints TABLE, not a JSON file.
    index_store = CheckpointIndexStore()
    row = index_store.find_by_id(entry["id"])
    assert row is not None
    assert row["files"]["mixed_e2e_probe.json"] is not None
    assert row["db_tables"] == ["books"]

    # find_checkpoint (the public ai.checkpoint API) reads the same DB-backed row.
    assert cp.find_checkpoint(entry["id"]) == row

    cp.restore(entry["id"], mode="data")

    assert json.loads(f.read_text()) == [{"id": "old"}], "file half must revert"
    with eng.get_engine().connect() as c:
        assert (
            c.execute(select(t.books.c.name).where(t.books.c.id == "book_e2e")).scalar() == "Before"
        ), "db half must revert"
    f.unlink()
