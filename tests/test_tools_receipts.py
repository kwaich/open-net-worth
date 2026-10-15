"""AI tool surface for receipts (cross-channel-attachments RFC item 25):
``attach_receipt``/``unlink_receipt``/``delete_receipt``, plus ``get_transactions``'
single-transaction detail branch presenting ``receipts`` (item 26)."""

from __future__ import annotations

import asyncio
import json

import pytest

from backend.services import ledger as svc


def _call_tool(name: str, **kwargs) -> dict:
    from backend.ai.tools import execute_tool

    text, _ = asyncio.run(execute_tool(name, kwargs))
    return json.loads(text)


@pytest.fixture(autouse=True)
def _isolated_dirs(tmp_path, monkeypatch, db_isolated):
    import backend.db.attachment_store as att_mod
    import backend.db.receipt_store as rcpt_mod
    from backend.services import attachments as attachments_svc

    att_dir = tmp_path / "attachments"
    att_dir.mkdir()
    monkeypatch.setattr(att_mod, "ATTACHMENTS_DIR", att_dir)
    rcpt_dir = tmp_path / "receipts"
    rcpt_dir.mkdir()
    monkeypatch.setattr(rcpt_mod, "RECEIPTS_DIR", rcpt_dir)
    attachments_svc._cache_drop()
    yield
    attachments_svc._cache_drop()


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setattr(svc, "base_currency", lambda: "SGD")
    svc.accounts.insert({"id": "acc_chk", "name": "Checking", "kind": "asset", "currency": "SGD"})
    txn = svc.create_transaction(
        {
            "kind": "expense",
            "date": "2026-08-01",
            "description": "Dinner",
            "category": None,
            "from_account_id": "acc_chk",
            "from_amount": 25.0,
            "from_currency": "SGD",
        }
    )
    return {"txn_id": txn["id"]}


def _upload_attachment(content: bytes = b"photo-bytes") -> str:
    """Go through the REAL ingress path (parse_upload + persist_inbound_file), not a
    hand-built entry dict — parse_upload's image branch stores base64 ``data``, not raw
    ``content`` bytes, so a hand-built ``content``-bearing entry is a shape it never actually
    produces and can mask a real promotion bug (see test_receipts_service.py's own fixture
    fix for the same issue)."""
    from backend.services.attachment_ingress import persist_inbound_file

    result = persist_inbound_file("receipt.jpg", "image/jpeg", content, source_channel="chat:web")
    return result["attachment_id"]


def test_attach_receipt_from_attachment(env):
    att_id = _upload_attachment()
    result = _call_tool("attach_receipt", transaction_id=env["txn_id"], attachment_id=att_id)
    assert result["ok"] is True
    assert result["receipt"]["source_attachment_id"] == att_id
    assert result["link"]["transaction_id"] == env["txn_id"]


def test_attach_receipt_from_a_scanned_pdf_attachment(env):
    """Lucius's attach_receipt tool shares services.receipts.apply_receipt_change with the
    drawer's REST route — the fix that lets a scanned (text-less) PDF promote with its real
    original bytes, instead of a lossy first-page substitute or a refusal, applies here too
    with no tool-layer change needed. A user sending Lucius a photographed multi-page
    receipt as a PDF must get the same correct behavior as the manual upload button."""
    import fitz

    from backend.services.attachment_ingress import persist_inbound_file

    doc = fitz.open()
    doc.new_page()
    scanned_pdf_bytes = doc.tobytes()
    doc.close()

    result = persist_inbound_file(
        "invoice.pdf", "application/pdf", scanned_pdf_bytes, source_channel="chat:web"
    )
    assert result["kind"] == "pdf_pages"

    out = _call_tool(
        "attach_receipt", transaction_id=env["txn_id"], attachment_id=result["attachment_id"]
    )
    assert out["ok"] is True
    assert out["receipt"]["media_type"] == "application/pdf"

    from backend.services.receipts import receipt_store

    assert receipt_store.read_bytes(out["receipt"]["id"]) == scanned_pdf_bytes


def test_attach_receipt_requires_exactly_one_source(env):
    result = _call_tool("attach_receipt", transaction_id=env["txn_id"])
    assert result["ok"] is False
    assert "exactly one" in result["error"].lower()


def test_attach_receipt_existing_to_second_transaction(env):
    att_id = _upload_attachment()
    first = _call_tool("attach_receipt", transaction_id=env["txn_id"], attachment_id=att_id)
    receipt_id = first["receipt"]["id"]

    txn2 = svc.create_transaction(
        {
            "kind": "expense",
            "date": "2026-08-02",
            "description": "Dinner split",
            "category": None,
            "from_account_id": "acc_chk",
            "from_amount": 5.0,
            "from_currency": "SGD",
        }
    )
    second = _call_tool("attach_receipt", transaction_id=txn2["id"], receipt_id=receipt_id)
    assert second["ok"] is True
    assert second["link"]["receipt_id"] == receipt_id


def test_attach_receipt_unknown_attachment_reports_error(env):
    result = _call_tool(
        "attach_receipt", transaction_id=env["txn_id"], attachment_id="att_does_not_exist"
    )
    assert result["ok"] is False
    assert "not found" in result["error"].lower()


def test_unlink_receipt_keeps_receipt(env):
    att_id = _upload_attachment()
    attached = _call_tool("attach_receipt", transaction_id=env["txn_id"], attachment_id=att_id)
    receipt_id = attached["receipt"]["id"]

    result = _call_tool("unlink_receipt", transaction_id=env["txn_id"], receipt_id=receipt_id)
    assert result["ok"] is True

    from backend.services.receipts import receipt_store

    assert receipt_store.find_by_id(receipt_id) is not None


def test_delete_receipt_refuses_then_succeeds_with_force(env):
    att_id = _upload_attachment()
    attached = _call_tool("attach_receipt", transaction_id=env["txn_id"], attachment_id=att_id)
    receipt_id = attached["receipt"]["id"]

    refused = _call_tool("delete_receipt", receipt_id=receipt_id)
    assert refused["ok"] is False
    assert env["txn_id"] in refused["error"]

    forced = _call_tool("delete_receipt", receipt_id=receipt_id, force=True)
    assert forced["ok"] is True
    assert forced["deleted"] == receipt_id


def test_get_transactions_detail_includes_receipts(env):
    att_id = _upload_attachment()
    _call_tool("attach_receipt", transaction_id=env["txn_id"], attachment_id=att_id)

    result = _call_tool("get_transactions", transaction_id=env["txn_id"])
    receipts = result["transaction"]["receipts"]
    assert len(receipts) == 1
    assert receipts[0]["filename"] == "receipt.jpg"


def _promote_receipt(content: bytes, filename: str, media_type: str) -> str:
    """Save bytes directly as a receipt (the drawer's own upload path,
    ``services.receipts.apply_receipt_change({"type": "upload", ...})``) — no attachment
    or transaction needed for a pure read test."""
    from backend.services.receipts import apply_receipt_change

    result = apply_receipt_change(
        {"type": "upload", "content": content, "filename": filename, "media_type": media_type}
    )
    return result["receipt"]["id"]


def _promote_image_receipt() -> str:
    att_id = _upload_attachment()
    out = _call_tool("attach_receipt", attachment_id=att_id)
    return out["receipt"]["id"]


class TestReadReceipts:
    """read_receipts — the on-demand vision-read twin of read_attachments (see
    ai/tools/_vision_read.py), pointed at ReceiptStore. Mirrors
    tests/test_tools_uploads.py::TestReadAttachments's coverage shape."""

    def test_an_image_receipt_is_read_by_the_configured_reader(self, monkeypatch):
        async def fake_extract(images, *, model):
            from backend.services.image_extract import ExtractOutcome

            return ExtractOutcome(text="COFFEE $4.50", pages_total=1, pages_read=1)

        monkeypatch.setattr("backend.services.image_extract.extract_image_text", fake_extract)
        monkeypatch.setattr(
            "backend.services.vision_capability.resolve_extractor_ref",
            lambda *a, **k: "anthropic:claude-haiku-4-5",
        )
        receipt_id = _promote_image_receipt()

        result = _call_tool("read_receipts", receipt_ids=[receipt_id])["results"][0]
        assert result["ok"] is True
        assert result["completeness"] == "complete"
        assert "COFFEE $4.50" in result["text"]

    def test_a_digital_pdf_receipt_with_text_needs_no_reader_call(self, monkeypatch):
        async def must_not_call(*a, **k):
            raise AssertionError("a text-bearing PDF must never invoke the reader")

        monkeypatch.setattr("backend.services.image_extract.extract_image_text", must_not_call)

        import fitz

        doc = fitz.open()
        page = doc.new_page()
        page.insert_text((72, 72), "RECEIPT TOTAL $9.99")
        text_pdf_bytes = doc.tobytes()
        doc.close()
        receipt_id = _promote_receipt(text_pdf_bytes, "receipt.pdf", "application/pdf")

        result = _call_tool("read_receipts", receipt_ids=[receipt_id])["results"][0]
        assert result["ok"] is True
        assert result["reader_model_ref"] == ""
        assert "$9.99" in result["text"] or "RECEIPT TOTAL" in result["text"]

    def test_a_scanned_pdf_receipt_is_read_by_the_configured_reader(self, monkeypatch):
        import fitz

        doc = fitz.open()
        doc.new_page()
        scanned_pdf_bytes = doc.tobytes()
        doc.close()
        receipt_id = _promote_receipt(scanned_pdf_bytes, "scan.pdf", "application/pdf")

        async def fake_extract(images, *, model):
            from backend.services.image_extract import ExtractOutcome

            return ExtractOutcome(text="SCANNED $1.00", pages_total=1, pages_read=1)

        monkeypatch.setattr("backend.services.image_extract.extract_image_text", fake_extract)
        monkeypatch.setattr(
            "backend.services.vision_capability.resolve_extractor_ref",
            lambda *a, **k: "anthropic:claude-haiku-4-5",
        )

        result = _call_tool("read_receipts", receipt_ids=[receipt_id])["results"][0]
        assert result["ok"] is True
        assert "SCANNED $1.00" in result["text"]

    def test_no_reader_configured_is_an_honest_per_item_error(self, monkeypatch):
        monkeypatch.setattr(
            "backend.services.vision_capability.resolve_extractor_ref", lambda *a, **k: ""
        )
        receipt_id = _promote_image_receipt()

        result = _call_tool("read_receipts", receipt_ids=[receipt_id])["results"][0]
        assert result["ok"] is False
        assert "chat_vision" in result["error"]

    def test_a_missing_id_is_reported_not_silently_skipped(self):
        result = _call_tool("read_receipts", receipt_ids=["rcpt_does_not_exist"])["results"][0]
        assert result["ok"] is False
        assert "not found" in result["error"]

    def test_mixed_batch_reports_each_id_independently(self, monkeypatch):
        async def fake_extract(images, *, model):
            from backend.services.image_extract import ExtractOutcome

            return ExtractOutcome(text="OK", pages_total=1, pages_read=1)

        monkeypatch.setattr("backend.services.image_extract.extract_image_text", fake_extract)
        monkeypatch.setattr(
            "backend.services.vision_capability.resolve_extractor_ref",
            lambda *a, **k: "anthropic:claude-haiku-4-5",
        )
        good = _promote_image_receipt()

        data = _call_tool("read_receipts", receipt_ids=[good, "rcpt_missing"])
        assert data["count"] == 2
        by_id = {r["receipt_id"]: r for r in data["results"]}
        assert by_id[good]["ok"] is True
        assert by_id["rcpt_missing"]["ok"] is False

    def test_too_many_ids_is_refused_not_silently_truncated(self):
        ids = [f"rcpt_{i}" for i in range(11)]
        data = _call_tool("read_receipts", receipt_ids=ids)
        assert data["ok"] is False
        assert "10" in data["error"]

    def test_empty_receipt_ids_is_a_validation_error(self):
        data = _call_tool("read_receipts", receipt_ids=[])
        assert "error" in data

    def test_read_receipts_is_read_only(self):
        from tests.test_tool_domains import READ_ONLY_TOOLS

        assert "read_receipts" in READ_ONLY_TOOLS
