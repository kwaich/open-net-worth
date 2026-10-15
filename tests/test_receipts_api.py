"""
API smoke tests for the transaction-receipts REST surface (backend/api/receipts.py) —
the drawer's Receipts section attach/list/unlink/file-serve routes.

Offline + isolated: a minimal FastAPI app mounts only the receipts router. db_isolated gives
each test its own fresh, migrated database; ATTACHMENTS_DIR/RECEIPTS_DIR are redirected into
tmp_path, mirroring tests/test_tools_receipts.py's fixtures (the AI-tool surface over the
exact same service choke this router calls).

Run:
    .venv/bin/pytest tests/test_receipts_api.py -v
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api.receipts import router as receipts_router
from backend.services import ledger as svc


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
def client():
    app = FastAPI()
    app.include_router(receipts_router)
    return TestClient(app)


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


def _upload_attachment(content: bytes = b"photo-bytes", media_type: str = "image/jpeg") -> str:
    """Go through the REAL ingress path (parse_upload + persist_inbound_file), not a
    hand-built entry dict — a hand-built ``{"kind": "image", "content": ...}`` entry is a
    shape parse_upload never actually produces for an image (it stores base64 ``data``, not
    raw ``content`` bytes), and a fixture that skips the real parser can drift from what
    promotion actually receives, silently hiding a real 422 (see
    services.receipts._receipt_bytes_from_entry's regression test for the bug this masked)."""
    from backend.services.attachment_ingress import persist_inbound_file

    result = persist_inbound_file("receipt.jpg", media_type, content, source_channel="chat:web")
    return result["attachment_id"]


# ── GET /api/transactions/{txn_id}/receipts ───────────────────────────────────────


def test_list_receipts_empty(client, env):
    r = client.get(f"/api/transactions/{env['txn_id']}/receipts")
    assert r.status_code == 200
    assert r.json() == {"receipts": []}


def test_list_receipts_404_missing_txn(client, env):
    r = client.get("/api/transactions/txn_does_not_exist/receipts")
    assert r.status_code == 404


def test_list_receipts_after_attach(client, env):
    att_id = _upload_attachment()
    client.post(f"/api/transactions/{env['txn_id']}/receipts", json={"attachment_id": att_id})
    r = client.get(f"/api/transactions/{env['txn_id']}/receipts")
    assert r.status_code == 200
    receipts = r.json()["receipts"]
    assert len(receipts) == 1
    assert receipts[0]["filename"] == "receipt.jpg"
    assert receipts[0]["media_type"] == "image/jpeg"
    assert receipts[0]["source_attachment_id"] == att_id


# ── POST /api/transactions/{txn_id}/receipts ──────────────────────────────────────


def test_attach_receipt_from_attachment(client, env):
    att_id = _upload_attachment()
    r = client.post(f"/api/transactions/{env['txn_id']}/receipts", json={"attachment_id": att_id})
    assert r.status_code == 200
    body = r.json()
    assert body["receipt"]["source_attachment_id"] == att_id
    assert body["link"]["transaction_id"] == env["txn_id"]


def test_attach_receipt_requires_exactly_one_source(client, env):
    r = client.post(f"/api/transactions/{env['txn_id']}/receipts", json={})
    assert r.status_code == 422

    att_id = _upload_attachment()
    r2 = client.post(
        f"/api/transactions/{env['txn_id']}/receipts",
        json={"attachment_id": att_id, "receipt_id": "rcpt_whatever"},
    )
    assert r2.status_code == 422


def test_attach_receipt_unknown_attachment_404(client, env):
    r = client.post(
        f"/api/transactions/{env['txn_id']}/receipts", json={"attachment_id": "att_missing"}
    )
    assert r.status_code == 404


def test_attach_receipt_missing_transaction_404(client, env):
    att_id = _upload_attachment()
    r = client.post("/api/transactions/txn_missing/receipts", json={"attachment_id": att_id})
    assert r.status_code == 404


def test_attach_existing_receipt_to_second_transaction(client, env):
    att_id = _upload_attachment()
    first = client.post(
        f"/api/transactions/{env['txn_id']}/receipts", json={"attachment_id": att_id}
    ).json()
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
    r = client.post(f"/api/transactions/{txn2['id']}/receipts", json={"receipt_id": receipt_id})
    assert r.status_code == 200
    assert r.json()["link"]["receipt_id"] == receipt_id
    assert "receipt" not in r.json()  # attach_existing returns only the link, mirroring the tool


# ── POST /api/transactions/{txn_id}/receipts/upload ────────────────────────────────


def test_upload_receipt_direct_saves_and_links_without_a_parser_detour(client, env):
    """The drawer's own upload button: a straight save, no /api/upload round-trip, no
    attachment_id at all — including for a scanned PDF, whose bytes an indirect
    attachment-parser detour used to lose entirely (see services/receipts.py)."""
    r = client.post(
        f"/api/transactions/{env['txn_id']}/receipts/upload",
        files={"file": ("receipt.jpg", b"raw-jpeg-bytes", "image/jpeg")},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["receipt"]["filename"] == "receipt.jpg"
    assert body["receipt"]["media_type"] == "image/jpeg"
    assert body["receipt"]["source_attachment_id"] is None
    assert body["link"]["transaction_id"] == env["txn_id"]

    receipt_id = body["receipt"]["id"]
    from backend.services.receipts import receipt_store

    assert receipt_store.read_bytes(receipt_id) == b"raw-jpeg-bytes"


def test_upload_receipt_direct_missing_transaction_404(client, env):
    r = client.post(
        "/api/transactions/txn_missing/receipts/upload",
        files={"file": ("receipt.jpg", b"bytes", "image/jpeg")},
    )
    assert r.status_code == 404


def test_upload_receipt_direct_rejects_oversize_file(client, env):
    from backend.api.upload import _MAX_UPLOAD_BYTES

    oversize = b"x" * (_MAX_UPLOAD_BYTES + 1)
    r = client.post(
        f"/api/transactions/{env['txn_id']}/receipts/upload",
        files={"file": ("big.jpg", oversize, "image/jpeg")},
    )
    assert r.status_code == 413


# ── DELETE /api/transactions/{txn_id}/receipts/{receipt_id} ───────────────────────


def test_unlink_receipt(client, env):
    att_id = _upload_attachment()
    attached = client.post(
        f"/api/transactions/{env['txn_id']}/receipts", json={"attachment_id": att_id}
    ).json()
    receipt_id = attached["receipt"]["id"]

    r = client.delete(f"/api/transactions/{env['txn_id']}/receipts/{receipt_id}")
    assert r.status_code == 200
    body = r.json()
    assert body["transaction_id"] == env["txn_id"]
    assert body["receipt_id"] == receipt_id

    # The receipt row itself survives — only the link is gone (it may support other txns).
    from backend.services.receipts import receipt_store

    assert receipt_store.find_by_id(receipt_id) is not None
    assert client.get(f"/api/transactions/{env['txn_id']}/receipts").json() == {"receipts": []}


def test_unlink_receipt_not_found(client, env):
    r = client.delete(f"/api/transactions/{env['txn_id']}/receipts/rcpt_missing")
    assert r.status_code == 404


# ── GET /api/receipts/{receipt_id}/file ────────────────────────────────────────────


def test_get_receipt_file(client, env):
    att_id = _upload_attachment(content=b"jpeg-bytes-here", media_type="image/jpeg")
    attached = client.post(
        f"/api/transactions/{env['txn_id']}/receipts", json={"attachment_id": att_id}
    ).json()
    receipt_id = attached["receipt"]["id"]

    r = client.get(f"/api/receipts/{receipt_id}/file")
    assert r.status_code == 200
    assert r.content == b"jpeg-bytes-here"
    assert r.headers["content-type"] == "image/jpeg"
    assert "inline" in r.headers["content-disposition"]
    assert r.headers["x-content-type-options"] == "nosniff"


def test_get_receipt_file_404_missing_receipt(client, env):
    r = client.get("/api/receipts/rcpt_missing/file")
    assert r.status_code == 404


def test_get_receipt_file_spoofed_media_type_never_served_inline(client, env):
    """Regression: a client that declares Content-Type "text/html" but names the file
    "*.csv" hits parse_upload's filename-OR-mime CSV branch regardless of the declared mime
    (attachment_parse.py: ``mime in (...) or filename.endswith(".csv")``), keeping the raw
    bytes AND the spoofed "text/html" media_type all the way onto the receipt row. That must
    never be echoed back as an inline, scriptable response on this origin — security review,
    HIGH. Forced to a download instead; the bytes are unchanged."""
    from backend.services.attachment_ingress import persist_inbound_file

    result = persist_inbound_file(
        "statement.csv",
        "text/html",
        b"<script>alert(document.domain)</script>",
        source_channel="chat:web",
    )
    attached = client.post(
        f"/api/transactions/{env['txn_id']}/receipts",
        json={"attachment_id": result["attachment_id"]},
    ).json()
    receipt_id = attached["receipt"]["id"]

    r = client.get(f"/api/receipts/{receipt_id}/file")
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/octet-stream"
    assert "attachment" in r.headers["content-disposition"]
    assert "inline" not in r.headers["content-disposition"]


def test_get_receipt_file_svg_never_served_inline(client, env):
    """Same regression, the other realistic vector: a real image/* upload whose actual
    payload is SVG (browsers execute <script> in an SVG navigated to directly, even though
    "image/svg+xml" passes a naive `startswith("image/")` check)."""
    att_id = _upload_attachment(
        content=b"<svg onload='alert(1)'></svg>", media_type="image/svg+xml"
    )
    attached = client.post(
        f"/api/transactions/{env['txn_id']}/receipts", json={"attachment_id": att_id}
    ).json()
    receipt_id = attached["receipt"]["id"]

    r = client.get(f"/api/receipts/{receipt_id}/file")
    assert r.headers["content-type"] == "application/octet-stream"
    assert "attachment" in r.headers["content-disposition"]


def test_get_receipt_file_non_latin1_filename_does_not_crash(client, env):
    """Regression: an unescaped filename interpolated straight into Content-Disposition
    raised UnicodeEncodeError for any non-latin-1 name (Starlette encodes header values
    latin-1) — security review, MEDIUM."""
    from backend.services.attachment_ingress import persist_inbound_file

    result = persist_inbound_file(
        "发票.csv", "text/csv", b"date,amount\n2026-08-01,25.00\n", source_channel="chat:web"
    )
    attached = client.post(
        f"/api/transactions/{env['txn_id']}/receipts",
        json={"attachment_id": result["attachment_id"]},
    ).json()
    receipt_id = attached["receipt"]["id"]

    r = client.get(f"/api/receipts/{receipt_id}/file")
    assert r.status_code == 200
    assert "filename*=UTF-8''" in r.headers["content-disposition"]


def test_get_receipt_file_404_missing_blob(client, env):
    import backend.db.receipt_store as rcpt_mod

    att_id = _upload_attachment()
    attached = client.post(
        f"/api/transactions/{env['txn_id']}/receipts", json={"attachment_id": att_id}
    ).json()
    receipt_id = attached["receipt"]["id"]

    store = rcpt_mod.ReceiptStore()
    store.blob_path(receipt_id).unlink()

    r = client.get(f"/api/receipts/{receipt_id}/file")
    assert r.status_code == 404
