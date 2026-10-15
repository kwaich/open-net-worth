"""Reasoning rehydrates WHERE IT HAPPENED, not merged above everything.

A turn thinks, calls a tool, then thinks again. Each provider round appends its own
``thinking`` content block (``run()`` resets ``assistant_thinking`` per round), interleaved
with that round's ``tool_use`` blocks — so the transcript already records when each thought
occurred. ``rehydrate_messages`` used to concatenate them all into ``msg["thinking"]``, a
single string the UI could only draw above the first tool row, including the half that came
after it (owner report, 2026-08-23).

``thinking`` is still accumulated: it stays the message-level field older readers use, and
the fallback for a transcript recorded before thinking became a positioned block.
"""

from __future__ import annotations

from backend.ai import assistant


def _history() -> list[dict]:
    return [
        {"role": "user", "content": "how much did I spend?"},
        {
            "role": "assistant",
            "content": [
                {"type": "thinking", "thinking": "I need the ledger first."},
                {
                    "type": "tool_use",
                    "id": "tu-1",
                    "name": "get_transactions",
                    "input": {"month": "2026-08"},
                },
            ],
        },
        {
            "role": "user",
            "content": [{"type": "tool_result", "tool_use_id": "tu-1", "content": '{"ok": true}'}],
        },
        {
            "role": "assistant",
            "content": [
                {"type": "thinking", "thinking": "Nothing there, so I will say so."},
                {"type": "text", "text": "You spent nothing."},
            ],
        },
    ]


def test_each_round_rehydrates_as_its_own_thinking_block_in_order(monkeypatch):
    monkeypatch.setattr(assistant, "_load_session", lambda sid: _history())

    messages = assistant.rehydrate_messages("sess-1")
    assistant_msgs = [m for m in messages if m.get("role") == "assistant"]
    blocks = [b for m in assistant_msgs for b in (m.get("blocks") or [])]
    kinds = [b["type"] for b in blocks]

    # The tool sits BETWEEN the two thoughts — that is the whole point.
    assert kinds.index("thinking") < kinds.index("tool"), kinds
    assert kinds.count("thinking") == 2, kinds
    assert kinds.index("tool") < [i for i, k in enumerate(kinds) if k == "thinking"][1], kinds

    contents = [b["content"] for b in blocks if b["type"] == "thinking"]
    assert contents == ["I need the ledger first.", "Nothing there, so I will say so."]


def test_the_merged_field_is_still_populated_for_older_readers(monkeypatch):
    monkeypatch.setattr(assistant, "_load_session", lambda sid: _history())

    messages = assistant.rehydrate_messages("sess-1")
    merged = "".join(m.get("thinking") or "" for m in messages if m.get("role") == "assistant")
    assert "I need the ledger first." in merged
    assert "Nothing there, so I will say so." in merged


def test_a_redacted_thinking_block_is_positioned_too(monkeypatch):
    """`redacted_thinking` carries no readable text of its own, so it contributes nothing to
    render — and must not manufacture an empty block that draws as a blank thought."""
    history = [
        {"role": "user", "content": "hi"},
        {
            "role": "assistant",
            "content": [
                {"type": "redacted_thinking", "data": "opaque"},
                {"type": "text", "text": "hello"},
            ],
        },
    ]
    monkeypatch.setattr(assistant, "_load_session", lambda sid: history)

    messages = assistant.rehydrate_messages("sess-1")
    blocks = [b for m in messages for b in (m.get("blocks") or [])]
    assert [b["type"] for b in blocks] == ["text"]
