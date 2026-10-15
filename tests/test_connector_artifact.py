"""Connector artifact delivery: a created artifact reaches a chat user as a native FILE
attachment (Hermes's sendDocument model), or a text note where files aren't supported."""

from __future__ import annotations

import asyncio

import backend.connectors.processor as proc
from backend.connectors.base import BaseChannelAdapter


class _FileConnector(BaseChannelAdapter):
    platform = "telegram"

    def __init__(self):
        self.docs = []
        self.texts = []

    async def _deliver(self, channel_id, part):
        self.texts.append(part)

    async def send_text(self, channel_id, text):
        self.texts.append(text)

    async def send_document(self, channel_id, content, filename, caption=""):
        self.docs.append((channel_id, content, filename, caption))


class _NoFileConnector(BaseChannelAdapter):
    def __init__(self):
        self.texts = []

    async def send_text(self, channel_id, text):
        self.texts.append(text)

    # inherits base send_document → raises NotImplementedError


def test_artifact_delivered_as_document(monkeypatch):
    monkeypatch.setattr(
        "backend.services.artifacts.read_bytes", lambda _id: b"<html><body>chart</body></html>"
    )
    c = _FileConnector()
    art = {"id": "a1", "title": "Net worth chart", "artifactType": "html"}
    asyncio.run(proc._deliver_artifact(c, "42", art))

    assert len(c.docs) == 1
    channel, content, filename, caption = c.docs[0]
    assert channel == "42"
    assert content == b"<html><body>chart</body></html>"
    assert filename == "Net worth chart.html"
    assert caption == "Net worth chart"


def test_svg_artifact_uses_svg_extension(monkeypatch):
    monkeypatch.setattr("backend.services.artifacts.read_bytes", lambda _id: b"<svg/>")
    c = _FileConnector()
    asyncio.run(
        proc._deliver_artifact(c, "1", {"id": "a2", "title": "Diagram", "artifactType": "svg"})
    )
    assert c.docs[0][2] == "Diagram.svg"


def test_explicit_filename_wins(monkeypatch):
    monkeypatch.setattr("backend.services.artifacts.read_bytes", lambda _id: b"x")
    c = _FileConnector()
    asyncio.run(
        proc._deliver_artifact(c, "1", {"id": "a3", "title": "T", "filename": "report.html"})
    )
    assert c.docs[0][2] == "report.html"


def test_no_file_support_falls_back_to_text(monkeypatch):
    monkeypatch.setattr("backend.services.artifacts.read_bytes", lambda _id: b"x")
    c = _NoFileConnector()
    asyncio.run(proc._deliver_artifact(c, "1", {"id": "a4", "title": "Budget"}))
    assert len(c.texts) == 1
    assert "Budget" in c.texts[0] and "web app" in c.texts[0]


def test_no_id_is_noop(monkeypatch):
    c = _FileConnector()
    asyncio.run(proc._deliver_artifact(c, "1", {"title": "x"}))
    assert c.docs == [] and c.texts == []


def test_telegram_adapter_supports_documents():
    from backend.connectors.telegram.handlers import _TelegramAdapter

    assert _TelegramAdapter.supports_documents is True


def test_pdf_artifact_is_delivered_as_its_own_bytes(monkeypatch):
    monkeypatch.setattr("backend.services.artifacts.read_bytes", lambda _id: b"%PDF-1.7 report")
    c = _FileConnector()
    asyncio.run(
        proc._deliver_artifact(c, "1", {"id": "a5", "title": "Statement", "artifactType": "pdf"})
    )
    assert c.docs[0][1] == b"%PDF-1.7 report"
    assert c.docs[0][2] == "Statement.pdf"
