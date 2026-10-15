"""A write receipt belongs to the turn that earned it, and to no later one.

The chip ("1 transaction edited") kept appearing on turns that modified nothing. `_land_receipt`
only fired when the NEXT receipt anchor came along, so a receipt from a writing turn slid
forward through every turn that wrote nothing and landed on whatever assistant message the walk
happened to end on. With no later write at all — the common case — that is the last message in
the conversation, which is why it looked like the chip simply stuck around.

Reported 2026-08-23, reproduced on a real transcript: the message carrying "created: 1" had
one tool on it, `get_portfolio`.
"""

from __future__ import annotations

from backend.ai import assistant

RECEIPT = {"created": 1, "updated": 0, "deleted": 0, "total": 1}


def _history() -> list[dict]:
    """A writing turn, then two turns that write nothing."""
    return [
        {"role": "user", "content": "log that payment"},
        {
            "role": "assistant",
            "content": [{"type": "tool_use", "id": "tu-1", "name": "add_transaction", "input": {}}],
        },
        {
            "role": "user",
            "content": [{"type": "tool_result", "tool_use_id": "tu-1", "content": '{"ok":true}'}],
        },
        {"role": "assistant", "content": [{"type": "text", "text": "Logged it."}]},
        {"role": "user", "content": "and what is my portfolio?"},
        {
            "role": "assistant",
            "content": [{"type": "tool_use", "id": "tu-2", "name": "get_portfolio", "input": {}}],
        },
        {
            "role": "user",
            "content": [{"type": "tool_result", "tool_use_id": "tu-2", "content": '{"ok":true}'}],
        },
        {"role": "assistant", "content": [{"type": "text", "text": "Here it is."}]},
        {"role": "user", "content": "thanks"},
        {"role": "assistant", "content": [{"type": "text", "text": "Any time."}]},
    ]


def _assistants_with_receipt(messages: list[dict]) -> list[dict]:
    return [m for m in messages if any(b.get("type") == "receipt" for b in (m.get("blocks") or []))]


def _patch(monkeypatch, history: list[dict], anchors: dict[int, dict]) -> None:
    from backend.ai import checkpoint as ckpt

    monkeypatch.setattr(assistant, "_load_session", lambda sid: history)
    monkeypatch.setattr(ckpt, "turn_receipts", lambda sid: anchors)
    monkeypatch.setattr(ckpt, "turn_anchors", lambda sid: {})


def test_the_receipt_lands_on_the_turn_that_wrote(monkeypatch):
    # Anchored to index 0 — the user message that opened the WRITING turn.
    _patch(monkeypatch, _history(), {0: RECEIPT})

    carrying = _assistants_with_receipt(assistant.rehydrate_messages("s1") or [])
    assert len(carrying) == 1, "exactly one message carries it"
    tools = [b.get("name") for b in carrying[0]["blocks"] if b.get("type") == "tool"]
    assert tools == ["add_transaction"], f"landed on the wrong turn: {tools}"


def test_it_does_not_slide_onto_a_later_read_only_turn(monkeypatch):
    _patch(monkeypatch, _history(), {0: RECEIPT})

    messages = assistant.rehydrate_messages("s1") or []
    for m in messages:
        blocks = m.get("blocks") or []
        if not any(b.get("type") == "receipt" for b in blocks):
            continue
        names = [b.get("name") for b in blocks if b.get("type") == "tool"]
        assert "get_portfolio" not in names, "a read-only turn is carrying a write receipt"
    # And nothing lands on the last message, which is where the slide always ended up.
    assert not _assistants_with_receipt(messages[-1:])


def test_two_writing_turns_each_keep_their_own(monkeypatch):
    history = _history()
    # Make the second turn a write as well, with its own anchor.
    history[5]["content"] = [
        {"type": "tool_use", "id": "tu-2", "name": "update_transactions", "input": {}}
    ]
    _patch(monkeypatch, history, {0: RECEIPT, 4: {**RECEIPT, "updated": 2}})

    carrying = _assistants_with_receipt(assistant.rehydrate_messages("s1") or [])
    assert len(carrying) == 2
    landed = [[b.get("name") for b in m["blocks"] if b.get("type") == "tool"] for m in carrying]
    assert landed == [["add_transaction"], ["update_transactions"]]


def test_a_receipt_with_no_assistant_message_is_dropped_not_carried(monkeypatch):
    """Nowhere honest to put it beats somewhere wrong — the slide is what this replaces."""
    history = [
        {"role": "user", "content": "do it"},
        {"role": "user", "content": "actually, what is my portfolio?"},
        {
            "role": "assistant",
            "content": [{"type": "tool_use", "id": "tu-1", "name": "get_portfolio", "input": {}}],
        },
    ]
    _patch(monkeypatch, history, {0: RECEIPT})

    assert not _assistants_with_receipt(assistant.rehydrate_messages("s1") or [])
