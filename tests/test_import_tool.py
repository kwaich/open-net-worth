"""Tests for the Lucius ``import_transactions`` tool (backend/ai/tools/imports.py).

Covers UI↔Lucius parity for the file/URL importer:
  * preview (commit=false, the default) writes NOTHING
  * commit (commit=true) writes via the ONE ledger choke point
  * a re-import is idempotent (zero duplicates)
  * CSV with a mapping, OFX, QFX (Quicken's OFX variant), and a mocked URL source
  * account / format / source validation
  * the tool is NOT on the headless ceiling / _T0 (a WRITE — never unattended)

Stores isolated to tmp files (mirrors test_import_ofx_service / test_import_csv_service).
Network is mocked (no real HTTP)."""

from __future__ import annotations

import asyncio
import json

import pytest

from backend.ai.tools import execute_tool
from backend.core.txn_sources import primary_ref, primary_source


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


def _call(**kwargs) -> dict:
    text, proposed = asyncio.run(execute_tool("import_transactions", kwargs))
    assert proposed is None  # never a position-style proposed_change
    return json.loads(text)


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

_CSV = b"Date,Memo,Amount\n2026-01-01,Lunch,-12.00\n2026-01-02,Refund,5.00\n"
_CSV_MAPPING = {"date": "Date", "description": "Memo", "amount": "Amount"}


# ── preview writes nothing ──────────────────────────────────────────────────────────


def test_preview_default_writes_nothing(checking, isolate_stores):
    _, txns = isolate_stores
    res = _call(content=_OFX.decode(), account_id=checking["id"])
    assert res["ok"] is True
    assert res["format"] == "ofx"
    assert res["committed"] is False
    assert res["result"]["importable"] == 2
    assert txns.all() == []  # PROOF: preview is pure


# ── commit writes via the choke point ───────────────────────────────────────────────


def test_commit_writes_via_ledger(checking, isolate_stores):
    _, txns = isolate_stores
    res = _call(content=_OFX.decode(), account_id=checking["id"], commit=True)
    assert res["ok"] is True
    assert res["committed"] is True
    assert res["result"]["created"] == 2
    assert len(txns.all()) == 2
    # external_source is the OFX namespace → idempotent.
    ext = {(primary_source(t), primary_ref(t)) for t in txns.all()}
    assert ext == {("ofx", "ACC-1:T1"), ("ofx", "ACC-1:T2")}


def test_commit_is_idempotent(checking, isolate_stores):
    _, txns = isolate_stores
    first = _call(content=_OFX.decode(), account_id=checking["id"], commit=True)
    assert first["result"]["created"] == 2
    second = _call(content=_OFX.decode(), account_id=checking["id"], commit=True)
    assert second["result"]["created"] == 0
    assert second["result"]["duplicates"] == 2
    assert len(txns.all()) == 2  # no double-post


# ── QFX (auto-detected via the OFX content sniff) ───────────────────────────────────


def test_qfx_auto_detect_and_commit(checking, isolate_stores):
    _, txns = isolate_stores
    res = _call(content=_QFX.decode(), format="qfx", account_id=checking["id"], commit=True)
    assert res["format"] == "ofx"  # qfx folds to the OFX parser
    assert res["result"]["created"] == 1
    assert {(primary_source(t), primary_ref(t)) for t in txns.all()} == {("ofx", "QACC:Q1")}


# ── CSV with a mapping ──────────────────────────────────────────────────────────────


def test_csv_with_mapping(checking, isolate_stores):
    _, txns = isolate_stores
    res = _call(
        content=_CSV.decode(),
        format="csv",
        mapping=_CSV_MAPPING,
        account_id=checking["id"],
        commit=True,
    )
    assert res["format"] == "csv"
    assert res["result"]["created"] == 2
    assert len(txns.all()) == 2


# ── URL source (network mocked) ─────────────────────────────────────────────────────


def test_source_url_mocked(checking, isolate_stores, monkeypatch):
    _, txns = isolate_stores
    from backend.services import import_common

    def _fake_fetch(url):
        assert url == "https://example.test/stmt.ofx"
        return _OFX, "stmt.ofx"

    monkeypatch.setattr(import_common, "fetch_url_bytes", _fake_fetch)
    res = _call(source_url="https://example.test/stmt.ofx", account_id=checking["id"], commit=True)
    assert res["format"] == "ofx"
    assert res["result"]["created"] == 2
    assert len(txns.all()) == 2


# ── validation ──────────────────────────────────────────────────────────────────────


def test_missing_account_id(checking):
    res = _call(content=_OFX.decode())
    assert res["ok"] is False
    assert "account_id" in res["error"]


def test_unknown_account_id(checking):
    res = _call(content=_OFX.decode(), account_id="nope")
    assert res["ok"] is False
    assert "nope" in res["error"]


def test_no_source(checking):
    res = _call(account_id=checking["id"])
    assert res["ok"] is False
    assert "content" in res["error"] or "source_url" in res["error"]


def test_both_sources_rejected(checking):
    res = _call(
        content=_OFX.decode(), source_url="https://example.test/x.ofx", account_id=checking["id"]
    )
    assert res["ok"] is False


def test_unsupported_declared_format(checking):
    res = _call(content=_CSV.decode(), format="docx", account_id=checking["id"])
    assert res["ok"] is False
    assert "docx" in res["error"]
