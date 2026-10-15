"""Unit tests for the OFX transaction import (services/import_ofx.py).

Covers: OFX 1.x (SGML) + 2.x (XML) parse, signed TRNAMT → income/expense, the FITID-based
external_ref dedup (re-import → ZERO duplicates), a malformed txn (skip), and the preview
writing NOTHING. Stores isolated to tmp files (mirrors test_import_csv_service)."""

from __future__ import annotations

import pytest

from backend.core.txn_sources import primary_ref, primary_source
from backend.services import import_ofx as svc


@pytest.fixture(autouse=True)
def isolate_stores(tmp_path, monkeypatch, fx_rates):
    from backend.db.account_store import AccountStore
    from backend.db.transaction_store import TransactionStore
    from backend.services import ledger as ledger_service

    accounts = AccountStore()
    accounts._path = tmp_path / "accounts.json"
    txns = TransactionStore()
    txns._path = tmp_path / "transactions.json"
    monkeypatch.setattr(ledger_service, "accounts", accounts)
    monkeypatch.setattr(ledger_service, "transactions", txns)
    return accounts, txns


@pytest.fixture
def checking(isolate_stores):
    accounts, _ = isolate_stores
    return accounts.create({"name": "Checking", "kind": "asset", "type": "cash", "currency": "USD"})


# ── OFX 1.x (SGML, unclosed value tags) ────────────────────────────────────────────

_OFX_1X = b"""OFXHEADER:100
DATA:OFXSGML
VERSION:102
SECURITY:NONE
ENCODING:USASCII

<OFX>
<BANKMSGSRSV1>
<STMTTRNRS>
<STMTRS>
<CURDEF>USD
<BANKACCTFROM>
<ACCTID>000123456789
<ACCTTYPE>CHECKING
</BANKACCTFROM>
<BANKTRANLIST>
<STMTTRN>
<TRNTYPE>DEBIT
<DTPOSTED>20260102120000
<TRNAMT>-4.50
<FITID>TXN-1001
<NAME>Coffee Shop
</STMTTRN>
<STMTTRN>
<TRNTYPE>CREDIT
<DTPOSTED>20260103
<TRNAMT>2500.00
<FITID>TXN-1002
<NAME>Paycheck
</STMTTRN>
</BANKTRANLIST>
</STMTRS>
</STMTTRNRS>
</BANKMSGSRSV1>
</OFX>
"""

# ── OFX 2.x (well-formed XML, closed tags) ─────────────────────────────────────────

_OFX_2X = b"""<?xml version="1.0" encoding="UTF-8"?>
<?OFX OFXHEADER="200" VERSION="220" SECURITY="NONE"?>
<OFX>
  <BANKMSGSRSV1><STMTTRNRS><STMTRS>
    <CURDEF>USD</CURDEF>
    <BANKACCTFROM><ACCTID>999888777</ACCTID><ACCTTYPE>CHECKING</ACCTTYPE></BANKACCTFROM>
    <BANKTRANLIST>
      <STMTTRN>
        <TRNTYPE>DEBIT</TRNTYPE>
        <DTPOSTED>20260201000000.000[-5:EST]</DTPOSTED>
        <TRNAMT>-1200.00</TRNAMT>
        <FITID>XML-2001</FITID>
        <NAME>Rent</NAME>
        <MEMO>monthly</MEMO>
      </STMTTRN>
      <STMTTRN>
        <TRNTYPE>CREDIT</TRNTYPE>
        <DTPOSTED>20260202</DTPOSTED>
        <TRNAMT>30.00</TRNAMT>
        <FITID>XML-2002</FITID>
        <MEMO>Refund</MEMO>
      </STMTTRN>
    </BANKTRANLIST>
  </STMTRS></STMTTRNRS></BANKMSGSRSV1>
</OFX>
"""


def test_parse_ofx_1x():
    acct, raw = svc.parse_ofx(_OFX_1X)
    assert acct == "000123456789"
    assert len(raw) == 2
    assert raw[0]["fitid"] == "TXN-1001"
    assert raw[0]["amount"] == "-4.50"
    assert raw[0]["description"] == "Coffee Shop"


def test_parse_ofx_2x():
    acct, raw = svc.parse_ofx(_OFX_2X)
    assert acct == "999888777"
    assert len(raw) == 2
    assert raw[0]["fitid"] == "XML-2001"
    assert raw[0]["amount"] == "-1200.00"
    # NAME wins over MEMO when both present.
    assert raw[0]["description"] == "Rent"
    # MEMO is the fallback description.
    assert raw[1]["description"] == "Refund"


def test_parse_empty_raises():
    with pytest.raises(svc.OfxImportError):
        svc.parse_ofx(b"")


def test_parse_non_ofx_raises():
    with pytest.raises(svc.OfxImportError):
        svc.parse_ofx(b"Date,Memo,Amount\n2026-01-01,x,1.0\n")


def test_date_normalised_to_iso():
    _, raw = svc.parse_ofx(_OFX_1X)
    draft_date = raw[0]["date"]
    assert draft_date == "2026-01-02"
    _, raw2 = svc.parse_ofx(_OFX_2X)
    assert raw2[0]["date"] == "2026-02-01"


def test_commit_1x_books_income_and_expense(checking):
    res = svc.commit_import(_OFX_1X, account_id=checking["id"])
    assert res == {"created": 2, "duplicates": 0, "skipped": 0, "total_rows": 2}

    from backend.services import ledger as ledger_service

    txns = ledger_service.transactions.all()
    paycheck = next(t for t in txns if t["description"] == "Paycheck")
    coffee = next(t for t in txns if t["description"] == "Coffee Shop")
    assert paycheck["kind"] == "income" and paycheck["to_amount"] == 2500.0
    assert paycheck["to_currency"] == "USD"
    assert coffee["kind"] == "expense" and coffee["from_amount"] == 4.50


def test_commit_2x_books_income_and_expense(checking):
    res = svc.commit_import(_OFX_2X, account_id=checking["id"])
    assert res["created"] == 2
    from backend.services import ledger as ledger_service

    txns = ledger_service.transactions.all()
    rent = next(t for t in txns if t["description"] == "Rent")
    refund = next(t for t in txns if t["description"] == "Refund")
    assert rent["kind"] == "expense" and rent["from_amount"] == 1200.0
    assert refund["kind"] == "income" and refund["to_amount"] == 30.0


def test_external_ref_uses_acct_and_fitid(checking):
    svc.commit_import(_OFX_1X, account_id=checking["id"])
    from backend.services import ledger as ledger_service

    ext = {(primary_source(t), primary_ref(t)) for t in ledger_service.transactions.all()}
    assert ("ofx", "000123456789:TXN-1001") in ext
    assert ("ofx", "000123456789:TXN-1002") in ext


def test_reimport_creates_zero_duplicates(checking):
    first = svc.commit_import(_OFX_1X, account_id=checking["id"])
    assert first["created"] == 2
    second = svc.commit_import(_OFX_1X, account_id=checking["id"])
    assert second["created"] == 0
    assert second["duplicates"] == 2

    from backend.services import ledger as ledger_service

    assert len(ledger_service.transactions.all()) == 2


# ── malformed txn → skip, preview writes nothing ───────────────────────────────────

_OFX_MALFORMED = b"""OFXHEADER:100
<OFX>
<BANKACCTFROM><ACCTID>A1</ACCTID></BANKACCTFROM>
<BANKTRANLIST>
<STMTTRN>
<DTPOSTED>20260102
<TRNAMT>-4.50
<FITID>OK-1
<NAME>Good
</STMTTRN>
<STMTTRN>
<DTPOSTED>20260103
<TRNAMT>0.00
<FITID>ZERO-1
<NAME>Zero
</STMTTRN>
<STMTTRN>
<DTPOSTED>20260104
<TRNAMT>-9.99
<NAME>No FITID
</STMTTRN>
</BANKTRANLIST>
</OFX>
"""


def test_malformed_rows_skipped(checking):
    res = svc.commit_import(_OFX_MALFORMED, account_id=checking["id"])
    assert res["created"] == 1
    assert res["skipped"] == 2
    assert res["total_rows"] == 3


def test_preview_reports_skips(checking):
    prev = svc.preview_import(_OFX_MALFORMED, account_id=checking["id"])
    assert prev.importable == 1
    assert prev.skipped == 2
    reasons = {s["reason"] for s in prev.skip_samples}
    assert "zero amount" in reasons
    assert "missing FITID" in reasons


def test_preview_writes_nothing(checking):
    from backend.services import ledger as ledger_service

    prev = svc.preview_import(_OFX_1X, account_id=checking["id"])
    assert prev.importable == 2
    assert prev.total_rows == 2
    assert len(prev.sample) == 2
    assert ledger_service.transactions.all() == []


def test_preview_sample_shape(checking):
    prev = svc.preview_import(_OFX_1X, account_id=checking["id"])
    row = next(r for r in prev.sample if r["description"] == "Coffee Shop")
    assert set(row) == {
        "date",
        "payee",
        "description",
        "amount",
        "currency",
        "kind",
        "category",
        "original_amount",
        "original_currency",
        "reference",
        "external_refs",
        "flags",
    }
    assert row["amount"] == -4.50
    assert row["kind"] == "expense"


def test_no_account_chosen_raises(isolate_stores):
    with pytest.raises(svc.OfxImportError):
        svc.commit_import(_OFX_1X, account_id=None)


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("-4.50", -4.50),
        ("2500.00", 2500.0),
        ("1.234,56", 1234.56),  # European decimal comma
        ("1,234.56", 1234.56),  # US thousands grouping
        ("", None),
        ("abc", None),
    ],
)
def test_parse_amount(raw, expected):
    assert svc._parse_amount(raw) == expected


# ── QFX (Quicken's OFX variant — same SGML/XML body + extra INTU.* tags) ────────────
# A real-shaped QFX carries the SGML header + an <INTU.BID>/<INTU.USERID> block the OFX
# scanner harmlessly ignores. external_source stays "ofx", ref = <acct>:<FITID> (QFX → the OFX parser).

_QFX = b"""OFXHEADER:100
DATA:OFXSGML
VERSION:102
SECURITY:NONE
ENCODING:USASCII
CHARSET:1252
COMPRESSION:NONE
OLDFILEUID:NONE
NEWFILEUID:NONE

<OFX>
<SIGNONMSGSRSV1>
<SONRS>
<STATUS><CODE>0<SEVERITY>INFO</STATUS>
<DTSERVER>20260301120000
<LANGUAGE>ENG
<FI><ORG>Quicken Bank<FID>10898</FI>
<INTU.BID>10898
</SONRS>
</SIGNONMSGSRSV1>
<BANKMSGSRSV1>
<STMTTRNRS>
<STMTRS>
<CURDEF>USD
<BANKACCTFROM>
<BANKID>121000358
<ACCTID>54321098
<ACCTTYPE>CHECKING
</BANKACCTFROM>
<BANKTRANLIST>
<STMTTRN>
<TRNTYPE>DEBIT
<DTPOSTED>20260302120000.000[-8:PST]
<TRNAMT>-72.40
<FITID>QFX-3001
<NAME>Grocery Mart
<MEMO>card purchase
</STMTTRN>
<STMTTRN>
<TRNTYPE>CREDIT
<DTPOSTED>20260303
<TRNAMT>1800.00
<FITID>QFX-3002
<NAME>Salary
</STMTTRN>
</BANKTRANLIST>
</STMTRS>
</STMTTRNRS>
</BANKMSGSRSV1>
<INTU.USERID>jdoe
</OFX>
"""


def test_qfx_parses_via_ofx_parser():
    # QFX is OFX with extra INTU.* tags — the same scanner parses it cleanly.
    acct, raw = svc.parse_ofx(_QFX)
    assert acct == "54321098"
    assert len(raw) == 2
    assert raw[0]["fitid"] == "QFX-3001"
    assert raw[0]["amount"] == "-72.40"
    assert raw[0]["description"] == "Grocery Mart"
    assert raw[0]["date"] == "2026-03-02"


def test_qfx_commit_books_income_and_expense(checking):
    res = svc.commit_import(_QFX, account_id=checking["id"])
    assert res == {"created": 2, "duplicates": 0, "skipped": 0, "total_rows": 2}

    from backend.services import ledger as ledger_service

    txns = ledger_service.transactions.all()
    salary = next(t for t in txns if t["description"] == "Salary")
    grocery = next(t for t in txns if t["description"] == "Grocery Mart")
    assert salary["kind"] == "income" and salary["to_amount"] == 1800.0
    assert grocery["kind"] == "expense" and grocery["from_amount"] == 72.40
    # external_source stays the OFX namespace (ref = acct:FITID) — QFX routes through the OFX parser.
    ext = {(primary_source(t), primary_ref(t)) for t in txns}
    assert ("ofx", "54321098:QFX-3001") in ext
    assert ("ofx", "54321098:QFX-3002") in ext


def test_qfx_reimport_is_idempotent(checking):
    first = svc.commit_import(_QFX, account_id=checking["id"])
    assert first["created"] == 2
    second = svc.commit_import(_QFX, account_id=checking["id"])
    assert second["created"] == 0
    assert second["duplicates"] == 2

    from backend.services import ledger as ledger_service

    assert len(ledger_service.transactions.all()) == 2


def test_qfx_format_detection():
    from backend.services import import_common

    # An explicit qfx/qbo alias folds to the OFX parser.
    assert import_common.detect_format("qfx", None, b"") == "ofx"
    assert import_common.detect_format("qbo", None, b"") == "ofx"
    # The .qfx / .qbo extension routes to ofx.
    assert import_common.detect_format(None, "statement.qfx", b"") == "ofx"
    assert import_common.detect_format(None, "statement.qbo", b"") == "ofx"
    # No extension → content sniff (the QFX still has the <OFX> root).
    assert import_common.detect_format(None, None, _QFX) == "ofx"
    # An explicitly unsupported declared format is rejected.
    with pytest.raises(import_common.ImportError_):
        import_common.detect_format("docx", None, b"")
