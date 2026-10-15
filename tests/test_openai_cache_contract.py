"""The OpenAI-compatible path's prompt-caching contract, pinned.

Two things are asserted here, both of which are load-bearing for cost:

1. WHICH endpoints get an explicit cache marker. Surveyed against provider docs
   2026-07-19: almost none of them, because almost none of them offer one. xAI in
   particular has NO breakpoint field at all — its cache is implicit and always on
   — so the Anthropic provider's four-breakpoint scheme has nothing to port to it.
   A future change that starts spraying ``cache_control`` at every endpoint (a
   strict endpoint 400s on unknown fields) fails these tests.

2. VOLATILE IS NEVER EMITTED BEFORE STABLE. An implicit cache keys on an exact
   prefix match, so a per-turn injection (today's date, a live portfolio snapshot)
   placed ahead of the stable instruction body would destroy the hit on every
   single turn. This is the invariant that actually earns the cache on this path,
   and nothing in the provider signals when it breaks — hence a test.
"""

from __future__ import annotations

import pytest

from backend.ai.models.openai_provider import OpenAIProvider
from backend.ai.prompts import CACHE_SPLIT_MARKER, TURN_SPLIT_MARKER

_XAI = "https://api.x.ai/v1"
_OPENROUTER = "https://openrouter.ai/api/v1"
_OPENAI = "https://api.openai.com/v1"
_LOCAL = "http://localhost:11434/v1"


class TestExplicitCacheMarkers:
    def test_openrouter_anthropic_gets_the_marker(self):
        """The one case that genuinely needs it — Anthropic caching is opt-in."""
        p = OpenAIProvider(base_url=_OPENROUTER)
        assert p._cache_body_fields("anthropic/claude-sonnet-5") == {
            "cache_control": {"type": "ephemeral"}
        }

    @pytest.mark.parametrize(
        "base_url,model",
        [
            # xAI documents NO breakpoint field. Sending one would be a guess.
            (_XAI, "grok-4.5"),
            (_OPENROUTER, "x-ai/grok-4.5"),
            # OpenAI ≤5.5, Moonshot, DeepSeek, GLM: all cache implicitly.
            (_OPENAI, "gpt-4o"),
            (_OPENROUTER, "moonshotai/kimi-k3"),
            (_OPENROUTER, "deepseek/deepseek-v4-pro"),
            (_OPENROUTER, "z-ai/glm-5.2"),
            # A local model has no upstream cache to engage at all.
            (_LOCAL, "llama3.1"),
        ],
    )
    def test_implicit_cache_endpoints_get_no_marker(self, base_url, model):
        assert OpenAIProvider(base_url=base_url)._cache_body_fields(model) == {}

    def test_marker_is_openrouter_only_even_for_a_claude_id(self):
        """``cache_control`` is an OpenRouter normalization field. Anthropic's own
        API is reached by the Anthropic provider, never this one."""
        assert (
            OpenAIProvider(base_url=_OPENAI)._cache_body_fields("anthropic/claude-sonnet-5") == {}
        )


class TestCacheRoutingKey:
    """``prompt_cache_key`` — documented by xAI and OpenAI, nobody else."""

    @pytest.mark.parametrize("base_url", [_XAI, _OPENAI])
    def test_sent_where_documented(self, base_url):
        fields = OpenAIProvider(base_url=base_url)._cache_routing_fields("sys", [{"name": "a"}])
        assert list(fields) == ["prompt_cache_key"]
        assert fields["prompt_cache_key"].startswith("onw-")

    @pytest.mark.parametrize("base_url", [_OPENROUTER, _LOCAL])
    def test_not_sent_where_undocumented(self, base_url):
        """An unrecognized body field is a 400 risk — never send one on spec."""
        assert OpenAIProvider(base_url=base_url)._cache_routing_fields("sys", []) == {}

    def test_key_is_stable_across_turns_with_the_same_prefix(self):
        p = OpenAIProvider(base_url=_XAI)
        tools = [{"name": "get_net_worth"}, {"name": "add_transaction"}]
        assert p._cache_routing_fields("SYSTEM", tools) == p._cache_routing_fields("SYSTEM", tools)

    def test_key_changes_when_the_cacheable_prefix_changes(self):
        p = OpenAIProvider(base_url=_XAI)
        base = p._cache_routing_fields("SYSTEM", [{"name": "a"}])
        assert p._cache_routing_fields("SYSTEM CHANGED", [{"name": "a"}]) != base
        assert p._cache_routing_fields("SYSTEM", [{"name": "a"}, {"name": "b"}]) != base

    def test_key_ignores_tool_schema_churn_that_does_not_change_the_offer(self):
        """Keyed on tool NAMES: a description edit should not repartition routing."""
        p = OpenAIProvider(base_url=_XAI)
        a = p._cache_routing_fields("S", [{"name": "x", "description": "one"}])
        b = p._cache_routing_fields("S", [{"name": "x", "description": "two"}])
        assert a == b

    def test_key_is_exactly_a_digest_and_cannot_carry_prompt_content(self):
        """Pins the CONSTRUCTION, not a substring probe.

        Asserting "the secret isn't in the output" is nearly free to satisfy —
        a hex digest can hardly contain a word. The real guarantee is that the
        key is a fixed-width digest of the prefix and nothing else, so no input
        can ever be echoed through it.
        """
        import hashlib

        system = "the user's net worth is 12345"
        tools = [{"name": "get_net_worth", "description": "d", "input_schema": {}}]
        expected = hashlib.sha256()
        expected.update(system.encode("utf-8", "replace"))
        expected.update(b"\x00")
        expected.update(b"get_net_worth")

        key = OpenAIProvider(base_url=_XAI)._cache_routing_fields(system, tools)["prompt_cache_key"]
        assert key == f"onw-{expected.hexdigest()[:32]}"

    @pytest.mark.parametrize(
        "system",
        ["", "x", "a very long system prompt " * 500, "unicode ✦ émoji 🎯 and \x00 control"],
    )
    def test_key_is_fixed_width_whatever_the_input(self, system):
        """Fixed width for any input length is what makes passthrough impossible."""
        key = OpenAIProvider(base_url=_XAI)._cache_routing_fields(system, [])["prompt_cache_key"]
        assert len(key) == len("onw-") + 32
        assert all(c in "0123456789abcdef" for c in key[4:])


class TestVolatileNeverPrecedesStable:
    """The invariant that actually earns the implicit cache hit."""

    def test_system_message_is_first_and_carries_the_stable_body(self):
        msgs = OpenAIProvider._messages_to_openai(
            [{"role": "user", "content": "hi"}], "STATIC" + CACHE_SPLIT_MARKER + "SEMI"
        )
        assert msgs[0]["role"] == "system"
        assert msgs[0]["content"].index("STATIC") < msgs[0]["content"].index("SEMI")

    def test_cache_split_markers_are_stripped_not_leaked(self):
        """The markers are an Anthropic breakpoint convention; this path has no
        breakpoints, so they must never reach the model as literal noise."""
        msgs = OpenAIProvider._messages_to_openai(
            [], "STATIC" + CACHE_SPLIT_MARKER + "SEMI" + TURN_SPLIT_MARKER + "TURN"
        )
        assert CACHE_SPLIT_MARKER.strip() not in msgs[0]["content"]
        assert TURN_SPLIT_MARKER.strip() not in msgs[0]["content"]

    def test_per_turn_volatile_content_lands_after_the_stable_prefix(self):
        """A live snapshot rides the trailing user message, so the whole system
        prefix in front of it stays byte-identical between turns."""
        msgs = OpenAIProvider._messages_to_openai(
            [{"role": "user", "content": "TODAY'S DATE: 2026-07-19\nask"}],
            "STATIC" + CACHE_SPLIT_MARKER + "SEMI",
        )
        roles = [m["role"] for m in msgs]
        assert roles == ["system", "user"]
        assert "TODAY'S DATE" not in msgs[0]["content"]

    def test_prefix_is_byte_identical_when_only_the_last_turn_differs(self):
        """The direct statement of the caching contract: two turns of the same
        conversation must share an exact common prefix."""
        system = "STATIC" + CACHE_SPLIT_MARKER + "SEMI"
        first = OpenAIProvider._messages_to_openai([{"role": "user", "content": "q1"}], system)
        second = OpenAIProvider._messages_to_openai(
            [
                {"role": "user", "content": "q1"},
                {"role": "assistant", "content": [{"type": "text", "text": "a1"}]},
                {"role": "user", "content": "q2"},
            ],
            system,
        )
        assert second[: len(first)] == first

    def test_tool_loop_round_trips_only_append(self):
        """Each tool round-trip re-sends the history; it must extend, never
        reorder, or every round-trip in the turn pays full price."""
        system = "STATIC"
        before = OpenAIProvider._messages_to_openai([{"role": "user", "content": "q"}], system)
        after = OpenAIProvider._messages_to_openai(
            [
                {"role": "user", "content": "q"},
                {
                    "role": "assistant",
                    "content": [{"type": "tool_use", "id": "t1", "name": "get_x", "input": {}}],
                },
                {
                    "role": "user",
                    "content": [{"type": "tool_result", "tool_use_id": "t1", "content": "ok"}],
                },
            ],
            system,
        )
        assert after[: len(before)] == before
