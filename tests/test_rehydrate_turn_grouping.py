"""Regression: one tool-loop turn must rehydrate as ONE bubble, not one per round.

Bug (regression, 2026-08-09): reopening a completed Automation run showed TWO
"Copy" buttons for what was, live, a single continuous reply. `MessageBubble.tsx`
renders one `CopyButton` per UI message. Live, `chatReducer.ensureAssistant` reuses
the trailing assistant bubble for the whole turn — however many `tool_use` rounds it
takes — so a mid-loop text aside ("no need to check lots.") and the final answer both
land in the SAME bubble, with ONE Copy button copying the concatenated text.

`assistant.rehydrate_messages` (used on reload/reopen — `api/chat.py`'s history
endpoint) instead created a NEW UI message for every raw "assistant" transcript
entry, i.e. one per model API round-trip. Any round that happened to carry text (an
aside, not just a final answer) got its own bubble and its own Copy button — a
reload rendered a different conversation than the one the user watched stream in.

Fix: track whether the "user" entry just processed was a real new turn (typed text /
an attachment) or a pure `tool_result` continuation of the same loop. An assistant
round that continues the same loop merges into the turn's existing bubble instead of
opening a new one.
"""

from __future__ import annotations

from backend.ai import assistant


def _assistant_msgs(messages: list[dict]) -> list[dict]:
    return [m for m in messages if m["role"] == "assistant"]


def test_a_multi_round_tool_loop_with_a_mid_loop_aside_rehydrates_as_one_bubble(
    monkeypatch,
):
    # Mirrors the regression's shape: several silent tool rounds, then a round with
    # BOTH text and a tool call ("Two buy transactions found... no need to check
    # lots."), then more silent tool rounds, ending on a text-only final round
    # ("Daily Vantage Brokerage summary sent...").
    history = [
        {"role": "user", "content": "run the daily summary"},
        {
            "role": "assistant",
            "content": [{"type": "tool_use", "id": "tu-1", "name": "list_accounts", "input": {}}],
        },
        {
            "role": "user",
            "content": [{"type": "tool_result", "tool_use_id": "tu-1", "content": "[]"}],
        },
        {
            "role": "assistant",
            "content": [
                {"type": "text", "text": "Two buy transactions found, no need to check lots."},
                {"type": "tool_use", "id": "tu-2", "name": "update_run_task", "input": {}},
            ],
        },
        {
            "role": "user",
            "content": [{"type": "tool_result", "tool_use_id": "tu-2", "content": "ok"}],
        },
        {
            "role": "assistant",
            "content": [
                {"type": "tool_use", "id": "tu-3", "name": "telegram_send_message", "input": {}}
            ],
        },
        {
            "role": "user",
            "content": [{"type": "tool_result", "tool_use_id": "tu-3", "content": "ok"}],
        },
        {
            "role": "assistant",
            "content": [
                {"type": "text", "text": "Daily Vantage Brokerage summary sent for 2026-08-08."}
            ],
        },
    ]
    monkeypatch.setattr(assistant, "_load_session", lambda sid: history)

    messages = assistant.rehydrate_messages("sess-1")

    asst = _assistant_msgs(messages)
    assert len(asst) == 1, "the whole tool loop is ONE turn — one bubble, one Copy button"
    assert asst[0]["text"] == (
        "Two buy transactions found, no need to check lots.Daily Vantage Brokerage summary sent for 2026-08-08."
    )
    tool_names = [b["name"] for b in asst[0]["blocks"] if b["type"] == "tool"]
    assert tool_names == ["list_accounts", "update_run_task", "telegram_send_message"]


def test_two_separate_turns_still_get_two_separate_bubbles(monkeypatch):
    history = [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": [{"type": "text", "text": "hello"}]},
        {"role": "user", "content": "what's my balance"},
        {
            "role": "assistant",
            "content": [{"type": "tool_use", "id": "tu-1", "name": "get_net_worth", "input": {}}],
        },
        {
            "role": "user",
            "content": [{"type": "tool_result", "tool_use_id": "tu-1", "content": "{}"}],
        },
        {"role": "assistant", "content": [{"type": "text", "text": "$42."}]},
    ]
    monkeypatch.setattr(assistant, "_load_session", lambda sid: history)

    messages = assistant.rehydrate_messages("sess-1")

    asst = _assistant_msgs(messages)
    assert [m["text"] for m in asst] == ["hello", "$42."]


def test_a_mid_turn_injected_user_message_still_opens_its_own_bubble(monkeypatch):
    # The injected-message fence (ADR 2026-08-06) marks text the reader actually typed
    # mid-turn — it must keep opening a fresh user bubble and end the merge chain,
    # never get folded away as if it were a tool_result continuation.
    history = [
        {"role": "user", "content": "start the run"},
        {
            "role": "assistant",
            "content": [{"type": "tool_use", "id": "tu-1", "name": "list_accounts", "input": {}}],
        },
        {
            "role": "user",
            "content": [{"type": "tool_result", "tool_use_id": "tu-1", "content": "[]"}],
        },
        {
            "role": "user",
            "content": [{"type": "text", "text": "«user_msg:ab12»wait, stop«/user_msg:ab12»"}],
        },
        {"role": "assistant", "content": [{"type": "text", "text": "Stopped."}]},
    ]
    monkeypatch.setattr(assistant, "_load_session", lambda sid: history)

    messages = assistant.rehydrate_messages("sess-1")

    roles_and_text = [(m["role"], m["text"]) for m in messages]
    assert roles_and_text[-2:] == [("user", "wait, stop"), ("assistant", "Stopped.")]
