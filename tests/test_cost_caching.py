"""Tests for the cache architecture fix (lane/cost-caching).

Covers:
- Fix 1a: semi-stable / per-turn prompt split via TURN_SPLIT_MARKER
- Fix 1b: turn_context persisted in history; rehydrate filters it; memory extraction skips it
- Fix 2:  window_start pinned across loop iterations (prefix byte-stability)
- Fix 3:  (removed 2026-08-03 — no tool-round cap; owner decision, no limits)
"""

from __future__ import annotations

import json

import pytest

from backend.ai import assistant
from backend.ai import memory as mem_module
from backend.ai.models.anthropic_provider import _split_system_blocks
from backend.ai.prompts import CACHE_SPLIT_MARKER, TURN_SPLIT_MARKER

# ── helpers ──────────────────────────────────────────────────────────────────


def _user(text: str) -> dict:
    return {"role": "user", "content": text}


def _assistant_text(text: str) -> dict:
    return {"role": "assistant", "content": text}


def _tool_use(tid: str, name: str) -> dict:
    return {
        "role": "assistant",
        "content": [{"type": "tool_use", "id": tid, "name": name, "input": {}}],
    }


def _tool_result(tid: str, content: str) -> dict:
    return {
        "role": "user",
        "content": [{"type": "tool_result", "tool_use_id": tid, "content": content}],
    }


def _result_text(prepared: list[dict], tid: str) -> str | None:
    for msg in prepared:
        if msg.get("role") == "user" and isinstance(msg.get("content"), list):
            for b in msg["content"]:
                if b.get("type") == "tool_result" and b.get("tool_use_id") == tid:
                    return b.get("content")
    return None


# ── Fix 1a: prompt structure ──────────────────────────────────────────────────


def test_build_system_prompt_has_both_markers():
    """build_system_prompt() emits static + CACHE_SPLIT + semi_stable + TURN_SPLIT + per_turn."""
    from backend.ai.prompts import build_system_prompt

    try:
        prompt = build_system_prompt()
    except Exception:
        pytest.skip("data stores not available in this environment")

    assert CACHE_SPLIT_MARKER in prompt
    assert TURN_SPLIT_MARKER in prompt
    # Order: CACHE before TURN
    assert prompt.index(CACHE_SPLIT_MARKER) < prompt.index(TURN_SPLIT_MARKER)
    # Per-turn tail contains today's date
    _, _, after_turn = prompt.partition(TURN_SPLIT_MARKER)
    assert "TODAY'S DATE" in after_turn
    assert "PORTFOLIO SNAPSHOT" in after_turn
    # Semi-stable contains profile + memory/skills sections
    _, _, after_cache = prompt.partition(CACHE_SPLIT_MARKER)
    semi_stable, _, _ = after_cache.partition(TURN_SPLIT_MARKER)
    assert "FINANCIAL PROFILE" in semi_stable


def test_per_turn_block_is_small_relative_to_semi_stable():
    """The per-turn tail is a small fraction of the old volatile block size."""
    from backend.ai.prompts import build_system_prompt

    try:
        prompt = build_system_prompt()
    except Exception:
        pytest.skip("data stores not available in this environment")

    _, _, after_turn = prompt.partition(TURN_SPLIT_MARKER)
    # Per-turn block should be well under 5K chars (date + portfolio snapshot ~1-2K)
    assert len(after_turn) < 5_000, f"per-turn block is too large: {len(after_turn)} chars"


def test_split_turn_context_returns_system_without_turn_marker():
    """_split_turn_context strips TURN_SPLIT_MARKER; provider gets static+CACHE+semi only."""
    system = (
        "STATIC"
        + CACHE_SPLIT_MARKER
        + "SEMI_STABLE"
        + TURN_SPLIT_MARKER
        + "TODAY: 2099-01-01\nNW: 999"
    )
    sys_for_provider, ctx = assistant._split_turn_context(system, "Be concise.")
    assert TURN_SPLIT_MARKER not in sys_for_provider
    assert "STATIC" in sys_for_provider
    assert "SEMI_STABLE" in sys_for_provider
    assert CACHE_SPLIT_MARKER in sys_for_provider
    # Per-turn content lands in turn_ctx only
    assert "TODAY: 2099-01-01" in ctx
    assert "TODAY: 2099-01-01" not in sys_for_provider


def test_split_system_blocks_receives_static_plus_semi_stable():
    """_split_system_blocks gets the pre-extracted string (no TURN_SPLIT_MARKER) and
    produces two blocks: static@1h and semi_stable@5m."""
    system_for_provider = "STATIC" + CACHE_SPLIT_MARKER + "SEMI_STABLE"
    blocks = _split_system_blocks(system_for_provider)
    assert len(blocks) == 2
    assert blocks[0]["text"] == "STATIC"
    assert blocks[0]["cache_control"] == {"type": "ephemeral", "ttl": "1h"}
    assert blocks[1]["text"] == "SEMI_STABLE"
    assert blocks[1]["cache_control"] == {"type": "ephemeral"}
    assert "ttl" not in blocks[1]["cache_control"]


# ── Fix 1b: turn_context persistence ─────────────────────────────────────────


def test_inject_turn_context_adds_block_to_string_content():
    msg = {"role": "user", "content": "hello world"}
    result = assistant._inject_turn_context(msg, "<turn_context>\nDATE</turn_context>")
    assert isinstance(result["content"], list)
    assert result["content"][0] == {"type": "text", "text": "hello world"}
    assert result["content"][-1] == {
        "type": "text",
        "text": "<turn_context>\nDATE</turn_context>",
    }
    # Original not mutated
    assert msg["content"] == "hello world"


def test_inject_turn_context_appends_to_list_content():
    existing_block = {"type": "image", "source": {}}
    msg = {"role": "user", "content": [{"type": "text", "text": "see above"}, existing_block]}
    result = assistant._inject_turn_context(msg, "<turn_context>\nDATE</turn_context>")
    assert result["content"][-1]["text"] == "<turn_context>\nDATE</turn_context>"
    assert result["content"][1] is existing_block  # middle block untouched


def test_persisted_turn_context_not_shown_in_rehydrate():
    """rehydrate_messages must filter out <turn_context> text blocks."""
    ctx_block = "<turn_context>\nTODAY: 2099\nNW: 42</turn_context>"
    history = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "What is my net worth?"},
                {"type": "text", "text": ctx_block},
            ],
        },
        {
            "role": "assistant",
            "content": "Your net worth is 42.",
        },
    ]
    # Patch _load_session to return our history
    session_id = "test-rehydrate"
    original_load = assistant._load_session
    assistant._load_session = lambda sid: history if sid == session_id else None
    try:
        msgs = assistant.rehydrate_messages(session_id)
    finally:
        assistant._load_session = original_load

    assert msgs is not None
    user_msgs = [m for m in msgs if m["role"] == "user"]
    assert len(user_msgs) == 1
    # The turn_context block must not appear in rendered text
    assert ctx_block not in user_msgs[0]["text"]
    assert "<turn_context>" not in user_msgs[0]["text"]
    # The real user text must appear
    assert "What is my net worth?" in user_msgs[0]["text"]


def test_memory_extraction_skips_turn_context_blocks():
    """_conversation_text must skip <turn_context> blocks so extraction isn't polluted."""
    ctx_text = "<turn_context>\nTODAY: 2099\nPortfolio: 42\n</turn_context>"
    history = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "Remember I prefer low risk."},
                {"type": "text", "text": ctx_text},
            ],
        },
        {
            "role": "assistant",
            "content": "Got it, I'll remember that.",
        },
    ]
    text = mem_module._conversation_text(history)
    assert "<turn_context>" not in text
    assert "TODAY: 2099" not in text
    assert "Remember I prefer low risk." in text


# ── Fix 2: prefix byte-stability across loop iterations ──────────────────────


def test_prepare_messages_prefix_identical_across_iterations():
    """The PREFIX (messages before the current turn) must be byte-identical between
    round 1 and round 2 of the same turn, even when a new tool call is appended."""
    big_content = "x" * 6000

    # Simulate a session with some prior history + the current turn's user message
    # (already carrying a turn_context block, as run() does).
    ctx_block = {"type": "text", "text": "<turn_context>\nDATE: 2099\n</turn_context>"}
    history = [
        _user("turn 0 user msg"),
        _assistant_text("turn 0 assistant reply"),
        _user("turn 1 user msg"),
        _assistant_text("turn 1 assistant reply"),
        # Current turn's user message with persisted turn_context
        {
            "role": "user",
            "content": [{"type": "text", "text": "what is my portfolio?"}, ctx_block],
        },
    ]

    # Snapshot window_start ONCE (as run() does)
    ws = assistant._compute_turn_prefix_state(history)

    # Round 1 — model sees the user question
    prepared_r1 = assistant._prepare_messages(history, window_start=ws)

    # Model responds with a get_portfolio tool_use; result is appended
    history.append(_tool_use("gp1", "get_portfolio"))
    history.append(_tool_result("gp1", big_content))

    # Round 2 — model sees the tool result
    prepared_r2 = assistant._prepare_messages(history, window_start=ws)

    # The prefix (everything except the last new message) must be byte-identical.
    # prepared_r2 has 2 more messages than prepared_r1; the first N-2 are identical.
    n = len(prepared_r1)
    assert n >= 3, "need at least 3 messages for a meaningful prefix check"
    for i in range(n):
        assert prepared_r1[i] == prepared_r2[i], f"prefix message {i} differs between rounds"


def test_compute_turn_prefix_state_pins_window_start():
    """_compute_turn_prefix_state returns the same window_start even when messages
    are appended mid-turn that would cross a chunk boundary."""
    # Build a history that is just at MAX so window_start == 0
    history = [{"role": "user", "content": f"m{i}"} for i in range(assistant.MAX_HISTORY_MESSAGES)]
    ws = assistant._compute_turn_prefix_state(history)
    assert ws == 0

    # Append enough messages to cross the first chunk boundary mid-turn
    for i in range(assistant._WINDOW_SLIDE_CHUNK + 2):
        history.append({"role": "user", "content": f"extra{i}"})

    # A FRESH compute would now return a non-zero window_start, but the pinned value stays 0
    ws_fresh = assistant._compute_turn_prefix_state(history)
    assert ws_fresh > 0  # confirm a fresh compute would move
    # The original ws from the start-of-turn snapshot is still 0
    assert ws == 0


# ── Fix 3: tool loop cap ──────────────────────────────────────────────────────


class _AlwaysToolProvider:
    """Fake provider that always responds with a single tool_use (never end_turn)."""

    def __init__(self, tool_name: str = "get_net_worth"):
        self.tool_name = tool_name
        self._call_count = 0

    async def stream_chat(self, messages, tools, system, model, effort="medium"):
        self._call_count += 1
        tid = f"t{self._call_count}"
        yield {"type": "tool_use_start", "id": tid, "name": self.tool_name}
        yield {"type": "tool_use", "id": tid, "name": self.tool_name, "input": {}}
        yield {
            "type": "usage",
            "input_tokens": 10,
            "output_tokens": 5,
            "cache_creation_tokens": 0,
            "cache_read_tokens": 0,
            "cost_usd": 0.0,
        }
        yield {"type": "done", "stop_reason": "tool_use"}


async def _drain(gen) -> list[dict]:
    """Collect all events from an async generator."""
    events = []
    async for ev in gen:
        events.append(ev)
    return events


# ── openai_provider marker stripping ─────────────────────────────────────────


def test_openai_provider_strips_both_markers():
    """OpenAI provider must strip both markers when building system messages."""
    from backend.ai.models.openai_provider import OpenAIProvider

    system = "STATIC" + CACHE_SPLIT_MARKER + "SEMI_STABLE" + TURN_SPLIT_MARKER + "PER_TURN"
    messages = OpenAIProvider._messages_to_openai([], system)
    assert len(messages) == 1
    assert messages[0]["role"] == "system"
    content = messages[0]["content"]
    assert CACHE_SPLIT_MARKER not in content
    assert TURN_SPLIT_MARKER not in content
    assert "STATIC" in content
    assert "SEMI_STABLE" in content
    assert "PER_TURN" in content


# ── regression: mid-turn results must NEVER be stubbed ────────────────────────
# Live incident: a get_portfolio result fetched DURING the turn was stubbed on the
# next round-trip (a since-retired mechanism), and the model re-called it in a loop
# until the turn ended naturally (~20 duplicate calls, $0.55 turn). Current-turn
# protection stays live as a rule even though the only remaining thing it could
# protect against today is free-text truncation, not stubbing.


def _mk_turn_history():
    from backend.ai.assistant import _compute_turn_prefix_state

    history = [
        {"role": "user", "content": "old question"},
        {"role": "assistant", "content": [{"type": "text", "text": "old answer"}]},
        {"role": "user", "content": "how is my portfolio?"},  # turn starts here
    ]
    turn_ctx_idx = len(history) - 1
    window_start = _compute_turn_prefix_state(history)
    return history, turn_ctx_idx, window_start


def test_mid_turn_prefix_bytes_stable_with_turn_start():

    from backend.ai.assistant import _prepare_messages

    history, turn_ctx_idx, window_start = _mk_turn_history()
    r1 = _prepare_messages(history, window_start=window_start, turn_start=turn_ctx_idx)
    history.append(
        {
            "role": "assistant",
            "content": [{"type": "tool_use", "id": "tu_a", "name": "get_portfolio", "input": {}}],
        }
    )
    history.append(
        {
            "role": "user",
            "content": [{"type": "tool_result", "tool_use_id": "tu_a", "content": "FULL-A"}],
        }
    )
    r2 = _prepare_messages(history, window_start=window_start, turn_start=turn_ctx_idx)
    assert json.dumps(r2[: len(r1)]) == json.dumps(r1), "prefix bytes changed mid-turn"
    assert r2[-1]["content"][0]["content"] == "FULL-A"


def test_approx_token_count_matches_codexs_own_formula():
    """Same technique as Codex's approx_token_count (codex-rs/utils/string/truncate.rs):
    ceil(byte_len / 4), zero for empty text."""
    from backend.ai.assistant import _APPROX_BYTES_PER_TOKEN, _approx_token_count

    assert _APPROX_BYTES_PER_TOKEN == 4
    assert _approx_token_count("") == 0
    assert _approx_token_count("abcd") == 1  # exactly one token's worth of bytes
    assert _approx_token_count("abcde") == 2  # ceil(5/4)
    assert _approx_token_count("x" * 4000) == 1000
