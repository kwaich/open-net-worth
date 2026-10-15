"""Unit tests for the XLSX transaction import (services/import_xlsx.py).

XLSX is "CSV with cells" — it reads the first sheet into (columns, rows) and DELEGATES the
column mapping / draft build / dedup to import_csv. Covers: workbook parse (incl. Excel
datetime → ISO date), single-amount + inflow/outflow mapping modes, the xlsx row-hash
external_ref dedup (re-import → ZERO duplicates), a malformed row (skip), preview writing
NOTHING, the column probe, and format detection (extension + ZIP content sniff + reject
non-matching). The fixture workbook is built in-test via openpyxl. Stores isolated to tmp
files (mirrors test_import_csv_service)."""

from __future__ import annotations

import datetime
import io

import pytest

from backend.core.txn_sources import primary_source
from backend.services import import_xlsx as svc


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


def _make_xlsx(header: list, rows: list) -> bytes:
    """Build a single-sheet workbook in memory and return its bytes."""
    import openpyxl

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(header)
    for r in rows:
        ws.append(r)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


# A signed-amount sheet with a real datetime cell (Excel stores dates as datetimes).
_XLSX = _make_xlsx(
    ["Date", "Memo", "Amount"],
    [
        [datetime.datetime(2026, 1, 2), "Coffee Shop", -4.50],
        [datetime.datetime(2026, 1, 3), "Paycheck", 2500.00],
    ],
)
_MAPPING = {"date": "Date", "description": "Memo", "amount": "Amount"}


def test_parse_xlsx_columns_and_date():
    columns, rows = svc.parse_xlsx(_XLSX)
    assert columns == ["Date", "Memo", "Amount"]
    assert len(rows) == 2
    # Excel datetime → ISO date (time dropped).
    assert rows[0]["Date"] == "2026-01-02"
    assert rows[0]["Memo"] == "Coffee Shop"
    # A whole-number float renders without the ``.0`` so the amount parses cleanly.
    assert rows[1]["Amount"] == "2500"


def test_detect_headers():
    assert svc.detect_headers(_XLSX) == ["Date", "Memo", "Amount"]


def test_parse_empty_raises():
    with pytest.raises(svc.XlsxImportError):
        svc.parse_xlsx(b"")


def test_parse_non_xlsx_raises():
    with pytest.raises(svc.XlsxImportError):
        svc.parse_xlsx(b"Date,Memo,Amount\n2026-01-01,x,1\n")


class _FakeEncryptedOffice:
    """Stands in for ``msoffcrypto.OfficeFile`` — a real password-protected xlsx is an
    OLE/CFB container the msoffcrypto package doesn't offer a way to author in a test, so the
    encryption/decryption boundary is faked at the seam ``services.office_crypt`` calls."""

    def __init__(self, _f):
        pass

    def is_encrypted(self):
        return True

    def load_key(self, password):
        if password != "secret":
            raise ValueError("bad key")

    def decrypt(self, out):
        out.write(_XLSX)


def test_parse_xlsx_encrypted_without_password_raises_password_required(monkeypatch):
    from backend.services import office_crypt

    monkeypatch.setattr(office_crypt.msoffcrypto, "OfficeFile", _FakeEncryptedOffice)
    with pytest.raises(svc.XlsxImportError) as exc:
        svc.parse_xlsx(b"encrypted-bytes-no-password")
    assert str(exc.value).startswith("password_required:")


def test_parse_xlsx_encrypted_wrong_password_raises_distinct_error(monkeypatch):
    from backend.services import office_crypt

    monkeypatch.setattr(office_crypt.msoffcrypto, "OfficeFile", _FakeEncryptedOffice)
    with pytest.raises(svc.XlsxImportError) as exc:
        svc.parse_xlsx(b"encrypted-bytes", password="wrong")
    assert "Wrong password" in str(exc.value)
    assert not str(exc.value).startswith("password_required:")


def test_parse_xlsx_encrypted_correct_password_decrypts_and_parses(monkeypatch):
    from backend.services import office_crypt

    monkeypatch.setattr(office_crypt.msoffcrypto, "OfficeFile", _FakeEncryptedOffice)
    columns, rows = svc.parse_xlsx(b"encrypted-bytes", password="secret")
    assert columns == ["Date", "Memo", "Amount"]
    assert rows[0]["Memo"] == "Coffee Shop"


def test_detect_headers_threads_password_through(monkeypatch):
    from backend.services import office_crypt

    monkeypatch.setattr(office_crypt.msoffcrypto, "OfficeFile", _FakeEncryptedOffice)
    assert svc.detect_headers(b"encrypted-bytes", password="secret") == [
        "Date",
        "Memo",
        "Amount",
    ]


def test_commit_books_income_and_expense(checking):
    res = svc.commit_import(_XLSX, _MAPPING, account_id=checking["id"])
    assert res == {"created": 2, "duplicates": 0, "skipped": 0, "total_rows": 2}

    from backend.services import ledger as ledger_service

    txns = ledger_service.transactions.all()
    paycheck = next(t for t in txns if t["description"] == "Paycheck")
    coffee = next(t for t in txns if t["description"] == "Coffee Shop")
    assert paycheck["kind"] == "income" and paycheck["to_amount"] == 2500.0
    assert paycheck["to_currency"] == "USD"
    assert coffee["kind"] == "expense" and coffee["from_amount"] == 4.50
    # The drafts carry the xlsx namespace, not csv.
    assert all(primary_source(t) == "xlsx" for t in txns)
    assert all(t["source"] == "xlsx_import" for t in txns)


def test_inflow_outflow_mode(checking):
    xlsx = _make_xlsx(
        ["Date", "Memo", "In", "Out"],
        [
            [datetime.datetime(2026, 2, 1), "Salary", 1000.0, ""],
            [datetime.datetime(2026, 2, 2), "Rent", "", 800.0],
        ],
    )
    res = svc.commit_import(
        xlsx,
        {"date": "Date", "description": "Memo", "inflow": "In", "outflow": "Out"},
        account_id=checking["id"],
    )
    assert res["created"] == 2
    from backend.services import ledger as ledger_service

    txns = ledger_service.transactions.all()
    assert next(t for t in txns if t["description"] == "Salary")["kind"] == "income"
    assert next(t for t in txns if t["description"] == "Rent")["kind"] == "expense"


def test_reimport_creates_zero_duplicates(checking):
    first = svc.commit_import(_XLSX, _MAPPING, account_id=checking["id"])
    assert first["created"] == 2
    second = svc.commit_import(_XLSX, _MAPPING, account_id=checking["id"])
    assert second["created"] == 0
    assert second["duplicates"] == 2

    from backend.services import ledger as ledger_service

    assert len(ledger_service.transactions.all()) == 2


def test_malformed_rows_skipped(checking):
    xlsx = _make_xlsx(
        ["Date", "Memo", "Amount"],
        [
            [datetime.datetime(2026, 3, 1), "Good", -4.5],
            ["", "No date", -9.99],  # missing date → skip
        ],
    )
    res = svc.commit_import(xlsx, _MAPPING, account_id=checking["id"])
    assert res["created"] == 1
    assert res["skipped"] == 1
    assert res["total_rows"] == 2


def test_preview_writes_nothing(checking):
    from backend.services import ledger as ledger_service

    prev = svc.preview_import(_XLSX, _MAPPING, account_id=checking["id"])
    assert prev.importable == 2
    assert prev.total_rows == 2
    assert prev.columns == ["Date", "Memo", "Amount"]
    assert ledger_service.transactions.all() == []


def test_preview_sample_shape(checking):
    prev = svc.preview_import(_XLSX, _MAPPING, account_id=checking["id"])
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


def test_bad_mapping_raises(checking):
    with pytest.raises(svc.ImportError_):
        svc.preview_import(_XLSX, {"date": "Date"}, account_id=checking["id"])  # no amount


def test_format_detection():
    from backend.services import import_common

    # Explicit + extension.
    assert import_common.detect_format("xlsx", None, b"") == "xlsx"
    assert import_common.detect_format(None, "ledger.xlsx", b"") == "xlsx"
    # No extension → ZIP/OOXML content sniff.
    assert import_common.detect_format(None, None, _XLSX) == "xlsx"
    # A plain ZIP (not a workbook) is NOT mistaken for XLSX.
    assert import_common.detect_format(None, None, b"PK\x03\x04plain-zip-no-workbook") == "csv"
    # A CSV is NOT mistaken for XLSX.
    assert import_common.detect_format(None, None, b"Date,Amount\n2026-01-01,5\n") == "csv"


def test_looks_like_xlsx():
    assert svc._looks_like_xlsx(_XLSX) is True
    assert svc._looks_like_xlsx(b"not a zip") is False
    assert svc._looks_like_xlsx(b"PK\x03\x04random") is False
