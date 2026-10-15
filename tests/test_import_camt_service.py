"""Unit tests for the CAMT.053 transaction import (services/import_camt.py).

Covers: ISO 20022 XML parse (namespace-agnostic, two camt.053 versions), CdtDbtInd sign →
income/expense, native currency from the account, the AcctSvcrRef/EndToEndId (else content
hash) external_ref dedup (re-import → ZERO duplicates), a malformed entry (skip), preview
writing NOTHING, and format detection (extension + content sniff + reject non-matching).
Stores isolated to tmp files (mirrors test_import_ofx_service)."""

from __future__ import annotations

import pytest

from backend.core.txn_sources import primary_ref, primary_source
from backend.services import import_camt as svc


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
    return accounts.create({"name": "Checking", "kind": "asset", "type": "cash", "currency": "EUR"})


# A real-shaped camt.053.001.02 statement: a debit (Coffee, has AcctSvcrRef) + a credit
# (Salary, only EndToEndId). The namespace is pinned to .001.02; the parser is ns-agnostic.
_CAMT = b"""<?xml version="1.0" encoding="UTF-8"?>
<Document xmlns="urn:iso:std:iso:20022:tech:xsd:camt.053.001.02">
  <BkToCstmrStmt>
    <Stmt>
      <Id>STMT-001</Id>
      <Acct><Id><IBAN>NL12ABNA0123456789</IBAN></Id></Acct>
      <Ntry>
        <Amt Ccy="EUR">4.50</Amt>
        <CdtDbtInd>DBIT</CdtDbtInd>
        <Sts>BOOK</Sts>
        <BookgDt><Dt>2026-01-02</Dt></BookgDt>
        <ValDt><Dt>2026-01-02</Dt></ValDt>
        <NtryDtls><TxDtls>
          <Refs><AcctSvcrRef>REF-COFFEE-1</AcctSvcrRef></Refs>
          <RmtInf><Ustrd>Coffee Shop downtown</Ustrd></RmtInf>
        </TxDtls></NtryDtls>
      </Ntry>
      <Ntry>
        <Amt Ccy="EUR">2500.00</Amt>
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

# A camt.053.001.08 variant (different namespace) — proves the parser is ns-agnostic.
_CAMT_V8 = b"""<?xml version="1.0" encoding="UTF-8"?>
<Document xmlns="urn:iso:std:iso:20022:tech:xsd:camt.053.001.08">
  <BkToCstmrStmt><Stmt>
    <Acct><Id><Othr><Id>ACCT-V8</Id></Othr></Id></Acct>
    <Ntry>
      <Amt Ccy="USD">12.00</Amt>
      <CdtDbtInd>DBIT</CdtDbtInd>
      <BookgDt><DtTm>2026-02-01T09:00:00</DtTm></BookgDt>
      <NtryDtls><TxDtls>
        <Refs><AcctSvcrRef>V8-1</AcctSvcrRef></Refs>
        <RmtInf><Ustrd>Lunch</Ustrd></RmtInf>
      </TxDtls></NtryDtls>
    </Ntry>
  </Stmt></BkToCstmrStmt>
</Document>
"""


def test_parse_camt():
    acct, raw = svc.parse_camt(_CAMT)
    assert acct == "NL12ABNA0123456789"
    assert len(raw) == 2
    assert raw[0]["dc"] == "DBIT"
    assert raw[0]["amount"] == "4.50"
    assert raw[0]["currency"] == "EUR"
    assert raw[0]["date"] == "2026-01-02"
    assert raw[0]["description"] == "Coffee Shop downtown"
    assert raw[0]["ref"] == "REF-COFFEE-1"
    assert raw[1]["dc"] == "CRDT"
    assert raw[1]["ref"] == "E2E-SALARY-1"


def test_parse_v8_namespace_agnostic():
    acct, raw = svc.parse_camt(_CAMT_V8)
    assert acct == "ACCT-V8"
    assert len(raw) == 1
    assert raw[0]["date"] == "2026-02-01"  # <DtTm> trimmed to the date
    assert raw[0]["description"] == "Lunch"


def test_parse_empty_raises():
    with pytest.raises(svc.CamtImportError):
        svc.parse_camt(b"")


def test_parse_non_camt_raises():
    # Arbitrary XML that is NOT a bank-to-customer statement is rejected.
    with pytest.raises(svc.CamtImportError):
        svc.parse_camt(b"<?xml version='1.0'?><root><a>1</a></root>")


def test_commit_books_income_and_expense(checking):
    res = svc.commit_import(_CAMT, account_id=checking["id"])
    assert res == {"created": 2, "duplicates": 0, "skipped": 0, "total_rows": 2}

    from backend.services import ledger as ledger_service

    txns = ledger_service.transactions.all()
    salary = next(t for t in txns if t["description"] == "Salary payment ACME")
    coffee = next(t for t in txns if t["description"] == "Coffee Shop downtown")
    assert salary["kind"] == "income" and salary["to_amount"] == 2500.0
    assert salary["to_currency"] == "EUR"
    assert coffee["kind"] == "expense" and coffee["from_amount"] == 4.50


def test_external_ref_uses_ref(checking):
    svc.commit_import(_CAMT, account_id=checking["id"])
    from backend.services import ledger as ledger_service

    ext = {(primary_source(t), primary_ref(t)) for t in ledger_service.transactions.all()}
    assert ("statement", "NL12ABNA0123456789:REF-COFFEE-1") in ext
    assert ("statement", "NL12ABNA0123456789:E2E-SALARY-1") in ext


def test_reimport_creates_zero_duplicates(checking):
    first = svc.commit_import(_CAMT, account_id=checking["id"])
    assert first["created"] == 2
    second = svc.commit_import(_CAMT, account_id=checking["id"])
    assert second["created"] == 0
    assert second["duplicates"] == 2

    from backend.services import ledger as ledger_service

    assert len(ledger_service.transactions.all()) == 2


# An entry with NO ref → the content-hash external_ref (still idempotent).
_CAMT_NO_REF = b"""<?xml version="1.0" encoding="UTF-8"?>
<Document xmlns="urn:iso:std:iso:20022:tech:xsd:camt.053.001.02">
  <BkToCstmrStmt><Stmt>
    <Acct><Id><IBAN>ACC-NOREF</IBAN></Id></Acct>
    <Ntry>
      <Amt Ccy="EUR">9.99</Amt><CdtDbtInd>DBIT</CdtDbtInd>
      <BookgDt><Dt>2026-03-01</Dt></BookgDt>
      <NtryDtls><TxDtls><RmtInf><Ustrd>No ref purchase</Ustrd></RmtInf></TxDtls></NtryDtls>
    </Ntry>
  </Stmt></BkToCstmrStmt>
</Document>
"""


def test_no_ref_uses_content_hash_and_dedups(checking):
    first = svc.commit_import(_CAMT_NO_REF, account_id=checking["id"])
    assert first["created"] == 1
    from backend.services import ledger as ledger_service

    txn = next(iter(ledger_service.transactions.all()))
    assert primary_source(txn) == "statement"
    assert primary_ref(txn).startswith("ACC-NOREF:") and primary_ref(txn) != "ACC-NOREF:"
    second = svc.commit_import(_CAMT_NO_REF, account_id=checking["id"])
    assert second["created"] == 0 and second["duplicates"] == 1


# A malformed entry (missing CdtDbtInd) → skip.
_CAMT_MALFORMED = b"""<?xml version="1.0" encoding="UTF-8"?>
<Document xmlns="urn:iso:std:iso:20022:tech:xsd:camt.053.001.02">
  <BkToCstmrStmt><Stmt>
    <Acct><Id><IBAN>ACC-M</IBAN></Id></Acct>
    <Ntry>
      <Amt Ccy="EUR">5.00</Amt><CdtDbtInd>DBIT</CdtDbtInd>
      <BookgDt><Dt>2026-04-01</Dt></BookgDt>
      <NtryDtls><TxDtls><Refs><AcctSvcrRef>OK</AcctSvcrRef></Refs>
        <RmtInf><Ustrd>Good</Ustrd></RmtInf></TxDtls></NtryDtls>
    </Ntry>
    <Ntry>
      <Amt Ccy="EUR">3.00</Amt>
      <BookgDt><Dt>2026-04-02</Dt></BookgDt>
      <NtryDtls><TxDtls><Refs><AcctSvcrRef>BAD</AcctSvcrRef></Refs>
        <RmtInf><Ustrd>No sign</Ustrd></RmtInf></TxDtls></NtryDtls>
    </Ntry>
  </Stmt></BkToCstmrStmt>
</Document>
"""


def test_malformed_rows_skipped(checking):
    res = svc.commit_import(_CAMT_MALFORMED, account_id=checking["id"])
    assert res["created"] == 1
    assert res["skipped"] == 1
    assert res["total_rows"] == 2


def test_preview_writes_nothing(checking):
    from backend.services import ledger as ledger_service

    prev = svc.preview_import(_CAMT, account_id=checking["id"])
    assert prev.importable == 2
    assert prev.total_rows == 2
    assert len(prev.sample) == 2
    assert ledger_service.transactions.all() == []


def test_preview_sample_shape(checking):
    prev = svc.preview_import(_CAMT, account_id=checking["id"])
    row = next(r for r in prev.sample if r["description"] == "Coffee Shop downtown")
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
    assert row["currency"] == "EUR"


def test_no_account_chosen_raises(isolate_stores):
    with pytest.raises(svc.CamtImportError):
        svc.commit_import(_CAMT, account_id=None)


def test_format_detection():
    from backend.services import import_common

    # detect_format returns the FORMAT (a parse hint), NOT the ref namespace — those are two
    # different things and the 2026-08-01 collapse only moved the latter.
    # Explicit.
    assert import_common.detect_format("camt", None, b"") == "camt"
    # Extension.
    assert import_common.detect_format(None, "statement.camt", b"") == "camt"
    # No extension → content sniff (the BkToCstmrStmt marker).
    assert import_common.detect_format(None, None, _CAMT) == "camt"
    assert import_common.detect_format(None, "stmt.xml", _CAMT) == "camt"
    # Arbitrary XML is NOT grabbed as CAMT (falls through to the csv default).
    assert import_common.detect_format(None, "stmt.xml", b"<root><a>1</a></root>") == "csv"
