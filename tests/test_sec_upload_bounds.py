"""M5 / L3 — uploads are read with a cap, and a scanned PDF can't be rasterized into a bomb."""

from __future__ import annotations

import fitz
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.datastructures import UploadFile

from backend.api import import_api, receipts, upload
from backend.services import attachment_parse


def _blank_pdf(pages: int, width: float = 595, height: float = 842) -> bytes:
    doc = fitz.open()
    for _ in range(pages):
        doc.new_page(width=width, height=height)
    data = doc.tobytes()
    doc.close()
    return data


def test_huge_page_pdf_is_refused_before_any_pixmap(monkeypatch):
    def _no_pixmap(*a, **k):
        raise AssertionError("get_pixmap must not run")

    monkeypatch.setattr(fitz.Page, "get_pixmap", _no_pixmap)
    with pytest.raises(ValueError, match="larger than a normal sheet"):
        attachment_parse.parse_upload("bomb.pdf", "application/pdf", _blank_pdf(1, 14400, 14400))


def test_too_many_scanned_pages_is_refused(monkeypatch):
    monkeypatch.setattr(fitz.Page, "get_pixmap", lambda *a, **k: pytest.fail("rendered"))
    with pytest.raises(ValueError, match="pages"):
        attachment_parse.parse_upload("long.pdf", "application/pdf", _blank_pdf(31))


def test_an_ordinary_scanned_pdf_still_renders():
    entry = attachment_parse.parse_upload("scan.pdf", "application/pdf", _blank_pdf(2))
    assert entry["kind"] == "pdf_pages" and len(entry["images"]) == 2


@pytest.mark.parametrize(
    "module,path,prefix",
    [
        (upload, "/api/upload", None),
        (receipts, "/api/transactions/t1/receipts/upload", None),
    ],
)
def test_upload_reads_are_capped(monkeypatch, module, path, prefix):
    seen: list = []
    real_read = UploadFile.read

    async def spy(self, size=-1):
        seen.append(size)
        return await real_read(self, size)

    monkeypatch.setattr(UploadFile, "read", spy)
    monkeypatch.setattr(module, "_MAX_UPLOAD_BYTES", 10)
    app = FastAPI()
    app.include_router(module.router)
    res = TestClient(app).post(path, files={"file": ("a.csv", b"x" * 100, "text/csv")})
    assert seen and seen[0] == 11
    assert res.status_code == 413


def test_import_read_is_capped(monkeypatch):
    import asyncio
    import io

    from fastapi import HTTPException

    seen: list = []
    real_read = UploadFile.read

    async def spy(self, size=-1):
        seen.append(size)
        return await real_read(self, size)

    monkeypatch.setattr(UploadFile, "read", spy)
    monkeypatch.setattr(import_api, "_MAX_UPLOAD_BYTES", 10)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(
            import_api._read_file(UploadFile(file=io.BytesIO(b"x" * 100), filename="a.csv"))
        )
    assert exc.value.status_code == 413 and seen == [11]
