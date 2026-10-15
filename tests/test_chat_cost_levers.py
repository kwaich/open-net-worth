"""Regression tests for the chat-cost levers (June cost-log audit).

Three fixes, all driven by the live cost log (chat = $78.59 of $84.45 total):
1. get_payees is capped (default 50 rows) and supports query — the full ~300-row payee
   book (~13-16k tokens) no longer lands in chat history wholesale.
2. The volatile system-prompt half (date/portfolio/FX/memory + response style) is
   injected into THIS turn's user message instead of the system param, so a snapshot
   change can no longer cache-bust the conversation prefix (which re-wrote the whole
   history at the 1.25x cache-write rate every turn).
3. The history sliding window advances in chunks (_WINDOW_SLIDE_CHUNK), not
   message-by-message, so the cached prefix stays byte-stable across turns in long
   sessions instead of busting on every call.
"""

from __future__ import annotations

import asyncio
import json

from backend.ai import assistant
from backend.ai.prompts import CACHE_SPLIT_MARKER

# ── 1. get_payees cap + query ────────────────────────────────────────────────


def _registry_rows(n: int) -> list[dict]:
    return [
        {
            "id": f"p{i}",
            "name": f"Entity {i}",
            "default_category": None,
            "autocategorize": False,
            "hidden": False,
            "aliases": [f"alias {i}"],
            "txn_count": n - i,
            "last_used": "2026-06-01",
        }
        for i in range(n)
    ]


def test_get_payees_cap_and_alias_query(monkeypatch):
    from backend.ai.tools import execute_tool
    from backend.services import payee_registry

    monkeypatch.setattr(payee_registry, "list_registry", lambda **_: _registry_rows(120))
    text, _ = asyncio.run(execute_tool("get_payees", {}))
    body = json.loads(text)
    assert body["count"] == 50
    assert body["total"] == 120

    text, _ = asyncio.run(execute_tool("get_payees", {"query": "alias 119"}))
    body = json.loads(text)
    assert body["total"] == 1
    assert body["payees"][0]["id"] == "p119"


def test_get_payees_by_id_exact_fetch_and_not_found(monkeypatch):
    from backend.ai.tools import execute_tool
    from backend.core.errors import NotFoundError
    from backend.services import payee_registry

    row = {"id": "pay_42", "name": "Acme", "aliases": ["acme"], "rules": [], "txn_count": 0}

    def fake_require(pid):
        if pid == "pay_42":
            return row
        raise NotFoundError(f"payee {pid!r} not found")

    monkeypatch.setattr(payee_registry, "require_payee", fake_require)
    # list_registry() is now called for the live projection; stub it too
    monkeypatch.setattr(payee_registry, "list_registry", lambda **_: [row])

    text, proposed = asyncio.run(execute_tool("get_payees", {"payee_id": "pay_42"}))
    assert proposed is None
    assert json.loads(text)["payee"]["name"] == "Acme"

    text, _ = asyncio.run(execute_tool("get_payees", {"payee_id": "pay_nope"}))
    assert "not found" in json.loads(text)["error"]


# ── 2. volatile half + style → per-turn context, out of the system param ────


def test_split_turn_context_with_both_markers():
    from backend.ai.prompts import TURN_SPLIT_MARKER

    system = (
        "STATIC"
        + CACHE_SPLIT_MARKER
        + "SEMI_STABLE profile"
        + TURN_SPLIT_MARKER
        + "TODAY: 2026-06-11\nNW: 1,000"
    )
    static_sys, ctx = assistant._split_turn_context(system, "Be concise.")
    # system_for_provider = static + marker + semi_stable (no TURN_SPLIT_MARKER)
    assert "STATIC" in static_sys
    assert "SEMI_STABLE profile" in static_sys
    assert CACHE_SPLIT_MARKER in static_sys
    assert TURN_SPLIT_MARKER not in static_sys
    # per-turn tail ends up in the context block
    assert "TODAY: 2026-06-11" in ctx
    assert "RESPONSE STYLE: Be concise." in ctx


def test_split_turn_context_with_cache_marker_only():
    # build_lean_system_prompt uses CACHE_SPLIT_MARKER but no TURN_SPLIT_MARKER.
    system = "STATIC AGENT INSTRUCTIONS" + CACHE_SPLIT_MARKER + "DATE: 2026-06-11"
    static_sys, ctx = assistant._split_turn_context(system, "Be concise.")
    # No TURN_SPLIT_MARKER → whole string is the system param, nothing is per-turn
    assert "STATIC AGENT INSTRUCTIONS" in static_sys
    assert "DATE: 2026-06-11" in static_sys
    assert CACHE_SPLIT_MARKER in static_sys
    assert "RESPONSE STYLE" in ctx


def test_split_turn_context_without_any_marker():
    static, ctx = assistant._split_turn_context("WHOLE PROMPT", "Short.")
    assert static == "WHOLE PROMPT"
    assert "RESPONSE STYLE" in ctx
    assert "WHOLE PROMPT" not in ctx


def test_turn_context_persisted_in_history_and_round_trip_byte_stable():
    # turn_context is now injected into the stored history message BEFORE _prepare_messages.
    # The outgoing bytes ARE the stored bytes — no ephemeral injection.
    history = [{"role": "user", "content": "hi"}]
    history[0] = assistant._inject_turn_context(history[0], "CTXBLOCK")
    assert history[0]["content"][-1] == {"type": "text", "text": "CTXBLOCK"}

    # Round-trip 1 — initial model call
    prepared_r1 = assistant._prepare_messages(history)
    assert prepared_r1[0]["content"][-1] == {"type": "text", "text": "CTXBLOCK"}

    # Round-trip 2 — after a tool_use + tool_result are appended mid-turn
    history += [
        {
            "role": "assistant",
            "content": [{"type": "tool_use", "id": "t1", "name": "get_net_worth", "input": {}}],
        },
        {
            "role": "user",
            "content": [{"type": "tool_result", "tool_use_id": "t1", "content": "{}"}],
        },
    ]
    prepared_r2 = assistant._prepare_messages(history)
    # The turn's user message (index 0) is byte-identical across both round-trips.
    assert prepared_r1[0] == prepared_r2[0]
    # The context block is still on the FIRST user message, not the last.
    assert prepared_r2[0]["content"][-1] == {"type": "text", "text": "CTXBLOCK"}
    assert all(
        b.get("text") != "CTXBLOCK" for b in prepared_r2[-1]["content"] if isinstance(b, dict)
    )


def test_turn_context_on_empty_user_message_emits_no_empty_text_block():
    history = [{"role": "user", "content": ""}]
    history[0] = assistant._inject_turn_context(history[0], "CTXBLOCK")
    prepared = assistant._prepare_messages(history)
    assert prepared[0]["content"] == [{"type": "text", "text": "CTXBLOCK"}]


# ── 3. window slides in chunks, not per-message ──────────────────────────────


def _chat(n: int) -> list[dict]:
    return [{"role": "user" if i % 2 == 0 else "assistant", "content": f"m{i}"} for i in range(n)]


def test_window_start_stable_until_chunk_boundary():
    # overflow 1..9 → window still starts at message 0 (byte-stable cached prefix)
    for n in range(assistant.MAX_HISTORY_MESSAGES + 1, assistant.MAX_HISTORY_MESSAGES + 10):
        prepared = assistant._prepare_messages(_chat(n))
        assert prepared[0]["content"] == "m0", f"window slid early at len={n}"


def test_window_advances_by_chunk_at_boundary():
    n = assistant.MAX_HISTORY_MESSAGES + assistant._WINDOW_SLIDE_CHUNK  # overflow == chunk
    prepared = assistant._prepare_messages(_chat(n))
    assert prepared[0]["content"] == f"m{assistant._WINDOW_SLIDE_CHUNK}"
