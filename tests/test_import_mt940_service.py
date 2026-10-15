"""Unit tests for the MT940 transaction import (services/import_mt940.py).

Covers: SWIFT tag parse (:20:/:25:/:60F:/:61:/:86:/:62F:), D/C sign → income/expense,
native currency from the account, the content-hash external_ref dedup (re-import → ZERO
duplicates), a malformed line (skip), preview writing NOTHING, and format detection
(extension + content sniff + reject non-matching). Stores isolated to tmp files (mirrors
test_import_ofx_service)."""

from __future__ import annotations

import pytest

from backend.core.txn_sources import primary_ref, primary_source
from backend.services import import_mt940 as svc


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


# A real-shaped MT940 statement: a debit (Coffee) + a credit (Salary), comma decimals.
_MT940 = b""":20:STMT-2026-001
:25:NL12ABNA0123456789
:28C:00001/001
:60F:C260101EUR1000,00
:61:2601020102DR4,50NTRFNONREF//BANKREF1
:86:Coffee Shop downtown
:61:2601030103CR2500,00NTRFNONREF//BANKREF2
:86:Salary payment ACME
:62F:C260103EUR3495,50
-
"""


def test_parse_mt940():
    acct, currency, raw = svc.parse_mt940(_MT940)
    assert acct == "NL12ABNA0123456789"
    assert currency == "EUR"
    assert len(raw) == 2
    assert raw[0]["dc"] == "D"
    assert raw[0]["amount"] == "4,50"
    assert raw[0]["date"] == "2026-01-02"
    assert raw[0]["description"] == "Coffee Shop downtown"
    assert raw[1]["dc"] == "C"
    assert raw[1]["description"] == "Salary payment ACME"


def test_parse_empty_raises():
    with pytest.raises(svc.Mt940ImportError):
        svc.parse_mt940(b"")


def test_parse_non_mt940_raises():
    with pytest.raises(svc.Mt940ImportError):
        svc.parse_mt940(b"Date,Memo,Amount\n2026-01-01,x,1.0\n")


def test_commit_books_income_and_expense(checking):
    res = svc.commit_import(_MT940, account_id=checking["id"])
    assert res == {"created": 2, "duplicates": 0, "skipped": 0, "total_rows": 2}

    from backend.services import ledger as ledger_service

    txns = ledger_service.transactions.all()
    salary = next(t for t in txns if t["description"] == "Salary payment ACME")
    coffee = next(t for t in txns if t["description"] == "Coffee Shop downtown")
    assert salary["kind"] == "income" and salary["to_amount"] == 2500.0
    assert salary["to_currency"] == "EUR"
    assert coffee["kind"] == "expense" and coffee["from_amount"] == 4.50
    assert coffee["from_currency"] == "EUR"


def test_external_ref_namespaced_by_account(checking):
    svc.commit_import(_MT940, account_id=checking["id"])
    from backend.services import ledger as ledger_service

    rows = ledger_service.transactions.all()
    assert all(primary_source(t) == "statement" for t in rows)
    refs = {primary_ref(t) for t in rows}
    assert all(r.startswith("NL12ABNA0123456789:") for r in refs)
    assert len(refs) == 2  # distinct content hashes


def test_reimport_creates_zero_duplicates(checking):
    first = svc.commit_import(_MT940, account_id=checking["id"])
    assert first["created"] == 2
    second = svc.commit_import(_MT940, account_id=checking["id"])
    assert second["created"] == 0
    assert second["duplicates"] == 2

    from backend.services import ledger as ledger_service

    assert len(ledger_service.transactions.all()) == 2


# ── malformed line → skip, preview writes nothing ───────────────────────────────────

_MT940_MALFORMED = b""":20:STMT-X
:25:ACCT-A
:60F:C260101EUR100,00
:61:2601020102DR4,50NTRFNONREF//OK
:86:Good line
:61:2601030103CR0,00NTRFNONREF//ZERO
:86:Zero amount
:62F:C260103EUR95,50
"""


def test_malformed_rows_skipped(checking):
    res = svc.commit_import(_MT940_MALFORMED, account_id=checking["id"])
    assert res["created"] == 1
    assert res["skipped"] == 1
    assert res["total_rows"] == 2


def test_preview_writes_nothing(checking):
    from backend.services import ledger as ledger_service

    prev = svc.preview_import(_MT940, account_id=checking["id"])
    assert prev.importable == 2
    assert prev.total_rows == 2
    assert len(prev.sample) == 2
    assert ledger_service.transactions.all() == []


def test_preview_sample_shape(checking):
    prev = svc.preview_import(_MT940, account_id=checking["id"])
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
    with pytest.raises(svc.Mt940ImportError):
        svc.commit_import(_MT940, account_id=None)


def test_format_detection():
    from backend.services import import_common

    # Explicit + alias (.sta → mt940).
    assert import_common.detect_format("mt940", None, b"") == "mt940"
    assert import_common.detect_format("sta", None, b"") == "mt940"
    # Extension.
    assert import_common.detect_format(None, "statement.mt940", b"") == "mt940"
    assert import_common.detect_format(None, "statement.sta", b"") == "mt940"
    # No extension → content sniff.
    assert import_common.detect_format(None, None, _MT940) == "mt940"
    # A CSV is NOT mistaken for MT940.
    assert import_common.detect_format(None, None, b"Date,Amount\n2026-01-01,5\n") == "csv"


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("4,50", 4.50),
        ("2500,00", 2500.0),
        ("1.234,56", 1234.56),  # European thousands + decimal comma
        ("", None),
        ("abc", None),
    ],
)
def test_parse_amount(raw, expected):
    assert svc._parse_amount(raw) == expected
