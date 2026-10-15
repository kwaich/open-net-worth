"""Connector mcp_image delivery: an external-MCP tool's captured image (a screenshot, e.g.)
reaches a chat user as a native PHOTO (mirrors test_connector_artifact.py's shape exactly,
send_photo instead of send_document), or a text note where media isn't supported."""

from __future__ import annotations

import asyncio

import backend.connectors.processor as proc
from backend.connectors.base import BaseChannelAdapter


class _PhotoConnector(BaseChannelAdapter):
    platform = "telegram"

    def __init__(self):
        self.photos = []
        self.texts = []

    async def _deliver(self, channel_id, part):
        self.texts.append(part)

    async def send_text(self, channel_id, text):
        self.texts.append(text)

    async def send_photo(self, channel_id, content, caption=""):
        self.photos.append((channel_id, content, caption))


class _NoMediaConnector(BaseChannelAdapter):
    def __init__(self):
        self.texts = []

    async def send_text(self, channel_id, text):
        self.texts.append(text)

    # inherits base send_photo → raises NotImplementedError


def test_mcp_image_delivered_as_photo(monkeypatch):
    monkeypatch.setattr(
        "backend.services.attachments.read_attachment_bytes", lambda ref_id: b"\x89PNG"
    )
    c = _PhotoConnector()
    attachments = [{"ref_id": "att-1", "kind": "image", "summary": "Screenshot from vnexpress"}]
    asyncio.run(proc._deliver_mcp_images(c, "42", attachments))

    assert len(c.photos) == 1
    channel, content, caption = c.photos[0]
    assert channel == "42"
    assert content == b"\x89PNG"
    assert caption == "Screenshot from vnexpress"


def test_multiple_images_each_deliver_separately(monkeypatch):
    monkeypatch.setattr(
        "backend.services.attachments.read_attachment_bytes", lambda ref_id: ref_id.encode()
    )
    c = _PhotoConnector()
    attachments = [
        {"ref_id": "att-1", "kind": "image", "summary": "one"},
        {"ref_id": "att-2", "kind": "image", "summary": "two"},
    ]
    asyncio.run(proc._deliver_mcp_images(c, "1", attachments))
    assert [p[1] for p in c.photos] == [b"att-1", b"att-2"]


def test_no_media_support_falls_back_to_text(monkeypatch):
    monkeypatch.setattr(
        "backend.services.attachments.read_attachment_bytes", lambda ref_id: b"\x89PNG"
    )
    c = _NoMediaConnector()
    asyncio.run(proc._deliver_mcp_images(c, "1", [{"ref_id": "att-1", "summary": "A chart"}]))
    assert len(c.texts) == 1
    assert "A chart" in c.texts[0] and "web app" in c.texts[0]


def test_a_deleted_attachment_is_skipped_not_a_crash(monkeypatch):
    monkeypatch.setattr("backend.services.attachments.read_attachment_bytes", lambda ref_id: None)
    c = _PhotoConnector()
    asyncio.run(proc._deliver_mcp_images(c, "1", [{"ref_id": "gone", "summary": "x"}]))
    assert c.photos == [] and c.texts == []


def test_no_ref_id_is_skipped():
    c = _PhotoConnector()
    asyncio.run(proc._deliver_mcp_images(c, "1", [{"summary": "x"}]))
    assert c.photos == [] and c.texts == []


def test_telegram_adapter_supports_media():
    from backend.connectors.telegram.handlers import _TelegramAdapter

    assert _TelegramAdapter.supports_media is True
