"""Behavioral tests for get_earlier_conversation_detail — the fold escape-hatch tool
(RFC context-folding-v2 §4). Read-only: reads this session's own transcript + .fold.json
sidecar (both already written elsewhere) and returns a bounded, filterable slice.
"""

from __future__ import annotations

import asyncio
import json

from backend.ai.tools import execute_tool
from backend.core.chat_session import clear_session_id, set_session_id


def _run(name: str, args: dict) -> dict:
    return json.loads(asyncio.run(execute_tool(name, args))[0])


def test_schema_never_accepts_a_caller_chosen_session_id():
    """The tool must be structurally unable to reach another session — no session_id
    (or any id-shaped) field in its schema at all, only get_session_id()'s ambient
    scope. This is the actual isolation guarantee, not just a runtime check."""
    from backend.ai.tools.registry import build_tool_schemas

    schema = next(s for s in build_tool_schemas() if s["name"] == "get_earlier_conversation_detail")
    assert "session" not in json.dumps(schema["input_schema"]).lower()


def test_no_active_session_returns_empty(monkeypatch):
    clear_session_id()
    out = _run("get_earlier_conversation_detail", {})
    assert out == {"ok": True, "entries": [], "showing": 0, "total": 0}


def test_session_never_folded_returns_empty_with_a_note():
    sid = "sess-1"
    set_session_id(sid)
    try:
        out = _run("get_earlier_conversation_detail", {})
    finally:
        clear_session_id()
    assert out["ok"] is True
    assert out["entries"] == []
    assert out["showing"] == 0
    assert out["total"] == 0
    assert "never been folded" in out["note"]


def _seed(history: list[dict], fold_data: dict) -> str:
    import backend.ai.assistant as asst

    sid = "sess-1"
    asst._save_session(sid, history)
    asst._save_fold(sid, fold_data)
    return sid


def test_returns_pre_fold_entries_newest_first():
    history = [
        {"role": "user", "content": "first: how much did I spend on groceries"},
        {"role": "assistant", "content": [{"type": "text", "text": "about SGD 400"}]},
        {"role": "user", "content": "second: and on transport"},
        {"role": "assistant", "content": [{"type": "text", "text": "about SGD 120"}]},
        {"role": "user", "content": "third: this is inside the current window, not folded"},
    ]
    sid = _seed(
        history,
        {"summary": "a summary", "folded_through_idx": 4, "generated_at_len": 5},
    )
    set_session_id(sid)
    try:
        out = _run("get_earlier_conversation_detail", {})
    finally:
        clear_session_id()

    assert out["ok"] is True
    assert out["total"] == 4
    assert out["showing"] == 4
    texts = [e["text"] for e in out["entries"]]
    # Newest-first (closest to the fold boundary), and the current-window message
    # (idx 4, at/after folded_through_idx) never appears.
    assert texts == [
        "about SGD 120",
        "second: and on transport",
        "about SGD 400",
        "first: how much did I spend on groceries",
    ]
    assert "this is inside the current window" not in json.dumps(out)


def test_query_hint_filters_case_insensitively():
    history = [
        {"role": "user", "content": "Meridian Multiplier balance please"},
        {"role": "assistant", "content": [{"type": "text", "text": "SGD 12,345.67"}]},
        {"role": "user", "content": "and my Lighthouse 360 account"},
        {"role": "assistant", "content": [{"type": "text", "text": "SGD 2,000.00"}]},
    ]
    sid = _seed(
        history,
        {"summary": "s", "folded_through_idx": 4, "generated_at_len": 4},
    )
    set_session_id(sid)
    try:
        out = _run("get_earlier_conversation_detail", {"query_hint": "meridian"})
    finally:
        clear_session_id()

    assert out["total"] == 1
    assert out["entries"] == [
        {"idx": 0, "role": "user", "text": "Meridian Multiplier balance please"}
    ]


def test_limit_caps_results_and_notes_truncation():
    history = [{"role": "user", "content": f"message {i}"} for i in range(10)]
    sid = _seed(
        history,
        {"summary": "s", "folded_through_idx": 10, "generated_at_len": 10},
    )
    set_session_id(sid)
    try:
        out = _run("get_earlier_conversation_detail", {"limit": 3})
    finally:
        clear_session_id()

    assert out["total"] == 10
    assert out["showing"] == 3
    assert len(out["entries"]) == 3
    assert "note" in out


def test_limit_is_capped_at_50_regardless_of_requested_value():
    history = [{"role": "user", "content": f"message {i}"} for i in range(80)]
    sid = _seed(
        history,
        {"summary": "s", "folded_through_idx": 80, "generated_at_len": 80},
    )
    set_session_id(sid)
    try:
        out = _run("get_earlier_conversation_detail", {"limit": 999})
    finally:
        clear_session_id()

    assert out["showing"] == 50


def test_writes_nothing():
    """Purely a read — the session transcript and fold column are byte-unchanged after
    the call. Sessions are DB-backed now (Wave 5E) — no more per-session file/sidecar
    to read_text(); compare the SessionStore-native equivalents instead."""
    from backend.db.session_store import SessionStore

    history = [{"role": "user", "content": "hello"}]
    fold_data = {"summary": "s", "folded_through_idx": 1, "generated_at_len": 1}
    sid = _seed(history, fold_data)

    store = SessionStore()
    before_transcript = store.get_transcript(sid)
    before_fold = store.get_fold(sid)

    set_session_id(sid)
    try:
        _run("get_earlier_conversation_detail", {})
    finally:
        clear_session_id()

    assert store.get_transcript(sid) == before_transcript
    assert store.get_fold(sid) == before_fold
