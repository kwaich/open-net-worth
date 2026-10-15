"""Regression tests for the prompt-cache TTL placement (chat-cost Lever A).

The architecture: _split_turn_context() produces system_for_provider = static +
CACHE_SPLIT_MARKER + semi_stable, and strips the per-turn tail (date/portfolio/FX)
into a turn_context block persisted in the user message. _split_system_blocks() then
receives that string and splits on CACHE_SPLIT_MARKER giving:
  static@stable_ttl — byte-identical for the life of an instance (1h for interactive,
                      5m for scheduled runs)
  semi_stable@5m    — changes only on memory extraction / profile edits (~every few turns)
No beta header required (1h TTL is GA).

Surface-aware TTL (Lever A2):
  Scheduled/agent runs fire every 1-4+ hours. A 1h-TTL write expires before the next
  run, so it is never read — pure 2x write cost. The 5-min default (1.25x write) still
  covers within-run reads (a multi-round tool loop finishes in minutes). Interactive
  surfaces (web, connector:*) keep 1h so cold/gapped turns read instead of re-write.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from backend.ai.models import anthropic_provider as ap
from backend.ai.prompts import CACHE_SPLIT_MARKER
from backend.core import cost_context

# ---------------------------------------------------------------------------
# Existing tests — unchanged; they exercise the default (no surface set → 1h)
# ---------------------------------------------------------------------------


def test_static_system_gets_1h_ttl_semi_stable_gets_5m():
    # _split_system_blocks receives static + CACHE_SPLIT_MARKER + semi_stable
    # (the per-turn tail has already been extracted by _split_turn_context).
    system = "STATIC INSTRUCTION BODY" + CACHE_SPLIT_MARKER + "SEMI STABLE PROFILE"
    blocks = ap._split_system_blocks(system)

    assert len(blocks) == 2
    static, semi_stable = blocks
    assert static["text"] == "STATIC INSTRUCTION BODY"
    assert static["cache_control"] == {"type": "ephemeral", "ttl": "1h"}
    # Semi-stable tail changes only on profile/memory edits → 5-min TTL (no "ttl" key).
    assert semi_stable["text"] == "SEMI STABLE PROFILE"
    assert semi_stable["cache_control"] == {"type": "ephemeral"}
    assert "ttl" not in semi_stable["cache_control"]


def test_system_without_marker_uses_long_ttl():
    blocks = ap._split_system_blocks("a single uncut system prompt")
    assert len(blocks) == 1
    assert blocks[0]["cache_control"] == {"type": "ephemeral", "ttl": "1h"}


def test_stable_cache_constants_distinct():
    # The stable prefix must be the long-lived entry; the volatile tail the short one.
    assert ap._STABLE_CACHE == {"type": "ephemeral", "ttl": "1h"}
    assert ap._VOLATILE_CACHE == {"type": "ephemeral"}
    assert "ttl" not in ap._VOLATILE_CACHE


# ---------------------------------------------------------------------------
# _stable_cache_for_surface unit tests
# ---------------------------------------------------------------------------


def test_stable_cache_for_surface_scheduled_returns_5m():
    result = ap._stable_cache_for_surface("scheduled")
    assert result == {"type": "ephemeral"}
    assert "ttl" not in result


def test_stable_cache_for_surface_scheduled_prefix_returns_5m():
    # Future sub-surface like "scheduled:agent" also picks the 5m path.
    result = ap._stable_cache_for_surface("scheduled:some-agent")
    assert result == {"type": "ephemeral"}
    assert "ttl" not in result


def test_stable_cache_for_surface_web_returns_1h():
    result = ap._stable_cache_for_surface("web")
    assert result == {"type": "ephemeral", "ttl": "1h"}


def test_stable_cache_for_surface_connector_returns_1h():
    result = ap._stable_cache_for_surface("connector:telegram")
    assert result == {"type": "ephemeral", "ttl": "1h"}


def test_stable_cache_for_surface_connector_bare_returns_1h():
    result = ap._stable_cache_for_surface("connector")
    assert result == {"type": "ephemeral", "ttl": "1h"}


def test_stable_cache_for_surface_none_returns_1h():
    result = ap._stable_cache_for_surface(None)
    assert result == {"type": "ephemeral", "ttl": "1h"}


def test_stable_cache_for_surface_unknown_returns_1h():
    # Unknown/future surfaces default to the safe (long) TTL.
    result = ap._stable_cache_for_surface("some-future-surface")
    assert result == {"type": "ephemeral", "ttl": "1h"}


# ---------------------------------------------------------------------------
# _split_system_blocks with explicit stable_cache param
# ---------------------------------------------------------------------------


def test_split_system_blocks_scheduled_stable_cache_5m():
    stable_5m = ap._stable_cache_for_surface("scheduled")
    system = "STATIC" + CACHE_SPLIT_MARKER + "SEMI"
    blocks = ap._split_system_blocks(system, stable_cache=stable_5m)
    assert len(blocks) == 2
    assert blocks[0]["cache_control"] == {"type": "ephemeral"}
    assert "ttl" not in blocks[0]["cache_control"]
    # Semi-stable always stays 5m regardless of surface.
    assert blocks[1]["cache_control"] == {"type": "ephemeral"}


def test_split_system_blocks_web_stable_cache_1h():
    stable_1h = ap._stable_cache_for_surface("web")
    system = "STATIC" + CACHE_SPLIT_MARKER + "SEMI"
    blocks = ap._split_system_blocks(system, stable_cache=stable_1h)
    assert len(blocks) == 2
    assert blocks[0]["cache_control"] == {"type": "ephemeral", "ttl": "1h"}
    assert blocks[1]["cache_control"] == {"type": "ephemeral"}


def test_split_system_blocks_no_marker_scheduled_5m():
    stable_5m = ap._stable_cache_for_surface("scheduled")
    blocks = ap._split_system_blocks("whole system no marker", stable_cache=stable_5m)
    assert len(blocks) == 1
    assert blocks[0]["cache_control"] == {"type": "ephemeral"}
    assert "ttl" not in blocks[0]["cache_control"]


# ---------------------------------------------------------------------------
# stream_chat integration: tools block + static system block TTL per surface
# ---------------------------------------------------------------------------


def _make_fake_event_stream():
    """Build a minimal fake async stream that satisfies stream_chat's consumption loop."""

    class FakeUsage:
        input_tokens = 10
        output_tokens = 5
        cache_creation_input_tokens = 0
        cache_read_input_tokens = 0
        cache_creation = None

    class FakeFinalMsg:
        usage = FakeUsage()
        content = []
        stop_reason = "end_turn"

    class FakeStream:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            pass

        def __aiter__(self):
            return self

        async def __anext__(self):
            raise StopAsyncIteration

        async def get_final_message(self):
            return FakeFinalMsg()

    return FakeStream()


async def _run_stream_chat_with_surface(surface: str | None) -> dict:
    """Set the cost surface, run stream_chat with a fake client, return captured kwargs."""
    captured: dict = {}

    def fake_stream(**kwargs):
        captured.update(kwargs)
        return _make_fake_event_stream()

    fake_client = MagicMock()
    fake_client.messages.stream = fake_stream

    provider = ap.AnthropicProvider.__new__(ap.AnthropicProvider)
    provider.client = fake_client

    tools = [{"name": "tool_a", "description": "a"}, {"name": "tool_b", "description": "b"}]
    system = "STATIC BODY" + CACHE_SPLIT_MARKER + "SEMI STABLE"

    if surface is not None:
        cost_context.set_cost_surface(surface)
    try:
        async for _ in provider.stream_chat(
            messages=[{"role": "user", "content": "hello"}],
            tools=tools,
            system=system,
            model="claude-haiku-4-5",
            effort="medium",
        ):
            pass
    finally:
        cost_context.clear_cost_surface()

    return captured


@pytest.fixture(autouse=True)
def _reset_cost_surface():
    """Ensure ContextVar is clean before and after each test."""
    cost_context.clear_cost_surface()
    yield
    cost_context.clear_cost_surface()


@pytest.mark.asyncio
async def test_stream_chat_web_surface_tools_and_static_get_1h():
    kwargs = await _run_stream_chat_with_surface("web")

    # Tools block: last tool must carry 1h TTL.
    last_tool = kwargs["tools"][-1]
    assert last_tool["cache_control"] == {"type": "ephemeral", "ttl": "1h"}

    # System blocks: static (first) must carry 1h TTL.
    system_blocks = kwargs["system"]
    assert system_blocks[0]["cache_control"] == {"type": "ephemeral", "ttl": "1h"}
    # Semi-stable (second) stays at 5m.
    assert system_blocks[1]["cache_control"] == {"type": "ephemeral"}
    assert "ttl" not in system_blocks[1]["cache_control"]


@pytest.mark.asyncio
async def test_stream_chat_scheduled_surface_tools_and_static_get_5m():
    kwargs = await _run_stream_chat_with_surface("scheduled")

    # Tools block: last tool must NOT carry ttl key (5m default).
    last_tool = kwargs["tools"][-1]
    assert last_tool["cache_control"] == {"type": "ephemeral"}
    assert "ttl" not in last_tool["cache_control"]

    # System blocks: static (first) must NOT carry ttl key.
    system_blocks = kwargs["system"]
    assert system_blocks[0]["cache_control"] == {"type": "ephemeral"}
    assert "ttl" not in system_blocks[0]["cache_control"]
    # Semi-stable (second) also stays at 5m.
    assert system_blocks[1]["cache_control"] == {"type": "ephemeral"}


@pytest.mark.asyncio
async def test_stream_chat_none_surface_defaults_to_1h():
    """Unset surface (None) must behave identically to 'web' — 1h TTL everywhere."""
    kwargs = await _run_stream_chat_with_surface(None)

    last_tool = kwargs["tools"][-1]
    assert last_tool["cache_control"] == {"type": "ephemeral", "ttl": "1h"}

    system_blocks = kwargs["system"]
    assert system_blocks[0]["cache_control"] == {"type": "ephemeral", "ttl": "1h"}


@pytest.mark.asyncio
async def test_stream_chat_connector_surface_gets_1h():
    kwargs = await _run_stream_chat_with_surface("connector:telegram")

    last_tool = kwargs["tools"][-1]
    assert last_tool["cache_control"] == {"type": "ephemeral", "ttl": "1h"}

    system_blocks = kwargs["system"]
    assert system_blocks[0]["cache_control"] == {"type": "ephemeral", "ttl": "1h"}
