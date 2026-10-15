"""API smoke tests for /api/upload and /api/uploads/{ref_id}/image (backend/api/upload.py) —
added by a security review that found (1) the endpoint had no upload size cap at all, unlike
every other upload surface in the app, and (2) the image-serve route echoed a caller-declared
media_type inline with no allowlist, letting a spoofed "image/svg+xml" upload render as a
scriptable document on the app origin (the same bug class just fixed on the sibling
GET /api/receipts/{id}/file route — see backend/api/_blob_response.py).
"""

from __future__ import annotations

import io

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api.upload import _MAX_UPLOAD_BYTES
from backend.api.upload import router as upload_router
from tests._pdf import blank_pdf


@pytest.fixture(autouse=True)
def _isolated_dirs(tmp_path, monkeypatch, db_isolated):
    import backend.db.attachment_store as att_mod
    from backend.services import attachments as attachments_svc

    att_dir = tmp_path / "attachments"
    att_dir.mkdir()
    monkeypatch.setattr(att_mod, "ATTACHMENTS_DIR", att_dir)
    attachments_svc._cache_drop()
    yield
    attachments_svc._cache_drop()


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(upload_router)
    return TestClient(app)


def test_upload_rejects_oversize_file(client):
    oversize = io.BytesIO(b"x" * (_MAX_UPLOAD_BYTES + 1))
    r = client.post("/api/upload", files={"file": ("big.csv", oversize, "text/csv")})
    assert r.status_code == 413


def test_upload_accepts_file_at_the_cap(client):
    at_cap = io.BytesIO(b"a,b\n1,2\n" + b" " * (_MAX_UPLOAD_BYTES - 8))
    r = client.post("/api/upload", files={"file": ("ok.csv", at_cap, "text/csv")})
    assert r.status_code == 200


def _post_pdf(client, content: bytes):
    return client.post(
        "/api/upload", files={"file": ("statement.pdf", io.BytesIO(content), "application/pdf")}
    )


def test_a_locked_or_damaged_pdf_is_a_readable_refusal(client):
    """Security review S5: pypdf raises its own errors — FileNotDecryptedError for a PDF that
    needs a password — never the ValueError every parse caller handles, so either was a 500.
    A PDF encrypted with an owner password only still opens, and still uploads."""
    locked = _post_pdf(client, blank_pdf(user_password="secret"))
    assert (locked.status_code, locked.json()["detail"]) == (
        422,
        "This PDF is password-protected — remove the password and send it again.",
    )
    damaged = _post_pdf(client, b"%PDF-1.4 not really")
    assert (damaged.status_code, damaged.json()["detail"]) == (
        422,
        "Could not read this PDF — it may be damaged.",
    )
    assert _post_pdf(client, blank_pdf(user_password="")).status_code == 200


def test_uploaded_svg_never_served_inline(client):
    """A real image/svg+xml upload passes parse_upload's `mime.startswith("image/")` gate —
    must still never be echoed back as a scriptable inline document."""
    svg = io.BytesIO(b"<svg onload='alert(1)'></svg>")
    up = client.post("/api/upload", files={"file": ("logo.svg", svg, "image/svg+xml")})
    assert up.status_code == 200
    ref_id = up.json()["ref_id"]

    r = client.get(f"/api/uploads/{ref_id}/image")
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/octet-stream"
    assert "attachment" in r.headers["content-disposition"]
    assert r.headers["x-content-type-options"] == "nosniff"


def test_uploaded_real_image_still_serves_inline(client):
    png_bytes = b"\x89PNG\r\n\x1a\nfake-but-fine-for-this-route"
    up = client.post(
        "/api/upload", files={"file": ("photo.png", io.BytesIO(png_bytes), "image/png")}
    )
    assert up.status_code == 200
    ref_id = up.json()["ref_id"]

    r = client.get(f"/api/uploads/{ref_id}/image")
    assert r.status_code == 200
    assert r.content == png_bytes
    assert r.headers["content-type"] == "image/png"
    assert "inline" in r.headers["content-disposition"]


# ── GET /api/uploads/{ref_id}/file — the generic download route (2026-08-21) ───────
#
# Closes the real UX gap: before this route existed, only an image attachment had its
# bytes reachable in a servable shape (GET .../image, above). See
# docs/adr/2026-08-21-unify-raw-file-storage-across-blob-stores.md.


def test_get_file_serves_a_non_image_attachment(client):
    csv_bytes = b"Date,Amount\n2026-07-01,-12.50"
    up = client.post("/api/upload", files={"file": ("bank.csv", io.BytesIO(csv_bytes), "text/csv")})
    assert up.status_code == 200
    ref_id = up.json()["ref_id"]

    r = client.get(f"/api/uploads/{ref_id}/file")
    assert r.status_code == 200
    assert r.content == csv_bytes
    # text/csv isn't in the inline-safe allowlist (INLINE_SAFE_MEDIA_TYPES) — forced to
    # download, same as the sibling receipt/statement routes; the bytes are unchanged.
    assert r.headers["content-type"] == "application/octet-stream"
    assert "attachment" in r.headers["content-disposition"]


def test_get_file_also_serves_an_image_attachment(client):
    png_bytes = b"\x89PNG\r\n\x1a\nfake-but-fine-for-this-route"
    up = client.post(
        "/api/upload", files={"file": ("photo.png", io.BytesIO(png_bytes), "image/png")}
    )
    ref_id = up.json()["ref_id"]

    r = client.get(f"/api/uploads/{ref_id}/file")
    assert r.status_code == 200
    assert r.content == png_bytes
    assert r.headers["content-type"] == "image/png"


def test_get_file_404_unknown_ref(client):
    r = client.get("/api/uploads/att_does_not_exist/file")
    assert r.status_code == 404


def test_get_file_404_after_delete(client):
    up = client.post("/api/upload", files={"file": ("bank.csv", io.BytesIO(b"x"), "text/csv")})
    ref_id = up.json()["ref_id"]
    assert client.delete(f"/api/uploads/{ref_id}").status_code == 200

    r = client.get(f"/api/uploads/{ref_id}/file")
    assert r.status_code == 404


def test_get_file_spoofed_media_type_never_served_inline(client):
    """Same regression class as GET /api/receipts/{id}/file and GET .../image: a client
    that declares Content-Type "text/html" but names the file "*.csv" still hits
    parse_upload's filename-OR-mime CSV branch, keeping the spoofed media_type on the row.
    Must never be echoed back as an inline, scriptable response on this origin."""
    up = client.post(
        "/api/upload",
        files={
            "file": (
                "statement.csv",
                io.BytesIO(b"<script>alert(document.domain)</script>"),
                "text/html",
            )
        },
    )
    assert up.status_code == 200
    ref_id = up.json()["ref_id"]

    r = client.get(f"/api/uploads/{ref_id}/file")
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/octet-stream"
    assert "attachment" in r.headers["content-disposition"]
    assert "inline" not in r.headers["content-disposition"]


def test_get_file_svg_never_served_inline(client):
    svg = io.BytesIO(b"<svg onload='alert(1)'></svg>")
    up = client.post("/api/upload", files={"file": ("logo.svg", svg, "image/svg+xml")})
    ref_id = up.json()["ref_id"]

    r = client.get(f"/api/uploads/{ref_id}/file")
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/octet-stream"
    assert "attachment" in r.headers["content-disposition"]


def test_get_file_404_missing_blob(client):
    import backend.db.attachment_store as att_mod

    up = client.post("/api/upload", files={"file": ("bank.csv", io.BytesIO(b"x"), "text/csv")})
    ref_id = up.json()["ref_id"]
    att_mod.AttachmentStore().blob_path(ref_id).unlink()

    r = client.get(f"/api/uploads/{ref_id}/file")
    assert r.status_code == 404
