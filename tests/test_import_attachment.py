"""Tests for the chat-attachment import path (broadened uploader + import_transactions).

End-to-end the way the chat flow works: a statement file is attached → ``/api/upload``
caches its RAW bytes + a detected ``format`` (``parse_upload`` + ``store_upload``) → Lucius
calls ``import_transactions(attachment_ref=…)`` which pulls those cached bytes via
``api.upload.get_upload_import_source`` and runs the SAME parser/preview/commit path.

Covers EVERY importable format through the attachment seam — CSV, OFX, QFX, QIF, MT940,
CAMT (XML), XLSX (binary workbook) and PDF (binary, LLM-parsed → STUBBED) — and proves the
core invariants: preview writes nothing, commit writes via the ledger choke point, and a
re-import dedups to zero (content-addressed external_ref). Plus the attachment-source
validation (multiple sources rejected, an image/expired ref is not importable).

Stores isolated to tmp files; the PDF LLM parse is stubbed; UPLOAD_CACHE is cleared per
test so there's no cross-test bleed."""

from __future__ import annotations

import asyncio
import datetime
import io
import json

import pytest

from backend.ai.tools import execute_tool
from backend.api import upload as upload_mod


@pytest.fixture(autouse=True)
def isolate_stores(tmp_path, monkeypatch, fx_rates):
    from backend.db.account_store import AccountStore
    from backend.db.transaction_store import TransactionStore
    from backend.services import ledger as ledger_service

    accounts = AccountStore()
    txns = TransactionStore()
    monkeypatch.setattr(ledger_service, "accounts", accounts)
    monkeypatch.setattr(ledger_service, "transactions", txns)
    return accounts, txns


@pytest.fixture(autouse=True)
def clear_cache(tmp_path, monkeypatch, db_isolated):
    """Isolate the persistent attachment store (replaces the old UPLOAD_CACHE.clear()) —
    metadata via db_isolated (DB-backed, plan 4e task 1), the blob dir still a real
    tmp_path directory (ATTACHMENTS_DIR stays a Tier-4 file dir)."""
    import backend.db.attachment_store as as_mod
    from backend.services import attachments as attachments_svc

    up_dir = tmp_path / "attachments-iso"
    up_dir.mkdir(exist_ok=True)
    monkeypatch.setattr(as_mod, "ATTACHMENTS_DIR", up_dir)
    attachments_svc._cache_drop()
    yield
    attachments_svc._cache_drop()


@pytest.fixture
def checking(isolate_stores):
    accounts, _ = isolate_stores
    return accounts.create({"name": "Checking", "kind": "asset", "type": "cash", "currency": "USD"})


def _call(**kwargs) -> dict:
    text, proposed = asyncio.run(execute_tool("import_transactions", kwargs))
    assert proposed is None
    return json.loads(text)


def _cache(filename: str, mime: str, content: bytes) -> str:
    """Run the real uploader parse + store, return the attachment ref_id."""
    entry = upload_mod.parse_upload(filename, mime, content)
    return upload_mod.store_upload(entry)


# ── fixtures: one statement per format ───────────────────────────────────────────────

_CSV = b"Date,Memo,Amount\n2026-01-01,Lunch,-12.00\n2026-01-02,Refund,5.00\n"
_CSV_MAPPING = {"date": "Date", "description": "Memo", "amount": "Amount"}

_OFX = b"""OFXHEADER:100
<OFX>
<BANKMSGSRSV1><STMTTRNRS><STMTRS>
<BANKACCTFROM><ACCTID>ACC-1</ACCTID></BANKACCTFROM>
<BANKTRANLIST>
<STMTTRN><DTPOSTED>20260102<TRNAMT>-4.50<FITID>T1<NAME>Coffee</STMTTRN>
<STMTTRN><DTPOSTED>20260103<TRNAMT>2500.00<FITID>T2<NAME>Paycheck</STMTTRN>
</BANKTRANLIST>
</STMTRS></STMTTRNRS></BANKMSGSRSV1>
</OFX>
"""

_QFX = b"""OFXHEADER:100
DATA:OFXSGML
VERSION:102
<OFX>
<SIGNONMSGSRSV1><SONRS><INTU.BID>10898</SONRS></SIGNONMSGSRSV1>
<BANKMSGSRSV1><STMTTRNRS><STMTRS>
<BANKACCTFROM><ACCTID>QACC</ACCTID></BANKACCTFROM>
<BANKTRANLIST>
<STMTTRN><DTPOSTED>20260302<TRNAMT>-72.40<FITID>Q1<NAME>Grocery</STMTTRN>
</BANKTRANLIST>
</STMTRS></STMTTRNRS></BANKMSGSRSV1>
<INTU.USERID>jdoe
</OFX>
"""

_QIF = b"""!Type:Bank
D2026-01-02
T-4.50
PCoffee
^
D2026-01-03
T2500.00
PPaycheck
^
"""

_MT940 = b""":20:STMT-1
:25:ACC-1
:60F:C260101USD0,00
:61:2601020102D4,50NTRFREF1//X
:86:Coffee
:61:2601030103C2500,00NTRFREF2//Y
:86:Paycheck
:62F:C260103USD2495,50
"""

_CAMT = b"""<?xml version="1.0" encoding="UTF-8"?>
<Document xmlns="urn:iso:std:iso:20022:tech:xsd:camt.053.001.02">
  <BkToCstmrStmt>
    <Stmt>
      <Id>STMT-001</Id>
      <Acct><Id><IBAN>NL12ABNA0123456789</IBAN></Id></Acct>
      <Ntry>
        <Amt Ccy="USD">4.50</Amt>
        <CdtDbtInd>DBIT</CdtDbtInd>
        <Sts>BOOK</Sts>
        <BookgDt><Dt>2026-01-02</Dt></BookgDt>
        <NtryDtls><TxDtls>
          <Refs><AcctSvcrRef>REF-COFFEE-1</AcctSvcrRef></Refs>
          <RmtInf><Ustrd>Coffee Shop downtown</Ustrd></RmtInf>
        </TxDtls></NtryDtls>
      </Ntry>
      <Ntry>
        <Amt Ccy="USD">2500.00</Amt>
        <CdtDbtInd>CRDT</CdtDbtInd>
        <Sts>BOOK</Sts>
        <BookgDt><Dt>2026-01-03</Dt></BookgDt>
        <NtryDtls><TxDtls>
          <Refs><EndToEndId>E2E-SALARY-1</EndToEndId></Refs>
          <RmtInf><Ustrd>Salary payment ACME</Ustrd></RmtInf>
        </TxDtls></NtryDtls>
      </Ntry>
    </Stmt>
  </BkToCstmrStmt>
</Document>
"""


def _make_xlsx() -> bytes:
    import openpyxl

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["Date", "Memo", "Amount"])
    ws.append([datetime.datetime(2026, 1, 2), "Coffee Shop", -4.50])
    ws.append([datetime.datetime(2026, 1, 3), "Paycheck", 2500.00])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _make_pdf() -> bytes:
    import fitz  # pymupdf

    doc = fitz.open()
    page = doc.new_page()
    page.insert_text(
        (72, 72),
        "2026-01-02  Coffee Shop downtown  -4.50\n2026-01-03  Salary payment ACME  +2500.00",
    )
    data = doc.tobytes()
    doc.close()
    return data


_PDF_ROWS = [
    {
        "date": "2026-01-02",
        "amount": 4.50,
        "direction": "OUT",
        "description": "Coffee",
        "confidence": 0.95,
    },
    {
        "date": "2026-01-03",
        "amount": 2500.00,
        "direction": "IN",
        "description": "Salary",
        "confidence": 0.95,
    },
]


def _stub_pdf_llm(monkeypatch):
    from backend.core import llm_seam

    async def _complete(prompt, **kwargs):
        return json.dumps(_PDF_ROWS)

    monkeypatch.setattr(llm_seam, "_complete", _complete)
    monkeypatch.setattr(llm_seam, "_parse_json", None)


# ── each format: cache → preview → commit → re-import dedups to zero ─────────────────


def _roundtrip(checking, txns, ref: str, *, expected_fmt: str, expected: int) -> None:
    """The shared assertion: preview is pure → commit writes N → re-import dedups to 0."""
    prev = _call(attachment_ref=ref, account_id=checking["id"])
    assert prev["ok"] is True, prev
    assert prev["format"] == expected_fmt
    assert prev["committed"] is False
    assert prev["result"]["importable"] == expected
    assert txns.all() == []  # PROOF: preview writes nothing

    done = _call(attachment_ref=ref, account_id=checking["id"], commit=True)
    assert done["committed"] is True
    assert done["result"]["created"] == expected
    assert len(txns.all()) == expected

    again = _call(attachment_ref=ref, account_id=checking["id"], commit=True)
    assert again["result"]["created"] == 0
    assert again["result"]["duplicates"] == expected
    assert len(txns.all()) == expected  # no double-post


def test_attachment_csv(checking, isolate_stores):
    _, txns = isolate_stores
    ref = _cache("statement.csv", "text/csv", _CSV)
    # CSV needs a column mapping; pass it like the UI does.
    prev = _call(attachment_ref=ref, mapping=_CSV_MAPPING, account_id=checking["id"])
    assert prev["format"] == "csv"
    assert prev["result"]["importable"] == 2
    assert txns.all() == []
    done = _call(attachment_ref=ref, mapping=_CSV_MAPPING, account_id=checking["id"], commit=True)
    assert done["result"]["created"] == 2
    again = _call(attachment_ref=ref, mapping=_CSV_MAPPING, account_id=checking["id"], commit=True)
    assert again["result"]["created"] == 0
    assert len(txns.all()) == 2


def test_attachment_ofx(checking, isolate_stores):
    _, txns = isolate_stores
    ref = _cache("statement.ofx", "application/x-ofx", _OFX)
    _roundtrip(checking, txns, ref, expected_fmt="ofx", expected=2)


def test_attachment_qfx(checking, isolate_stores):
    _, txns = isolate_stores
    ref = _cache("statement.qfx", "application/octet-stream", _QFX)
    _roundtrip(checking, txns, ref, expected_fmt="ofx", expected=1)  # qfx folds to ofx


def test_attachment_qif(checking, isolate_stores):
    _, txns = isolate_stores
    ref = _cache("statement.qif", "application/octet-stream", _QIF)
    _roundtrip(checking, txns, ref, expected_fmt="qif", expected=2)


def test_attachment_mt940(checking, isolate_stores):
    _, txns = isolate_stores
    ref = _cache("statement.mt940", "application/octet-stream", _MT940)
    _roundtrip(checking, txns, ref, expected_fmt="mt940", expected=2)


def test_attachment_camt(checking, isolate_stores):
    _, txns = isolate_stores
    ref = _cache("statement.camt", "application/xml", _CAMT)
    _roundtrip(checking, txns, ref, expected_fmt="camt", expected=2)


def test_attachment_xlsx(checking, isolate_stores):
    _, txns = isolate_stores
    xlsx_mime = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    ref = _cache("statement.xlsx", xlsx_mime, _make_xlsx())
    # XLSX is a sheet → needs a mapping (same as the file/URL path).
    prev = _call(attachment_ref=ref, mapping=_CSV_MAPPING, account_id=checking["id"])
    assert prev["format"] == "xlsx"
    assert prev["result"]["importable"] == 2
    assert txns.all() == []
    done = _call(attachment_ref=ref, mapping=_CSV_MAPPING, account_id=checking["id"], commit=True)
    assert done["result"]["created"] == 2
    again = _call(attachment_ref=ref, mapping=_CSV_MAPPING, account_id=checking["id"], commit=True)
    assert again["result"]["created"] == 0
    assert len(txns.all()) == 2


def test_attachment_pdf(checking, isolate_stores, monkeypatch):
    _, txns = isolate_stores
    _stub_pdf_llm(monkeypatch)
    ref = _cache("statement.pdf", "application/pdf", _make_pdf())
    _roundtrip(checking, txns, ref, expected_fmt="pdf", expected=2)


# ── the uploader stashes bytes + format on importable entries ───────────────────────


def test_uploader_stashes_format_and_bytes():
    entry = upload_mod.parse_upload("statement.ofx", "application/x-ofx", _OFX)
    assert entry["format"] == "ofx"
    assert entry["content"] == _OFX
    src = upload_mod.get_upload_import_source(upload_mod.store_upload(entry))
    assert src == (_OFX, "ofx")


def test_uploader_image_is_not_importable():
    # A 1x1 PNG — an image is cached for vision, but is NOT an importable statement.
    png = (
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
        b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00\x01"
        b"\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
    )
    ref = upload_mod.store_upload(upload_mod.parse_upload("shot.png", "image/png", png))
    assert upload_mod.get_upload_import_source(ref) is None


# ── attachment-source validation ────────────────────────────────────────────────────


def test_unknown_attachment_ref(checking):
    res = _call(attachment_ref="does-not-exist", account_id=checking["id"])
    assert res["ok"] is False
    assert "attachment" in res["error"]


def test_image_attachment_ref_not_importable(checking):
    png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
    ref = upload_mod.store_upload(upload_mod.parse_upload("x.png", "image/png", png))
    res = _call(attachment_ref=ref, account_id=checking["id"])
    assert res["ok"] is False


def test_multiple_sources_rejected(checking):
    ref = _cache("statement.ofx", "application/x-ofx", _OFX)
    res = _call(attachment_ref=ref, content=_OFX.decode(), account_id=checking["id"])
    assert res["ok"] is False
    assert "exactly ONE" in res["error"]
