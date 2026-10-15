"""OpenAIProvider.stream_chat's image-modality-refusal translation
(``backend/ai/models/openai_provider.py``, roughly lines 476-505).

SUPERSEDES this file's own pre-Phase-3 pinned behavior. RFC
``.codex/rfcs/cross-channel-attachments-vision-and-statement-evidence.md`` section 4,
Phase 0 item 4, pinned the OLD reactive strip-and-retry mechanism here first (commit
``5c1a3bf7``) specifically so Phase 3's replacement would read as an intentional,
reviewable change rather than a silent regression — its own docstring said so explicitly:
"When [Phase 3] lands, THIS file's assertions on the placeholder text and the single
blind retry are expected to go red — that is the point of pinning them here first." This
is that landing: the file is REWRITTEN in place (git history keeps the original pinned
version, e.g. ``git show 5c1a3bf7:tests/test_char_openai_provider_reactive_image_strip.py``)
rather than kept as stale scaffolding that no longer describes the running code.

Old (retired) behavior: on a narrow image/vision/modal-shaped refusal with images
actually present, the provider itself recorded ``supports_vision=False``, replaced every
image part with the literal placeholder ``"[image omitted — the selected model has no
vision]"``, and retried the SAME request once, inline, in this file's own class.

New (pinned here) behavior: the provider does none of that. It raises a typed
``UnsupportedModalityError(provider_ref, "image")`` for the SAME narrow refusal shape and
stops — no retry, no strip, no learn. The retry/learn/rebuild decision moved to shared
turn orchestration (``ai/assistant.py``'s ``_stream_round``, pinned by
``tests/test_capability_retry_orchestration.py``) because an adapter must not itself
decide to invoke a different model or rebuild the request — see the RFC's "Alternatives
considered: put capability fallback inside each provider adapter."
"""

from __future__ import annotations

import pytest

from backend.ai.models.openai_provider import OpenAIProvider
from backend.core.errors import UnsupportedModalityError


def _image_block(data: str = "ZmFrZQ==", media_type: str = "image/jpeg") -> dict:
    return {"type": "image", "source": {"type": "base64", "media_type": media_type, "data": data}}


class _EmptyStream:
    """A minimal async-iterable standing in for an SDK stream with no chunks."""

    def __aiter__(self):
        return self

    async def __anext__(self):
        raise StopAsyncIteration


class _FakeCompletions:
    """Raises a vision-refusal-shaped error on the first ``refusals`` calls, then succeeds."""

    def __init__(self, refusals: int = 1, message: str = "image input") -> None:
        self.calls: list[dict] = []
        self._refusals = refusals
        self._message = message

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        if len(self.calls) <= self._refusals:
            raise RuntimeError(self._message)
        return _EmptyStream()


class _FakeClient:
    def __init__(self, completions: _FakeCompletions) -> None:
        self.chat = type("C", (), {})()
        self.chat.completions = completions


def _provider(monkeypatch, completions: _FakeCompletions) -> OpenAIProvider:
    provider = OpenAIProvider(base_url="https://openrouter.ai/api/v1", api_key="sk-test")
    provider._client = _FakeClient(completions)
    # An UNKNOWN model (never learned, never declared): the PROACTIVE strip
    # (``_known_no_vision``) must stay dormant so the test exercises the reactive
    # translation this file pins, not the already-learned fast path pinned by
    # test_vision_capability.py.
    monkeypatch.setattr("backend.services.vision_capability.supports_vision", lambda *a, **k: None)
    return provider


@pytest.mark.asyncio
async def test_a_narrow_image_refusal_raises_the_typed_error_and_makes_no_retry(monkeypatch):
    completions = _FakeCompletions(
        refusals=1, message="invalid_request_error: this model does not support image input"
    )
    provider = _provider(monkeypatch, completions)

    messages = [
        {"role": "user", "content": [_image_block(), {"type": "text", "text": "what is this?"}]}
    ]

    with pytest.raises(UnsupportedModalityError) as exc:
        async for _ in provider.stream_chat(
            messages, [], system="", model="mystery-model", effort="low"
        ):
            pass

    assert len(completions.calls) == 1, (
        "the provider itself must never retry -- that decision moved up"
    )
    assert exc.value.modality == "image"
    assert "mystery-model" in exc.value.provider_ref

    first_content = completions.calls[0]["messages"][-1]["content"]
    assert any(part.get("type") == "image_url" for part in first_content), (
        "the ONE attempt made must still try the real image natively -- capability is "
        "unknown, not declared blind"
    )


@pytest.mark.asyncio
async def test_the_provider_never_records_a_learned_capability_itself(monkeypatch):
    """Learning + retrying both moved to shared turn orchestration — the provider raising
    the typed error must not ALSO write to the capability store; that would let the two
    ever disagree about which layer owns the decision."""
    completions = _FakeCompletions(refusals=1, message="this model has no vision")
    provider = _provider(monkeypatch, completions)

    learned: list = []
    monkeypatch.setattr(
        "backend.services.vision_capability.record_vision_capability",
        lambda *args, **kwargs: learned.append(args),
    )

    messages = [{"role": "user", "content": [_image_block(), {"type": "text", "text": "hi"}]}]

    with pytest.raises(UnsupportedModalityError):
        async for _ in provider.stream_chat(
            messages, [], system="", model="mystery-model", effort="low"
        ):
            pass

    assert learned == []


@pytest.mark.asyncio
async def test_an_unrelated_error_is_never_translated(monkeypatch):
    """The trigger is still a narrow substring match on the raised error text (unchanged
    from the pre-Phase-3 shape — only what happens on a MATCH changed). An error that says
    nothing about image/vision/modal (auth, rate limit, ...) must surface completely
    untouched, never wrapped as an UnsupportedModalityError."""
    completions = _FakeCompletions(refusals=1, message="401 unauthorized: invalid api key")
    provider = _provider(monkeypatch, completions)

    messages = [{"role": "user", "content": [_image_block(), {"type": "text", "text": "hi"}]}]

    with pytest.raises(RuntimeError, match="unauthorized"):
        async for _ in provider.stream_chat(
            messages, [], system="", model="mystery-model", effort="low"
        ):
            pass

    assert len(completions.calls) == 1


@pytest.mark.asyncio
async def test_a_text_only_turn_is_never_translated_even_on_a_vision_shaped_error(monkeypatch):
    """``had_images`` still gates the translation too — a vision-shaped refusal on a turn
    that carried no image at all must surface as-is (nothing was rejected FOR being an
    image, whatever the message text says)."""
    completions = _FakeCompletions(refusals=1, message="this endpoint requires image input")
    provider = _provider(monkeypatch, completions)

    messages = [{"role": "user", "content": "just text, no image"}]

    with pytest.raises(RuntimeError, match="requires image input"):
        async for _ in provider.stream_chat(
            messages, [], system="", model="mystery-model", effort="low"
        ):
            pass

    assert len(completions.calls) == 1


@pytest.mark.asyncio
async def test_the_proactive_known_blind_strip_is_unaffected(monkeypatch):
    """The DIFFERENT, unchanged code path: when the model is already POSITIVELY known
    blind (not this file's unknown-refusal scenario), stream_chat still strips images
    BEFORE the call, exactly as before Phase 3 -- that mechanism was never part of the
    reactive retry this file's rewrite replaces."""
    completions = _FakeCompletions(refusals=0)
    provider = _provider(monkeypatch, completions)
    monkeypatch.setattr("backend.services.vision_capability.supports_vision", lambda *a, **k: False)

    messages = [{"role": "user", "content": [_image_block(), {"type": "text", "text": "hi"}]}]

    events = [
        e
        async for e in provider.stream_chat(
            messages, [], system="", model="mystery-model", effort="low"
        )
    ]

    assert events[-1]["type"] == "done"
    sent_content = completions.calls[0]["messages"][-1]["content"]
    assert isinstance(sent_content, str)
    assert "[image omitted — the selected model has no vision]" in sent_content
