"""A restored message stays restored across a reload — and so does its Undo.

"✓ Restored to before this message · Undo" lived only in the browser that clicked Restore:
the server recorded nothing on the turn checkpoint, so ``rehydrate_messages`` could only
re-attach the plain Restore button, and the Undo — the one way back from a restore — vanished
on reload and never reached another device (visual QA, 2026-10-12). The mark now rides the
turn checkpoint, exactly as the Restore anchor and the write receipt already do.
"""

from __future__ import annotations

import pytest
from sqlalchemy import insert

from backend.ai import assistant
from backend.ai import checkpoint as cp
from backend.core.io import fire_db_write_hook
from backend.db import engine as eng
from backend.db import tables as t

SESSION = "s-restore"


def _history() -> list[dict]:
    return [
        {"role": "user", "content": "add a book"},
        {"role": "assistant", "content": [{"type": "text", "text": "Added."}]},
    ]


@pytest.fixture
def turn(db_isolated, monkeypatch):
    """One real turn that inserted a row, anchored at transcript index 0."""
    monkeypatch.setattr(assistant, "_load_session", lambda sid: _history())
    ctx, token = cp.begin_turn(SESSION, 0, "add a book")
    fire_db_write_hook("books", [("book_r", None)])
    with eng.get_engine().begin() as c:
        c.execute(insert(t.books).values(id="book_r", name="R", base_currency="", created_at=""))
    return cp.end_turn(ctx, token)


def _user_message() -> dict:
    messages = assistant.rehydrate_messages(SESSION) or []
    return next(m for m in messages if m["role"] == "user")


def test_a_fresh_turn_rehydrates_unrestored(turn):
    msg = _user_message()
    assert msg["checkpointId"] == turn["id"]
    assert not msg.get("restored")
    assert "undoCheckpointId" not in msg


def test_a_data_restore_rehydrates_restored_with_its_undo(turn):
    result = cp.restore(turn["id"], mode="data")

    msg = _user_message()
    assert msg["checkpointId"] == turn["id"]
    assert msg["restored"] is True
    assert msg["undoCheckpointId"] == result["undo_checkpoint_id"]


def test_undoing_the_restore_rehydrates_the_plain_restore_again(turn):
    result = cp.restore(turn["id"], mode="data")
    cp.restore(result["undo_checkpoint_id"], mode="data")

    msg = _user_message()
    assert msg["checkpointId"] == turn["id"]
    assert not msg.get("restored")
    assert "undoCheckpointId" not in msg


def test_an_evicted_undo_checkpoint_leaves_restored_without_a_broken_undo(turn):
    result = cp.restore(turn["id"], mode="data")
    assert cp.delete_checkpoint(result["undo_checkpoint_id"])

    msg = _user_message()
    assert msg["restored"] is True
    assert "undoCheckpointId" not in msg


def test_a_conversation_only_rewind_marks_nothing(turn, monkeypatch):
    monkeypatch.setattr(cp, "_truncate_session_history", lambda sid, n: n)
    cp.restore(turn["id"], mode="conversation")

    assert not _user_message().get("restored")
