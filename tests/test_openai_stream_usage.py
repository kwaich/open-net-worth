"""Usage capture across streaming conventions (openai_provider.stream_chat).

Two conventions exist in the wild for where the trailing usage object rides:
  - OpenAI:     a FINAL chunk with an EMPTY choices list (stream_options contract)
  - OpenRouter: the final chunk that still CARRIES a choice (finish_reason=stop)
Live incident 2026-07-17: kimi-k3 via OpenRouter booked input=0/output=0/cost=$0
("unmeasured") because only the empty-choices convention was read — the real
reported cost in ``usage.cost`` was dropped on the floor.

Also covers reasoning deltas: OpenRouter reasoning models stream the thinking
text as ``delta.reasoning``, surfaced as ``thinking_delta`` events.
"""

from __future__ import annotations

import pytest

from backend.ai.models.openai_provider import OpenAIProvider


class _Obj:
    """Attribute bag (SDK chunks are pydantic models; tests fake the shape)."""

    def __init__(self, **kw):
        self.__dict__.update(kw)


def _delta(content=None, reasoning=None):
    return _Obj(content=content, reasoning=reasoning, tool_calls=None)


def _choice(delta, finish_reason=None):
    return _Obj(delta=delta, finish_reason=finish_reason)


def _usage(prompt=91, completion=56, cost=0.001113):
    return _Obj(
        prompt_tokens=prompt,
        completion_tokens=completion,
        prompt_tokens_details=_Obj(cached_tokens=0),
        cost=cost,
    )


class _FakeStream:
    def __init__(self, chunks):
        self._chunks = list(chunks)

    def __aiter__(self):
        return self

    async def __anext__(self):
        if not self._chunks:
            raise StopAsyncIteration
        return self._chunks.pop(0)


class _FakeClient:
    def __init__(self, chunks):
        completions = _Obj(kwargs=None)

        async def create(**kwargs):
            completions.kwargs = kwargs
            return _FakeStream(chunks)

        completions.create = create
        self.chat = _Obj(completions=completions)


def _provider(base_url: str, chunks) -> OpenAIProvider:
    p = OpenAIProvider(base_url=base_url, api_key="sk-test")
    p._client = _FakeClient(chunks)
    return p


async def _run(p: OpenAIProvider, model="moonshotai/kimi-k3") -> list[dict]:
    events = []
    async for ev in p.stream_chat([], [], system="", model=model, effort="medium"):
        events.append(ev)
    return events


@pytest.mark.asyncio
async def test_usage_on_final_chunk_with_choice_is_captured():
    """OpenRouter convention: usage rides the last chunk WITH a choice."""
    chunks = [
        _Obj(choices=[_choice(_delta(content="OK"))], usage=None),
        _Obj(choices=[_choice(_delta(), finish_reason="stop")], usage=_usage()),
    ]
    events = await _run(_provider("https://openrouter.ai/api/v1", chunks))
    usage = next(e for e in events if e["type"] == "usage")
    assert usage["input_tokens"] == 91
    assert usage["output_tokens"] == 56
    assert usage["cost_usd"] == pytest.approx(0.001113)


@pytest.mark.asyncio
async def test_usage_on_empty_choices_chunk_still_captured():
    """OpenAI convention (regression guard): trailing empty-choices usage chunk."""
    chunks = [
        _Obj(choices=[_choice(_delta(content="OK"), finish_reason="stop")], usage=None),
        _Obj(choices=[], usage=_usage(prompt=10, completion=2, cost=None)),
    ]
    events = await _run(_provider("https://api.openai.com/v1", chunks), model="gpt-5")
    usage = next(e for e in events if e["type"] == "usage")
    assert usage["input_tokens"] == 10
    assert usage["output_tokens"] == 2


@pytest.mark.asyncio
async def test_reasoning_deltas_surface_as_thinking():
    """delta.reasoning (OpenRouter reasoning models) → thinking_delta events."""
    chunks = [
        _Obj(choices=[_choice(_delta(reasoning="hmm, "))], usage=None),
        _Obj(choices=[_choice(_delta(reasoning="the user greets"))], usage=None),
        _Obj(choices=[_choice(_delta(content="Hey!"), finish_reason="stop")], usage=_usage()),
    ]
    events = await _run(_provider("https://openrouter.ai/api/v1", chunks))
    thinking = [e["text"] for e in events if e["type"] == "thinking_delta"]
    assert thinking == ["hmm, ", "the user greets"]
    assert [e["text"] for e in events if e["type"] == "text_delta"] == ["Hey!"]
