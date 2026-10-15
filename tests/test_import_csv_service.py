"""Unit tests for the mapped CSV transaction import (services/import_csv.py).

Covers: header parse + mapping validation, single-amount vs inflow/outflow modes, a
malformed row (skip), account resolution by name + chosen id, idempotent dedup on
re-import (ZERO duplicates), and the preview writing NOTHING. Stores are isolated to
tmp files per the simplefin-service pattern."""

from __future__ import annotations

import pytest

from backend.services import import_csv as svc


@pytest.fixture(autouse=True)
def isolate_stores(tmp_path, monkeypatch, fx_rates):
    """Rebind the ledger's account/transaction singletons to fresh tmp-backed stores so
    each test is isolated and dedup is deterministic (the service reads/writes via
    ledger_service.accounts/transactions)."""
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


# ── single signed-amount CSV ───────────────────────────────────────────────────────

_AMOUNT_CSV = b"""Date,Memo,Amount
2026-01-02,Coffee shop,-4.50
2026-01-03,Paycheck,2500.00
2026-01-04,Grocery store,-87.20
"""

_AMOUNT_MAPPING = {"date": "Date", "description": "Memo", "amount": "Amount"}


def test_parse_csv_headers():
    cols, rows = svc.parse_csv(_AMOUNT_CSV)
    assert cols == ["Date", "Memo", "Amount"]
    assert len(rows) == 3


def test_detect_headers():
    assert svc.detect_headers(_AMOUNT_CSV) == ["Date", "Memo", "Amount"]


def test_parse_empty_file_raises():
    with pytest.raises(svc.CsvImportError):
        svc.parse_csv(b"")


def test_validate_mapping_requires_date_and_description():
    cols = ["Date", "Memo", "Amount"]
    with pytest.raises(svc.CsvImportError):
        svc.validate_mapping(cols, {"description": "Memo", "amount": "Amount"})
    with pytest.raises(svc.CsvImportError):
        svc.validate_mapping(cols, {"date": "Date", "amount": "Amount"})


def test_validate_mapping_requires_an_amount_mode():
    cols = ["Date", "Memo", "Amount"]
    with pytest.raises(svc.CsvImportError):
        svc.validate_mapping(cols, {"date": "Date", "description": "Memo"})


def test_validate_mapping_rejects_both_amount_modes():
    cols = ["Date", "Memo", "Amount", "In", "Out"]
    with pytest.raises(svc.CsvImportError):
        svc.validate_mapping(
            cols, {"date": "Date", "description": "Memo", "amount": "Amount", "inflow": "In"}
        )


def test_validate_mapping_rejects_unknown_column():
    cols = ["Date", "Memo", "Amount"]
    with pytest.raises(svc.CsvImportError):
        svc.validate_mapping(cols, {"date": "Nope", "description": "Memo", "amount": "Amount"})


def test_commit_single_amount_books_income_and_expense(checking):
    res = svc.commit_import(_AMOUNT_CSV, _AMOUNT_MAPPING, account_id=checking["id"])
    assert res == {"created": 3, "duplicates": 0, "skipped": 0, "total_rows": 3}

    from backend.services import ledger as ledger_service

    txns = ledger_service.transactions.all()
    assert len(txns) == 3
    kinds = sorted(t["kind"] for t in txns)
    assert kinds == ["expense", "expense", "income"]
    paycheck = next(t for t in txns if t["kind"] == "income")
    assert paycheck["to_amount"] == 2500.0
    assert paycheck["to_currency"] == "USD"


# ── inflow / outflow split-column CSV ──────────────────────────────────────────────

_SPLIT_CSV = b"""Posted,Description,Debit,Credit
2026-02-01,Rent,1200.00,
2026-02-02,Refund,,30.00
"""

_SPLIT_MAPPING = {
    "date": "Posted",
    "description": "Description",
    "outflow": "Debit",
    "inflow": "Credit",
}


def test_commit_inflow_outflow(checking):
    res = svc.commit_import(_SPLIT_CSV, _SPLIT_MAPPING, account_id=checking["id"])
    assert res["created"] == 2

    from backend.services import ledger as ledger_service

    txns = ledger_service.transactions.all()
    rent = next(t for t in txns if t["description"] == "Rent")
    refund = next(t for t in txns if t["description"] == "Refund")
    assert rent["kind"] == "expense" and rent["from_amount"] == 1200.0
    assert refund["kind"] == "income" and refund["to_amount"] == 30.0


# ── malformed rows are skipped, not fatal ──────────────────────────────────────────

_MALFORMED_CSV = b"""Date,Memo,Amount
2026-01-02,Good row,-4.50
,No date here,-1.00
2026-01-05,Bad amount,not-a-number
2026-01-06,Zero amount,0
2026-01-07,Another good,-9.99
"""


def test_malformed_rows_skipped(checking):
    res = svc.commit_import(_MALFORMED_CSV, _AMOUNT_MAPPING, account_id=checking["id"])
    assert res["created"] == 2
    assert res["skipped"] == 3
    assert res["total_rows"] == 5


def test_preview_reports_skips_with_reasons(checking):
    prev = svc.preview_import(_MALFORMED_CSV, _AMOUNT_MAPPING, account_id=checking["id"])
    assert prev.importable == 2
    assert prev.skipped == 3
    reasons = {s["reason"] for s in prev.skip_samples}
    assert "missing date" in reasons
    assert "unparseable amount" in reasons
    assert "zero amount" in reasons


# ── account resolution by name ─────────────────────────────────────────────────────

_NAMED_CSV = b"""Date,Memo,Amount,Acct
2026-03-01,Lunch,-12.00,Checking
2026-03-02,Mystery,-5.00,Nonexistent
"""


def test_account_resolved_by_name_column(checking):
    mapping = {"date": "Date", "description": "Memo", "amount": "Amount", "account": "Acct"}
    res = svc.commit_import(_NAMED_CSV, mapping, account_id=None)
    # The "Nonexistent" account row is skipped, the "Checking" row imports.
    assert res["created"] == 1
    assert res["skipped"] == 1


# ── idempotent dedup on re-import (ZERO duplicates) ────────────────────────────────


def test_reimport_creates_zero_duplicates(checking):
    first = svc.commit_import(_AMOUNT_CSV, _AMOUNT_MAPPING, account_id=checking["id"])
    assert first["created"] == 3

    from backend.services import ledger as ledger_service

    assert len(ledger_service.transactions.all()) == 3

    second = svc.commit_import(_AMOUNT_CSV, _AMOUNT_MAPPING, account_id=checking["id"])
    assert second["created"] == 0
    assert second["duplicates"] == 3
    # No new rows on the re-import.
    assert len(ledger_service.transactions.all()) == 3


def test_duplicate_rows_within_one_file(checking):
    """Two genuinely-identical rows (two same-day $4.50 coffees) are TWO real charges and must
    BOTH import — the occurrence-index id fix (RFC transaction-dedup-engine §id fix). Pre-fix
    they collided on one content-hash external_ref and the second was silently dropped."""
    dup_csv = b"""Date,Memo,Amount
2026-01-02,Coffee,-4.50
2026-01-02,Coffee,-4.50
"""
    res = svc.commit_import(dup_csv, _AMOUNT_MAPPING, account_id=checking["id"])
    assert res["created"] == 2
    assert res["duplicates"] == 0
    # ...and re-importing the SAME file is still a perfect no-op (idempotency preserved).
    again = svc.commit_import(dup_csv, _AMOUNT_MAPPING, account_id=checking["id"])
    assert again["created"] == 0
    assert again["duplicates"] == 2


# ── preview writes NOTHING ─────────────────────────────────────────────────────────


def test_preview_writes_nothing(checking):
    from backend.services import ledger as ledger_service

    prev = svc.preview_import(_AMOUNT_CSV, _AMOUNT_MAPPING, account_id=checking["id"])
    assert prev.importable == 3
    assert prev.total_rows == 3
    assert len(prev.sample) == 3
    # The ledger is untouched by a preview.
    assert ledger_service.transactions.all() == []


def test_preview_sample_shape(checking):
    prev = svc.preview_import(_AMOUNT_CSV, _AMOUNT_MAPPING, account_id=checking["id"])
    row = prev.sample[0]
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


# ── category-opaque-ids migration: the "category" column carries a LABEL, resolved ─────


def test_csv_import_resolves_category_by_label(checking):
    """A CSV 'Category' column carries a human LABEL (a bank's own text, or a prior export) —
    resolved against the live category store to its opaque id, never slugified/derived
    (category-opaque-ids migration). The shared categories.json is seeded with
    BUILTIN_CATEGORIES before every test (tests/conftest.py); 'Food & Dining' is cat_13095ba70000's
    stable label."""
    csv_bytes = b"""Date,Memo,Amount,Category
2026-01-02,Coffee shop,-4.50,Food & Dining
"""
    mapping = {"date": "Date", "description": "Memo", "amount": "Amount", "category": "Category"}
    prev = svc.preview_import(csv_bytes, mapping, account_id=checking["id"])
    assert prev.sample[0]["category"] == "cat_13095ba70000"


def test_csv_import_category_label_match_is_case_insensitive(checking):
    csv_bytes = b"""Date,Memo,Amount,Category
2026-01-02,Coffee shop,-4.50,food & dining
"""
    mapping = {"date": "Date", "description": "Memo", "amount": "Amount", "category": "Category"}
    prev = svc.preview_import(csv_bytes, mapping, account_id=checking["id"])
    assert prev.sample[0]["category"] == "cat_13095ba70000"


def test_csv_import_unresolvable_category_dropped(checking):
    """An unrecognized category column value is dropped (never invents/derives a slug) — the
    row still imports, just uncategorized."""
    csv_bytes = b"""Date,Memo,Amount,Category
2026-01-02,Coffee shop,-4.50,Not A Real Category
"""
    mapping = {"date": "Date", "description": "Memo", "amount": "Amount", "category": "Category"}
    prev = svc.preview_import(csv_bytes, mapping, account_id=checking["id"])
    assert prev.sample[0]["category"] == ""
    assert prev.importable == 1  # still imports, just uncategorized


# ── amount parsing edge cases ──────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("$1,234.56", 1234.56),
        ("(89.10)", -89.10),
        ("-50", -50.0),
        ("  12.00  ", 12.0),
        ("", None),
        ("abc", None),
    ],
)
def test_parse_amount(raw, expected):
    assert svc._parse_amount(raw) == expected
