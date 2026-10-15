"""Undoing a restore gives the later messages their Restore back.

Restoring to before message 1 marks the conversation's LATER turn checkpoints orphaned, so
their Restore buttons disappear. Undo used to leave them orphaned — and the very next
checkpoint the Undo itself wrote pruned them for good — so the later messages lost their
Restore even though the data was back exactly as it had been. Each restore now records the
turns it superseded on its own pre_restore (undo) checkpoint; undoing it revives exactly
those, and pruning keeps them alive while that undo is still possible.
"""

from __future__ import annotations

import pytest
from sqlalchemy import insert, select, update

from backend.ai import assistant
from backend.ai import checkpoint as cp
from backend.core.io import fire_db_write_hook
from backend.db import engine as eng
from backend.db import tables as t

SESSION = "s-revive"


def _history(turns: int) -> list[dict]:
    out: list[dict] = []
    for n in range(1, turns + 1):
        out.append({"role": "user", "content": f"message {n}"})
        out.append({"role": "assistant", "content": [{"type": "text", "text": f"done {n}"}]})
    return out


def _book(book_id: str, name: str) -> dict:
    return {"id": book_id, "name": name, "base_currency": "", "created_at": ""}


def _add(book_id: str):
    def write():
        fire_db_write_hook("books", [(book_id, None)])
        with eng.get_engine().begin() as c:
            c.execute(insert(t.books).values(**_book(book_id, book_id)))

    return write


def _rename(book_id: str, old: str, new: str):
    def write():
        fire_db_write_hook("books", [(book_id, _book(book_id, old))])
        with eng.get_engine().begin() as c:
            c.execute(update(t.books).where(t.books.c.id == book_id).values(name=new))

    return write


def _turn(n: int, write) -> str:
    """Turn n's user message sits at transcript index 2*(n-1)."""
    ctx, token = cp.begin_turn(SESSION, 2 * (n - 1), f"message {n}")
    write()
    return cp.end_turn(ctx, token)["id"]


def _books() -> dict[str, str]:
    with eng.get_engine().connect() as c:
        rows = c.execute(select(t.books.c.id, t.books.c.name)).all()
    return {i: n for i, n in rows if i in {"A", "B", "C"}}


def _users() -> list[dict]:
    return [m for m in assistant.rehydrate_messages(SESSION) or [] if m["role"] == "user"]


def _state(msg: dict) -> str:
    if msg.get("checkpointSuperseded"):
        return "superseded"
    if msg.get("restored"):
        return "restored" if msg.get("undoCheckpointId") else "restored-no-undo"
    return "restore" if msg.get("checkpointId") else "none"


def _states() -> list[str]:
    return [_state(m) for m in _users()]


@pytest.fixture
def three_turns(db_isolated, monkeypatch):
    """Message 1 adds A, message 2 adds B (a different row), message 3 renames A."""
    monkeypatch.setattr(assistant, "_load_session", lambda sid: _history(3))
    ids = [_turn(1, _add("A")), _turn(2, _add("B")), _turn(3, _rename("A", "A", "A3"))]
    assert _books() == {"A": "A3", "B": "B"}
    assert _states() == ["restore", "restore", "restore"]
    return ids


def test_restore_supersedes_later_turns_and_undo_revives_them(three_turns):
    c1, c2, c3 = three_turns
    result = cp.restore(c1, mode="data")
    assert sorted(result["superseded_checkpoint_ids"]) == sorted([c2, c3])
    assert _states() == ["restored", "superseded", "superseded"]
    superseded = _users()[1:]
    assert [m["checkpointId"] for m in superseded] == [c2, c3]

    undo = cp.restore(result["undo_checkpoint_id"], mode="data")
    assert sorted(undo["revived_checkpoint_ids"]) == sorted([c2, c3])
    assert _books() == {"A": "A3", "B": "B"}, "Undo puts the data back as it was"
    assert _states() == ["restore", "restore", "restore"]
    assert [m["checkpointId"] for m in _users()] == [c1, c2, c3]


def test_a_revived_turn_can_itself_be_restored_and_undone(three_turns):
    c1, c2, c3 = three_turns
    cp.restore(cp.restore(c1, mode="data")["undo_checkpoint_id"], mode="data")

    result = cp.restore(c3, mode="data")
    assert _books() == {"A": "A", "B": "B"}
    assert _states() == ["restore", "restore", "restored"]
    cp.restore(result["undo_checkpoint_id"], mode="data")
    assert _books() == {"A": "A3", "B": "B"}
    assert _states() == ["restore", "restore", "restore"]


def test_the_superseded_turns_survive_the_prune_the_undo_itself_triggers(three_turns):
    """The undo writes its own pre_restore checkpoint, whose finalise() prunes. Without the
    keep rule that prune deleted the orphaned turns before they could be revived."""
    c1, c2, c3 = three_turns
    result = cp.restore(c1, mode="data")
    cp._prune_old_checkpoints()  # what any intervening finalise() does
    assert cp.find_checkpoint(c2) is not None
    assert cp.find_checkpoint(c3) is not None

    cp.restore(result["undo_checkpoint_id"], mode="data")
    assert {cp.find_checkpoint(i)["status"] for i in (c2, c3)} == {"active"}


def test_a_new_turn_after_the_restore_does_not_lose_the_revival(three_turns, monkeypatch):
    c1, c2, c3 = three_turns
    result = cp.restore(c1, mode="data")
    monkeypatch.setattr(assistant, "_load_session", lambda sid: _history(4))
    c4 = _turn(4, _add("C"))  # finalise() → prune
    assert _states() == ["restored", "superseded", "superseded", "restore"]

    cp.restore(result["undo_checkpoint_id"], mode="data")
    # Message 4 was written after the restore, so the undo rewinds past it — superseded
    # in turn, and revivable by undoing the undo.
    assert _states()[:3] == ["restore", "restore", "restore"]
    assert cp.find_checkpoint(c4)["status"] == "orphaned"


def test_nested_restores_unwind_one_level_at_a_time(three_turns):
    c1, c2, c3 = three_turns
    first = cp.restore(c3, mode="data")
    assert _states() == ["restore", "restore", "restored"]
    second = cp.restore(c1, mode="data")
    assert _states() == ["restored", "superseded", "superseded"]

    cp.restore(second["undo_checkpoint_id"], mode="data")
    assert _books() == {"A": "A", "B": "B"}, "back to after the first restore"
    assert _states() == ["restore", "restore", "restored"]
    assert _users()[2]["undoCheckpointId"] == first["undo_checkpoint_id"]

    cp.restore(first["undo_checkpoint_id"], mode="data")
    assert _books() == {"A": "A3", "B": "B"}
    assert _states() == ["restore", "restore", "restore"]


def test_a_rewind_does_not_keep_the_later_turns_for_revival(three_turns, monkeypatch):
    """ "Restore data & rewind chat" removes the later messages, so nothing can show them
    again — keeping their checkpoints would only leak them past the prune."""
    c1, c2, c3 = three_turns
    monkeypatch.setattr(cp, "_truncate_session_history", lambda sid, n: n)
    cp.restore(c1, mode="both")
    cp._prune_old_checkpoints()
    assert cp.find_checkpoint(c2) is None
    assert cp.find_checkpoint(c3) is None


def test_the_kept_turns_go_once_their_undo_checkpoint_is_pruned(three_turns, monkeypatch):
    c1, c2, c3 = three_turns
    result = cp.restore(c1, mode="data")
    assert cp.delete_checkpoint(result["undo_checkpoint_id"])
    cp._prune_old_checkpoints()
    assert cp.find_checkpoint(c2) is None
    assert cp.find_checkpoint(c3) is None
    assert _states() == ["restored-no-undo", "none", "none"]


def test_another_conversations_turns_are_never_superseded(three_turns, monkeypatch):
    c1, _, _ = three_turns
    ctx, token = cp.begin_turn("s-other", 0, "elsewhere")
    _add("C")()
    other = cp.end_turn(ctx, token)["id"]
    result = cp.restore(c1, mode="data")
    assert other not in result["superseded_checkpoint_ids"]
    assert cp.find_checkpoint(other)["status"] == "active"


def test_undo_revives_even_at_a_tight_checkpoint_cap(three_turns, monkeypatch):
    """At cap the undo's own pre_restore finalise() prunes hard. The undo target is pinned
    for the restore, so the turns it would revive are kept through that prune."""
    c1, c2, c3 = three_turns
    result = cp.restore(c1, mode="data")
    monkeypatch.setattr(cp, "MAX_CHECKPOINTS", 1)
    cp.restore(result["undo_checkpoint_id"], mode="data")
    assert _books() == {"A": "A3", "B": "B"}
    assert {cp.find_checkpoint(i)["status"] for i in (c2, c3)} == {"active"}
