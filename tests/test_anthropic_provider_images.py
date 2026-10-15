"""Native image delivery through the Anthropic API transport.

Phase-0 pin (RFC cross-channel-attachments-vision-and-statement-evidence, item 3): the
app's internal message shape is ALREADY Anthropic's own wire shape
(``{"type": "image", "source": {"type": "base64", ...}}``), so this transport's job is to
pass an image block through untouched — never re-encode it, never drop it, never let the
last-message cache-control merge (``_cache_last_message``) silently strip the image type/
source fields it doesn't own. Byte/count bounds are enforced transport-agnostically at the
shared boundary (``backend/ai/image_payloads.py``, pinned by ``test_image_payloads.py`` and
the ``stream_turn`` boundary tests in ``test_connector_conversation.py``); this file pins
the transport-specific contract only.

Hermetic: no network. ``AnthropicProvider.client`` is swapped for a fake that records the
exact kwargs handed to ``messages.stream(...)``.
"""

from __future__ import annotations

import pytest

from backend.ai.models.anthropic_provider import AnthropicProvider


def _image_block(data: str = "ZmFrZQ==", media_type: str = "image/jpeg") -> dict:
    return {"type": "image", "source": {"type": "base64", "media_type": media_type, "data": data}}


class _FakeUsage:
    input_tokens = 5
    output_tokens = 3
    cache_creation_input_tokens = 0
    cache_read_input_tokens = 0
    cache_creation = None


class _FakeFinalMessage:
    usage = _FakeUsage()
    content: list = []
    stop_reason = "end_turn"


class _FakeStreamCtx:
    def __aiter__(self):
        return self

    async def __anext__(self):
        raise StopAsyncIteration

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc):
        return False

    async def get_final_message(self):
        return _FakeFinalMessage()


class _FakeMessages:
    def __init__(self) -> None:
        self.kwargs: dict | None = None

    def stream(self, **kwargs):
        self.kwargs = kwargs
        return _FakeStreamCtx()


class _FakeClient:
    def __init__(self) -> None:
        self.messages = _FakeMessages()


def _provider() -> tuple[AnthropicProvider, _FakeClient]:
    provider = AnthropicProvider(api_key="sk-ant-test")
    fake = _FakeClient()
    provider.client = fake
    return provider, fake


@pytest.mark.asyncio
async def test_an_image_block_rides_the_wire_completely_unmodified():
    provider, fake = _provider()
    image = _image_block()
    messages = [{"role": "user", "content": [image, {"type": "text", "text": "what is this?"}]}]

    async for _ in provider.stream_chat(messages, [], system="", model="claude-sonnet-4-6"):
        pass

    sent = fake.messages.kwargs["messages"][-1]["content"]
    sent_image = next(p for p in sent if p.get("type") == "image")
    assert sent_image["source"] == image["source"]  # byte-for-byte: media_type + data untouched


@pytest.mark.asyncio
async def test_an_image_as_the_LAST_content_part_still_keeps_its_type_and_source():
    """``_cache_last_message`` merges ``cache_control`` into the trailing content part —
    when that part happens to be the image itself (no caption after it), the merge must add
    the field, never replace the block or drop ``source``."""
    provider, fake = _provider()
    image = _image_block(data="dHJhaWxpbmc=")
    messages = [{"role": "user", "content": [{"type": "text", "text": "look"}, image]}]

    async for _ in provider.stream_chat(messages, [], system="", model="claude-sonnet-4-6"):
        pass

    sent = fake.messages.kwargs["messages"][-1]["content"]
    trailing = sent[-1]
    assert trailing["type"] == "image"
    assert trailing["source"] == image["source"]
    assert trailing.get("cache_control") == {"type": "ephemeral"}


@pytest.mark.asyncio
async def test_multiple_images_in_one_turn_all_survive_in_order():
    provider, fake = _provider()
    images = [_image_block(data=f"img{i}") for i in range(3)]
    messages = [{"role": "user", "content": [*images, {"type": "text", "text": "compare these"}]}]

    async for _ in provider.stream_chat(messages, [], system="", model="claude-sonnet-4-6"):
        pass

    sent = fake.messages.kwargs["messages"][-1]["content"]
    sent_images = [p for p in sent if p.get("type") == "image"]
    assert [p["source"]["data"] for p in sent_images] == ["img0", "img1", "img2"]
