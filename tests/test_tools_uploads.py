"""ai/tools/uploads.py — get_uploads, delete_upload, and read_attachments (RFC
``cross-channel-attachments-vision-and-statement-evidence`` §5 / Phase 3 item 19).

Also regression-covers a real dispatch-time bug this change found in passing:
``get_uploads``/``delete_upload`` returned a plain dict instead of the required
``(text, proposed_change)`` tuple every ``registry.dispatch`` handler must return —
which crashed with ``ValueError: too many values to unpack`` the moment either tool was
actually dispatched (never caught before because nothing dispatched them end to end).
"""

from __future__ import annotations

import json

import pytest

from backend.ai.tools import registry
from backend.services import attachments as attachments_svc


@pytest.fixture(autouse=True)
def _isolated_attachments(tmp_path, monkeypatch, db_isolated):
    import backend.db.attachment_store as as_mod

    att_dir = tmp_path / "attachments"
    att_dir.mkdir()
    monkeypatch.setattr(as_mod, "ATTACHMENTS_DIR", att_dir)
    attachments_svc._cache_drop()
    yield
    attachments_svc._cache_drop()


def _text_ref(**over) -> str:
    entry = {"kind": "text", "text": "Date,Amount\n2026-07-01,-12.50", "summary": "bank.csv"}
    entry.update(over)
    return attachments_svc.create_attachment(entry, filename="bank.csv")


def _image_ref(**over) -> str:
    entry = {"kind": "image", "media_type": "image/jpeg", "data": "ZmFrZQ==", "summary": "r.jpg"}
    entry.update(over)
    return attachments_svc.create_attachment(entry, filename="r.jpg")


class TestGetUploadsAndDeleteUpload:
    """The dispatch-shape regression: both must round-trip through the REAL dispatcher,
    not just be callable as bare Python functions."""

    async def test_get_uploads_dispatches_and_lists(self):
        _text_ref()
        text, proposed = await registry.dispatch("get_uploads", {})
        assert proposed is None
        data = json.loads(text)
        assert data["ok"] is True
        assert data["count"] == 1

    async def test_delete_upload_dispatches_and_removes(self):
        ref = _text_ref()
        text, proposed = await registry.dispatch("delete_upload", {"ref_id": ref})
        assert proposed is None
        assert json.loads(text) == {"ok": True, "deleted": ref}
        assert attachments_svc.get_attachment_meta(ref) is None

    async def test_delete_upload_unknown_id_is_an_honest_error(self):
        text, proposed = await registry.dispatch("delete_upload", {"ref_id": "att_nope"})
        assert proposed is None
        data = json.loads(text)
        assert data["ok"] is False
        assert "att_nope" in data["error"]


class TestReadAttachments:
    async def test_a_text_attachment_is_returned_directly_no_reader_call(self, monkeypatch):
        async def must_not_call(*a, **k):
            raise AssertionError("a text-kind attachment must never invoke the reader")

        monkeypatch.setattr("backend.services.image_extract.extract_image_text", must_not_call)
        ref = _text_ref()
        text, proposed = await registry.dispatch("read_attachments", {"attachment_ids": [ref]})
        assert proposed is None
        data = json.loads(text)
        assert data["ok"] is True
        result = data["results"][0]
        assert result["ok"] is True
        assert result["completeness"] == "complete"
        assert result["text"] == "Date,Amount\n2026-07-01,-12.50"
        assert result["reader_model_ref"] == ""

    async def test_an_image_attachment_is_read_by_the_configured_reader(self, monkeypatch):
        seen = {}

        async def fake_extract(images, *, model):
            seen["model"] = model
            from backend.services.image_extract import ExtractOutcome

            return ExtractOutcome(text="RECEIPT $12.40", pages_total=1, pages_read=1)

        monkeypatch.setattr("backend.services.image_extract.extract_image_text", fake_extract)
        monkeypatch.setattr(
            "backend.services.vision_capability.resolve_extractor_ref",
            lambda *a, **k: "anthropic:claude-haiku-4-5",
        )
        ref = _image_ref()
        text, proposed = await registry.dispatch("read_attachments", {"attachment_ids": [ref]})
        assert proposed is None
        result = json.loads(text)["results"][0]
        assert result["ok"] is True
        assert result["completeness"] == "complete"
        assert "RECEIPT $12.40" in result["text"]
        assert seen["model"] == "anthropic:claude-haiku-4-5"

    async def test_no_reader_configured_is_an_honest_per_item_error(self, monkeypatch):
        monkeypatch.setattr(
            "backend.services.vision_capability.resolve_extractor_ref", lambda *a, **k: ""
        )
        ref = _image_ref()
        text, proposed = await registry.dispatch("read_attachments", {"attachment_ids": [ref]})
        assert proposed is None
        result = json.loads(text)["results"][0]
        assert result["ok"] is False
        assert "chat_vision" in result["error"]

    async def test_a_missing_id_is_reported_not_silently_skipped(self):
        text, proposed = await registry.dispatch(
            "read_attachments", {"attachment_ids": ["att_does_not_exist"]}
        )
        assert proposed is None
        result = json.loads(text)["results"][0]
        assert result["ok"] is False
        assert "not found" in result["error"]

    async def test_mixed_batch_reports_each_id_independently(self, monkeypatch):
        async def must_not_call(*a, **k):
            raise AssertionError

        monkeypatch.setattr("backend.services.image_extract.extract_image_text", must_not_call)
        good = _text_ref()
        text, proposed = await registry.dispatch(
            "read_attachments", {"attachment_ids": [good, "att_missing"]}
        )
        assert proposed is None
        data = json.loads(text)
        assert data["count"] == 2
        by_id = {r["attachment_id"]: r for r in data["results"]}
        assert by_id[good]["ok"] is True
        assert by_id["att_missing"]["ok"] is False

    async def test_too_many_ids_is_refused_not_silently_truncated(self):
        ids = [f"att_{i}" for i in range(11)]
        text, proposed = await registry.dispatch("read_attachments", {"attachment_ids": ids})
        assert proposed is None
        data = json.loads(text)
        assert data["ok"] is False
        assert "10" in data["error"]

    async def test_empty_attachment_ids_is_a_validation_error(self):
        text, proposed = await registry.dispatch("read_attachments", {"attachment_ids": []})
        assert proposed is None
        assert "error" in json.loads(text)

    async def test_read_attachments_is_read_only(self):
        from tests.test_tool_domains import READ_ONLY_TOOLS

        assert "read_attachments" in READ_ONLY_TOOLS
