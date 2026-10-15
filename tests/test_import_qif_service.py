"""Unit tests for the QIF transaction import (services/import_qif.py).

Covers: QIF !Type/D/T/P/M/L parse, signed amount → income/expense, date normalisation,
the content-hash external_ref dedup (re-import → ZERO duplicates), a malformed record
(skip), investment-type rejection, and the preview writing NOTHING. Stores isolated to
tmp files (mirrors test_import_csv_service)."""

from __future__ import annotations

import pytest

from backend.services import import_qif as svc


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


_QIF = b"""!Type:Bank
D01/02'2026
T-4.50
PCoffee Shop
MMorning latte
LFood
^
D01/03/2026
T2500.00
PPaycheck
^
D01/04/2026
T-87.20
PGrocery Store
LFood:Groceries
^
"""


def test_parse_qif():
    raw = svc.parse_qif(_QIF)
    assert len(raw) == 3
    assert raw[0]["payee"] == "Coffee Shop"
    assert raw[0]["amount"] == "-4.50"
    assert raw[0]["memo"] == "Morning latte"
    assert raw[0]["category"] == "Food"


def test_parse_empty_raises():
    with pytest.raises(svc.QifImportError):
        svc.parse_qif(b"")


def test_parse_no_type_header_raises():
    with pytest.raises(svc.QifImportError):
        svc.parse_qif(b"D01/02/2026\nT-4.50\nPx\n^\n")


def test_investment_type_rejected():
    with pytest.raises(svc.QifImportError):
        svc.parse_qif(b"!Type:Invst\nD01/02/2026\nNBuy\n^\n")


def test_date_normalised_to_iso():
    raw = svc.parse_qif(_QIF)
    assert raw[0]["date"] == "2026-01-02"
    assert raw[1]["date"] == "2026-01-03"


def test_commit_books_income_and_expense(checking):
    res = svc.commit_import(_QIF, account_id=checking["id"])
    assert res == {"created": 3, "duplicates": 0, "skipped": 0, "total_rows": 3}

    from backend.services import ledger as ledger_service

    txns = ledger_service.transactions.all()
    paycheck = next(t for t in txns if t["description"] == "Paycheck")
    coffee = next(t for t in txns if t["description"] == "Coffee Shop")
    assert paycheck["kind"] == "income" and paycheck["to_amount"] == 2500.0
    assert paycheck["to_currency"] == "USD"
    assert coffee["kind"] == "expense" and coffee["from_amount"] == 4.50


def test_reimport_creates_zero_duplicates(checking):
    first = svc.commit_import(_QIF, account_id=checking["id"])
    assert first["created"] == 3
    second = svc.commit_import(_QIF, account_id=checking["id"])
    assert second["created"] == 0
    assert second["duplicates"] == 3

    from backend.services import ledger as ledger_service

    assert len(ledger_service.transactions.all()) == 3


# ── malformed record → skip; preview writes nothing ────────────────────────────────

_QIF_MALFORMED = b"""!Type:Bank
D01/02/2026
T-4.50
PGood
^
T-1.00
PNo date
^
D01/05/2026
Tnot-a-number
PBad amount
^
D01/06/2026
T0
PZero
^
"""


def test_malformed_rows_skipped(checking):
    res = svc.commit_import(_QIF_MALFORMED, account_id=checking["id"])
    assert res["created"] == 1
    assert res["skipped"] == 3
    assert res["total_rows"] == 4


def test_preview_reports_skips(checking):
    prev = svc.preview_import(_QIF_MALFORMED, account_id=checking["id"])
    assert prev.importable == 1
    assert prev.skipped == 3
    reasons = {s["reason"] for s in prev.skip_samples}
    assert "missing date" in reasons
    assert "unparseable amount" in reasons
    assert "zero amount" in reasons


def test_preview_writes_nothing(checking):
    from backend.services import ledger as ledger_service

    prev = svc.preview_import(_QIF, account_id=checking["id"])
    assert prev.importable == 3
    assert prev.total_rows == 3
    assert len(prev.sample) == 3
    assert ledger_service.transactions.all() == []


def test_preview_sample_shape(checking):
    prev = svc.preview_import(_QIF, account_id=checking["id"])
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


def test_category_mapped_when_known(checking, isolate_stores):
    # resolve_category_ref matches a category id OR an exact (case-insensitive) LABEL — no
    # slug/key derivation (category-opaque-ids migration). A category labelled 'Food' resolves
    # the QIF 'L Food' leaf to its opaque id; an unknown leaf ('L Food:Groceries' → leaf
    # 'Groceries', no such category) is dropped to uncategorized.
    from backend.services.categories import create_category

    food = create_category("Food")
    prev = svc.preview_import(_QIF, account_id=checking["id"])
    coffee = next(r for r in prev.sample if r["description"] == "Coffee Shop")
    grocery = next(r for r in prev.sample if r["description"] == "Grocery Store")
    assert coffee["category"] == food["id"]
    assert grocery["category"] == ""


def test_no_account_chosen_raises(isolate_stores):
    with pytest.raises(svc.QifImportError):
        svc.commit_import(_QIF, account_id=None)


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("-4.50", -4.50),
        ("2,500.00", 2500.0),
        ("(89.10)", -89.10),
        ("1.234,56", 1234.56),  # European decimal comma
        ("", None),
        ("xyz", None),
    ],
)
def test_parse_amount(raw, expected):
    assert svc._parse_amount(raw) == expected


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("01/02'2026", "2026-01-02"),
        ("01/02/2026", "2026-01-02"),
        ("2026-01-02", "2026-01-02"),
        ("12/31/26", "2026-12-31"),
    ],
)
def test_parse_date(raw, expected):
    assert svc._parse_date(raw) == expected
