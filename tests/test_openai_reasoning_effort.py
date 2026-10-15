"""Effort → reasoning-param mapping for the OpenAI-compatible provider.

The chat UI's effort control (low/medium/high/max) must reach endpoints that
support a reasoning parameter — capability-gated by base_url/model (the batch
seam's routing rule), so a strict or local endpoint never sees an unknown field:

  - OpenRouter        → extra_body {"reasoning": {"effort": ...}} (any model —
                        OpenRouter ignores it for non-reasoning models)
  - api.openai.com    → {"reasoning_effort": ...} for reasoning families only
  - api.x.ai          → {"reasoning_effort": "low"|"high"} for grok-3-mini only
  - anything else     → nothing (Ollama/LM Studio/vLLM/unknown gateways)

Hermetic: no network — stream_chat is exercised against a fake client.
"""

from __future__ import annotations

import pytest

from backend.ai.models.openai_provider import OpenAIProvider


def _provider(base_url: str) -> OpenAIProvider:
    return OpenAIProvider(base_url=base_url, api_key="sk-test")


class TestReasoningBodyFields:
    def test_openrouter_full_ladder_passes_through(self):
        # OpenRouter normalizes to the nearest level each model supports, so the
        # full app ladder — xhigh and max included — passes through unchanged.
        p = _provider("https://openrouter.ai/api/v1")
        for effort in ("low", "medium", "high", "xhigh", "max"):
            assert p._reasoning_body_fields("meta/muse-spark-1.1", effort) == {
                "reasoning": {"effort": effort}
            }

    def test_openai_reasoning_families_get_reasoning_effort(self):
        p = _provider("https://api.openai.com/v1")
        assert p._reasoning_body_fields("o3-mini", "low") == {"reasoning_effort": "low"}
        assert p._reasoning_body_fields("gpt-5", "max") == {"reasoning_effort": "high"}
        assert p._reasoning_body_fields("gpt-5", "xhigh") == {"reasoning_effort": "high"}

    def test_openai_non_reasoning_model_gets_nothing(self):
        p = _provider("https://api.openai.com/v1")
        assert p._reasoning_body_fields("gpt-4o", "high") == {}
        assert p._reasoning_body_fields("gpt-4.1-mini", "max") == {}

    def test_xai_grok3_mini_two_level_mapping(self):
        p = _provider("https://api.x.ai/v1")
        assert p._reasoning_body_fields("grok-3-mini", "low") == {"reasoning_effort": "low"}
        assert p._reasoning_body_fields("grok-3-mini", "medium") == {"reasoning_effort": "low"}
        assert p._reasoning_body_fields("grok-3-mini", "high") == {"reasoning_effort": "high"}
        assert p._reasoning_body_fields("grok-3-mini", "xhigh") == {"reasoning_effort": "high"}
        assert p._reasoning_body_fields("grok-3-mini", "max") == {"reasoning_effort": "high"}

    def test_xai_grok45_three_level_mapping(self):
        # grok-4.5 supports low/medium/high (verified against docs.x.ai).
        p = _provider("https://api.x.ai/v1")
        assert p._reasoning_body_fields("grok-4.5", "low") == {"reasoning_effort": "low"}
        assert p._reasoning_body_fields("grok-4.5", "medium") == {"reasoning_effort": "medium"}
        assert p._reasoning_body_fields("grok-4.5", "high") == {"reasoning_effort": "high"}
        assert p._reasoning_body_fields("grok-4.5", "xhigh") == {"reasoning_effort": "high"}
        assert p._reasoning_body_fields("grok-4.5", "max") == {"reasoning_effort": "high"}

    def test_xai_grok4_gets_nothing(self):
        # Plain grok-4 isn't documented for reasoning_effort — never send it.
        assert _provider("https://api.x.ai/v1")._reasoning_body_fields("grok-4", "high") == {}

    def test_local_and_unknown_endpoints_get_nothing(self):
        for url in ("http://localhost:11434/v1", "https://my-gateway.example.com/v1"):
            assert _provider(url)._reasoning_body_fields("o3-mini", "high") == {}

    def test_unknown_effort_treated_as_medium(self):
        p = _provider("https://openrouter.ai/api/v1")
        assert p._reasoning_body_fields("x", "bogus") == {"reasoning": {"effort": "medium"}}


class TestCacheBodyFields:
    def test_openrouter_anthropic_model_gets_cache_control(self):
        p = _provider("https://openrouter.ai/api/v1")
        assert p._cache_body_fields("anthropic/claude-sonnet-5") == {
            "cache_control": {"type": "ephemeral"}
        }

    def test_openrouter_non_anthropic_model_gets_nothing(self):
        # Moonshot/OpenAI/DeepSeek cache automatically — no opt-in field needed.
        p = _provider("https://openrouter.ai/api/v1")
        assert p._cache_body_fields("moonshotai/kimi-k3") == {}

    def test_non_openrouter_endpoint_gets_nothing(self):
        # Top-level cache_control is an OpenRouter-normalization field, not a
        # standard OpenAI param — a strict endpoint must never see it.
        assert _provider("https://api.openai.com/v1")._cache_body_fields("gpt-5") == {}


class _FakeStream:
    """Async-iterable empty stream (no chunks) that stream_chat can consume."""

    def __aiter__(self):
        return self

    async def __anext__(self):
        raise StopAsyncIteration


class _FakeCompletions:
    def __init__(self):
        self.kwargs = None

    async def create(self, **kwargs):
        self.kwargs = kwargs
        return _FakeStream()


class _FakeClient:
    def __init__(self):
        self.chat = type("C", (), {})()
        self.chat.completions = _FakeCompletions()


@pytest.mark.asyncio
async def test_stream_chat_merges_reasoning_into_extra_body():
    """OpenRouter: usage accounting AND reasoning ride ONE merged extra_body."""
    p = _provider("https://openrouter.ai/api/v1")
    fake = _FakeClient()
    p._client = fake
    async for _ in p.stream_chat([], [], system="", model="meta/muse-spark-1.1", effort="high"):
        pass
    body = fake.chat.completions.kwargs["extra_body"]
    assert body["usage"] == {"include": True}
    assert body["reasoning"] == {"effort": "high"}


@pytest.mark.asyncio
async def test_stream_chat_openrouter_claude_rides_cache_control():
    """Claude via OpenRouter opts into their automatic prompt-cache mode."""
    p = _provider("https://openrouter.ai/api/v1")
    fake = _FakeClient()
    p._client = fake
    async for _ in p.stream_chat(
        [], [], system="", model="anthropic/claude-sonnet-5", effort="medium"
    ):
        pass
    assert fake.chat.completions.kwargs["extra_body"]["cache_control"] == {"type": "ephemeral"}


@pytest.mark.asyncio
async def test_stream_chat_local_endpoint_sends_no_reasoning():
    p = _provider("http://localhost:11434/v1")
    fake = _FakeClient()
    p._client = fake
    async for _ in p.stream_chat([], [], system="", model="llama3", effort="max"):
        pass
    kwargs = fake.chat.completions.kwargs
    assert "reasoning_effort" not in kwargs
    assert "reasoning" not in kwargs.get("extra_body", {})
