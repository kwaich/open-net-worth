"""Regression tests for AIProvider.stream_complete() (2026-08-27) — the streaming twin
of complete(), built so the fold-summary compacting bar can show a REAL percentage
(tokens streamed so far ÷ an expected-length target) instead of a binary start/done
signal. Deliberately narrower than complete(): no images, no response_format — only
what background text summarization (fold) needs.

Each provider's version must, like complete(): resolve the stored default model when
none is passed, record cost via ai.cost_tracker.record, and yield ONLY
{"type": "text_delta", "text": str} chunks — no tool_use, no usage event surfaced to
the caller (there is no turn-level consumer to hand a usage event to, unlike
stream_chat).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest


async def _drain(agen):
    return [ev async for ev in agen]


# ── Anthropic ────────────────────────────────────────────────────────────────────


def _make_fake_anthropic_stream(chunks: list[str]):
    class FakeUsage:
        input_tokens = 42
        output_tokens = 7
        cache_creation_input_tokens = 0
        cache_read_input_tokens = 0
        cache_creation = None

    class FakeFinalMsg:
        usage = FakeUsage()

    class FakeDelta:
        def __init__(self, text):
            self.text = text

    class FakeEvent:
        def __init__(self, text):
            self.type = "content_block_delta"
            self.delta = FakeDelta(text)

    class FakeStream:
        def __init__(self):
            self._events = iter([FakeEvent(c) for c in chunks])

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            pass

        def __aiter__(self):
            return self

        async def __anext__(self):
            try:
                return next(self._events)
            except StopIteration:
                raise StopAsyncIteration

        async def get_final_message(self):
            return FakeFinalMsg()

    return FakeStream()


@pytest.mark.asyncio
async def test_anthropic_stream_complete_yields_text_deltas_in_order():
    from backend.ai.models.anthropic_provider import AnthropicProvider

    provider = AnthropicProvider.__new__(AnthropicProvider)
    fake_client = MagicMock()
    fake_client.messages.stream = MagicMock(
        return_value=_make_fake_anthropic_stream(["Hello", ", ", "world"])
    )
    provider.client = fake_client

    events = await _drain(provider.stream_complete("summarize this", model="claude-haiku-4-5"))

    assert events == [
        {"type": "text_delta", "text": "Hello"},
        {"type": "text_delta", "text": ", "},
        {"type": "text_delta", "text": "world"},
    ]


@pytest.mark.asyncio
async def test_anthropic_stream_complete_records_cost(monkeypatch):
    from backend.ai.models.anthropic_provider import AnthropicProvider

    provider = AnthropicProvider.__new__(AnthropicProvider)
    fake_client = MagicMock()
    fake_client.messages.stream = MagicMock(return_value=_make_fake_anthropic_stream(["hi"]))
    provider.client = fake_client

    recorded = {}

    def fake_record(source, model, in_tok, out_tok, cache_write, cache_read, cost_usd, **_kw):
        recorded.update(
            source=source,
            model=model,
            in_tok=in_tok,
            out_tok=out_tok,
            cost_usd=cost_usd,
        )

    monkeypatch.setattr("backend.ai.cost_tracker.record", fake_record)

    await _drain(provider.stream_complete("x", model="claude-haiku-4-5", source="context_fold"))

    assert recorded["source"] == "context_fold"
    assert recorded["model"] == "claude-haiku-4-5"
    assert recorded["in_tok"] == 42
    assert recorded["out_tok"] == 7


@pytest.mark.asyncio
async def test_anthropic_stream_complete_resolves_stored_model_when_none_given(monkeypatch):
    import backend.db.model_config_store as mcs
    from backend.ai.models.anthropic_provider import AnthropicProvider

    mcs.ModelConfigStore().save({"provider": "anthropic", "model": "claude-haiku-4-5"})
    monkeypatch.setenv("AI_MODEL", "claude-sonnet-4-6")

    provider = AnthropicProvider.__new__(AnthropicProvider)
    captured = {}

    def fake_stream(**kwargs):
        captured.update(kwargs)
        return _make_fake_anthropic_stream([])

    fake_client = MagicMock()
    fake_client.messages.stream = fake_stream
    provider.client = fake_client

    await _drain(provider.stream_complete("hello"))

    assert captured["model"] == "claude-haiku-4-5"


# ── OpenAI-compatible ────────────────────────────────────────────────────────────


def _make_fake_openai_stream(chunks: list[str]):
    class FakeUsage:
        prompt_tokens = 42
        completion_tokens = 7

    class FakeDelta:
        def __init__(self, text):
            self.content = text

    class FakeChoice:
        def __init__(self, text):
            self.delta = FakeDelta(text)

    class FakeChunk:
        def __init__(self, text, usage=None):
            self.choices = [FakeChoice(text)] if text is not None else []
            self.usage = usage

    async def gen():
        for c in chunks:
            yield FakeChunk(c)
        yield FakeChunk(None, usage=FakeUsage())

    class FakeAsyncIterable:
        def __aiter__(self):
            return gen()

    return FakeAsyncIterable()


@pytest.mark.asyncio
async def test_openai_stream_complete_yields_text_deltas_in_order():
    from backend.ai.models.openai_provider import OpenAIProvider

    provider = OpenAIProvider(base_url="http://localhost:11434/v1", api_key="x")
    fake_client = MagicMock()
    fake_client.chat.completions.create = AsyncMock(
        return_value=_make_fake_openai_stream(["Hello", ", ", "world"])
    )
    provider._client = fake_client

    events = await _drain(provider.stream_complete("summarize this", model="gpt-4o-mini"))

    assert events == [
        {"type": "text_delta", "text": "Hello"},
        {"type": "text_delta", "text": ", "},
        {"type": "text_delta", "text": "world"},
    ]


@pytest.mark.asyncio
async def test_openai_stream_complete_records_cost(monkeypatch):
    from backend.ai.models.openai_provider import OpenAIProvider

    provider = OpenAIProvider(base_url="http://localhost:11434/v1", api_key="x")
    fake_client = MagicMock()
    fake_client.chat.completions.create = AsyncMock(return_value=_make_fake_openai_stream(["hi"]))
    provider._client = fake_client

    recorded = {}

    def fake_record(source, model, in_tok, out_tok, cache_write, cache_read, cost_usd, **_kw):
        recorded.update(source=source, model=model, in_tok=in_tok, out_tok=out_tok)

    monkeypatch.setattr("backend.ai.cost_tracker.record", fake_record)

    await _drain(provider.stream_complete("x", model="gpt-4o-mini", source="context_fold"))

    assert recorded["source"] == "context_fold"
    assert recorded["model"] == "gpt-4o-mini"
    assert recorded["in_tok"] == 42
    assert recorded["out_tok"] == 7
