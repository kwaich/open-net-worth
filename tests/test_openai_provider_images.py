"""Regression test for a live incident (2026-07-18): a user sent a photo via Telegram

while the chat provider was configured as ``openai_compatible`` (xAI grok-4.5), and Lucius
replied "I don't see any images attached" — the image block never reached the model.

Root cause: ``OpenAIProvider._messages_to_openai`` (used by ``stream_chat``, the live
interactive chat/tool loop) only recognized ``tool_result``/``text`` blocks in a user
turn; an Anthropic-shaped ``{"type": "image", ...}`` block fell through the loop and was
silently dropped — no exception, no log. ``OpenAIProvider.complete()`` already converted
images correctly; ``stream_chat``'s converter did not.

Hermetic: no network.
"""

from __future__ import annotations

import pytest

from backend.ai.models.openai_provider import OpenAIProvider


def _image_block(data: str = "ZmFrZQ==", media_type: str = "image/jpeg") -> dict:
    return {"type": "image", "source": {"type": "base64", "media_type": media_type, "data": data}}


class TestMessagesToOpenAIImages:
    def test_image_with_caption_becomes_image_url_content_list(self):
        messages = [
            {
                "role": "user",
                "content": [_image_block(), {"type": "text", "text": "what's this?"}],
            }
        ]
        out = OpenAIProvider._messages_to_openai(messages, system="")
        user_msg = out[-1]
        assert user_msg["role"] == "user"
        assert isinstance(user_msg["content"], list)
        assert user_msg["content"][0] == {
            "type": "image_url",
            "image_url": {"url": "data:image/jpeg;base64,ZmFrZQ=="},
        }
        assert user_msg["content"][1] == {"type": "text", "text": "what's this?"}

    def test_image_with_no_caption_still_included(self):
        # A bare photo (no caption) must not degrade to an empty/dropped user turn.
        messages = [{"role": "user", "content": [_image_block()]}]
        out = OpenAIProvider._messages_to_openai(messages, system="")
        assert len(out) == 1
        user_msg = out[0]
        assert user_msg["role"] == "user"
        assert user_msg["content"] == [
            {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,ZmFrZQ=="}}
        ]

    def test_multiple_images_all_preserved(self):
        messages = [
            {
                "role": "user",
                "content": [
                    _image_block("aaaa"),
                    _image_block("bbbb"),
                    {"type": "text", "text": "compare these"},
                ],
            }
        ]
        out = OpenAIProvider._messages_to_openai(messages, system="")
        content = out[-1]["content"]
        urls = [b["image_url"]["url"] for b in content if b["type"] == "image_url"]
        assert urls == ["data:image/jpeg;base64,aaaa", "data:image/jpeg;base64,bbbb"]
        assert content[-1] == {"type": "text", "text": "compare these"}

    def test_text_only_user_turn_unchanged(self):
        # No image blocks present — behavior must stay byte-identical to before the fix.
        messages = [{"role": "user", "content": [{"type": "text", "text": "hello"}]}]
        out = OpenAIProvider._messages_to_openai(messages, system="")
        assert out[-1] == {"role": "user", "content": "hello"}


@pytest.mark.asyncio
async def test_stream_chat_sends_image_url_block_to_the_wire():
    """End-to-end through stream_chat: the fake client actually receives the image."""

    class _FakeStream:
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

    provider = OpenAIProvider(base_url="https://api.x.ai/v1", api_key="sk-test")
    fake = _FakeClient()
    provider._client = fake

    messages = [
        {
            "role": "user",
            "content": [_image_block(), {"type": "text", "text": "log this receipt"}],
        }
    ]
    async for _ in provider.stream_chat(messages, [], system="", model="grok-4.5", effort="low"):
        pass

    sent_messages = fake.chat.completions.kwargs["messages"]
    user_msg = sent_messages[-1]
    assert user_msg["role"] == "user"
    image_blocks = [b for b in user_msg["content"] if b["type"] == "image_url"]
    assert len(image_blocks) == 1
    assert image_blocks[0]["image_url"]["url"] == "data:image/jpeg;base64,ZmFrZQ=="


@pytest.mark.asyncio
async def test_stream_chat_closes_upstream_stream_on_early_aclose():
    """A client disconnect (Stop) makes assistant.run aclose() stream_chat mid-stream. That
    must EXPLICITLY close the upstream provider stream so token generation — and billing —
    stops promptly, instead of abandoning the still-open request to GC/backpressure while the
    model keeps writing. Regression for the OpenAI-compatible (OpenRouter/GLM) path, which
    had no `async with`/close on the stream (unlike anthropic_provider)."""

    class _Delta:
        reasoning = None
        tool_calls = None

        def __init__(self, content):
            self.content = content

    class _Choice:
        finish_reason = None

        def __init__(self, content):
            self.delta = _Delta(content)

    class _Chunk:
        usage = None

        def __init__(self, content):
            self.choices = [_Choice(content)]

    class _EndlessStream:
        """Emulates a live model STILL generating — never ends on its own, so the only way
        generation stops is the consumer explicitly closing it."""

        def __init__(self):
            self.closed = False
            self._n = 0

        def __aiter__(self):
            return self

        async def __anext__(self):
            self._n += 1
            return _Chunk(f"tok{self._n} ")

        async def close(self):
            self.closed = True

    stream = _EndlessStream()

    class _FakeCompletions:
        async def create(self, **kwargs):
            return stream

    class _FakeClient:
        def __init__(self):
            self.chat = type("C", (), {})()
            self.chat.completions = _FakeCompletions()

    provider = OpenAIProvider(base_url="https://openrouter.ai/api/v1", api_key="sk-test")
    provider._client = _FakeClient()

    gen = provider.stream_chat(
        [{"role": "user", "content": "hi"}], [], system="", model="z-ai/glm-5.2", effort="none"
    )
    first = await gen.__anext__()  # pull one streamed token
    assert first["type"] == "text_delta"
    assert stream.closed is False  # still generating

    await gen.aclose()  # the disconnect — assistant.run's finally does exactly this
    assert stream.closed is True  # upstream connection was explicitly torn down


class TestNoVisionModelStripsImages:
    """Regression (live 2026-07-19): a photo lingering in a long Telegram history 404'd
    EVERY subsequent TEXT turn once the default model was switched to a NON-vision model
    (z-ai/glm-5.2) — OpenRouter: 'No endpoints found that support image input'. stream_chat
    now retries once with the images stripped so a text turn still works."""

    def test_strip_image_parts_replaces_images_with_placeholder(self):
        msgs = [
            {
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,AAA"}},
                    {"type": "text", "text": "Spent $5.95 at ACE"},
                ],
            },
            {"role": "assistant", "content": "ok"},
        ]
        out, had = OpenAIProvider._strip_image_parts(msgs)
        assert had is True
        assert "image omitted" in out[0]["content"]
        assert "Spent $5.95 at ACE" in out[0]["content"]
        assert isinstance(out[0]["content"], str)  # collapsed to plain text
        assert out[1]["content"] == "ok"  # non-image message untouched

    def test_strip_image_parts_no_images_is_noop(self):
        out, had = OpenAIProvider._strip_image_parts([{"role": "user", "content": "hi"}])
        assert had is False
        assert out[0]["content"] == "hi"
