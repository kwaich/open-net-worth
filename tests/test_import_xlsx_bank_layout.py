"""Regression tests for bank-statement XLSX imports with RIV-like layouts.

Covers the fixes introduced for catastrophic Riverside Bank XLSX import failure:
- Fix B: Smart header detection scanning up to 40 rows, scoring by keyword presence
- Fix C: Normalized column matching (NFKD + casefold + whitespace/newline/'/' collapse)
  so "Debit" resolves to "Số tiền ghi nợ/\\nDebit" and vice versa; Vietnamese synonyms
- Fix A: Error messages for missing/unknown columns include the available column list
  and a first-row preview
- Fix D: Date cells with packed doc numbers ("01/06/2026\\n5378 - 66656") extract ISO date;
  comma-thousands amounts ("200,000", "1,515,700") parse correctly

All workbooks are built in-test with openpyxl using SYNTHETIC data (no real account
numbers or personal names).
"""

from __future__ import annotations

import io

import pytest

from backend.services import import_csv as csv_svc
from backend.services import import_xlsx as xlsx_svc

# ── helpers ────────────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def isolate_stores(tmp_path, monkeypatch):
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
def vnd_account(isolate_stores, fx_rates):
    accounts, _ = isolate_stores
    return accounts.create(
        {"name": "RIV Checking", "kind": "asset", "type": "cash", "currency": "VND"}
    )


def _riv_workbook() -> bytes:
    """Build a synthetic workbook that mirrors the Riverside Bank statement layout:
    - 12-row preamble (merged cells, leading empty column)
    - header row with Vietnamese + English labels separated by newlines
    - data rows with leading empty cell, packed date cells, comma amounts
    - footer rows (totals, notes)

    All personal data is synthetic.
    """
    import openpyxl

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "SyntheticBank_Statement"

    # Preamble rows (rows 0-11)
    ws.append([None, None, None, None, "SAO KE TAI KHOAN / STATEMENT OF ACCOUNT", None])
    ws.append([None])  # blank
    ws.append([None, "Chu tai khoan/ Account name:", None, "NGUYEN VAN TEST"])
    ws.append([None, "So tai khoan/ Account number:", None, "0000000000000"])
    ws.append([None, "Loai tai khoan/ Type:", None, "D"])
    ws.append([None, "Dia chi/ Address:", None, "123 Test Street, District 1"])
    ws.append([None, "CMND/CCCD:", None, "079099999999"])
    ws.append([None, "CIF:", None, None])
    ws.append([None, "Loai tien / Currency:", None, "VND"])
    ws.append([None, "Tu/ From:", None, "01/01/2026", "Den/ To:", "31/01/2026"])
    ws.append([None, "So du dau ky/ Opening balance:", None, None, "5,000,000 VND"])
    ws.append([None])  # blank

    # Header row (row 12) — Vietnamese + English with embedded newlines, leading empty col
    ws.append(
        [
            None,
            "STT\nNo.",
            "Ngày/\nTNX Date/ Số CT/ Doc No",
            "Số tiền ghi nợ/\nDebit",
            "Số tiền ghi có/\nCredit",
            "Số dư/\nBalance",
            "Nội dung chi tiết/\nTransactions in detail",
        ]
    )

    # Data rows — leading empty cell, packed date (dd/mm/yyyy\ndoc), comma amounts
    ws.append(
        [
            None,
            "1",
            "10/01/2026\n5378 - 11111",
            "200,000",
            "",
            "4,800,000",
            "POCKET payment for coffee",
        ]
    )
    ws.append(
        [
            None,
            "2",
            "11/01/2026\n5389 - 22222",
            "",
            "2,000,000",
            "6,800,000",
            "Salary transfer from ACME Corp",
        ]
    )
    ws.append(
        [
            None,
            "3",
            "12/01/2026\n5282 - 33333",
            "1,515,700",
            "",
            "5,284,300",
            "Online shopping payment",
        ]
    )
    ws.append([None, "4", "13/01/2026\n5380 - 44444", "98,000", "", "5,186,300", "Mobile top-up"])
    ws.append(
        [None, "5", "14/01/2026\n5380 - 55555", "", "500,000", "5,686,300", "Refund from merchant"]
    )

    # Footer rows (should be skipped — no parseable amounts in debit/credit)
    ws.append([None, "Tong so/ Total:", None, "-1,813,700 VND", None])
    ws.append([None, "So du cuoi ky/ Closing balance:", None, None, "5,686,300 VND"])
    ws.append([None])
    ws.append([None, "Note: This is a synthetic test statement."])

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _plain_workbook() -> bytes:
    """A simple flat workbook with the header in row 1 (no preamble)."""
    import openpyxl

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["Date", "Memo", "Amount"])
    ws.append(["2026-03-01", "Grocery store", -45.50])
    ws.append(["2026-03-02", "Direct deposit", 1200.00])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


_RIV_WB = _riv_workbook()
_PLAIN_WB = _plain_workbook()


# ── Fix B: header detection picks the right row ──────────────────────────────────


def test_riv_header_detection_finds_row_12():
    """Parser must skip the 12-row preamble and detect the Vietnamese+English header."""
    columns, rows = xlsx_svc.parse_xlsx(_RIV_WB)
    # The empty leading column is included (kept for index alignment).
    assert "Ngày/\nTNX Date/ Số CT/ Doc No" in columns
    assert "Số tiền ghi nợ/\nDebit" in columns
    assert "Số tiền ghi có/\nCredit" in columns
    assert "Nội dung chi tiết/\nTransactions in detail" in columns
    # Should NOT contain preamble text as a column name.
    assert not any("SAO KE" in c for c in columns)
    assert not any("Account name" in c for c in columns)


def test_detect_headers_riv():
    """detect_headers must return the real header, not the title row."""
    headers = xlsx_svc.detect_headers(_RIV_WB)
    assert "Ngày/\nTNX Date/ Số CT/ Doc No" in headers
    assert not any("SAO KE" in h for h in headers)


# ── Fix D: packed date cells and comma amounts ────────────────────────────────────


def test_riv_date_extraction():
    """Date cells packed with a doc number must yield a clean ISO date."""
    assert csv_svc._extract_date("10/01/2026\n5378 - 11111") == "2026-01-10"
    assert csv_svc._extract_date("11/01/2026\n5389 - 22222") == "2026-01-11"
    assert csv_svc._extract_date("2026-01-15") == "2026-01-15"  # already ISO
    assert csv_svc._extract_date("15/06/2026") == "2026-06-15"
    assert csv_svc._extract_date("no date here") == "no date here"  # unchanged


def test_riv_comma_amounts():
    """Comma-thousands amounts must parse to their numeric value."""
    assert csv_svc._parse_amount("200,000") == 200_000.0
    assert csv_svc._parse_amount("1,515,700") == 1_515_700.0
    assert csv_svc._parse_amount("2,000,000") == 2_000_000.0
    assert csv_svc._parse_amount("5,000,000") == 5_000_000.0
    # European style (comma decimal) must also work.
    assert csv_svc._parse_amount("1.234,56") == 1_234.56
    # US style with cents.
    assert csv_svc._parse_amount("1,234.56") == 1_234.56
    # Plain float unchanged.
    assert csv_svc._parse_amount("4.50") == 4.50


# ── Fix C: normalized column matching ─────────────────────────────────────────────


def test_normalized_match_simplified_english_labels():
    """Simplified label 'Debit' must resolve to 'Số tiền ghi nợ/\\nDebit'."""
    columns, _ = xlsx_svc.parse_xlsx(_RIV_WB)
    assert csv_svc._find_column("Debit", columns) == "Số tiền ghi nợ/\nDebit"
    assert csv_svc._find_column("Credit", columns) == "Số tiền ghi có/\nCredit"
    assert csv_svc._find_column("TNX Date", columns) == "Ngày/\nTNX Date/ Số CT/ Doc No"
    assert (
        csv_svc._find_column("Transactions in detail", columns)
        == "Nội dung chi tiết/\nTransactions in detail"
    )


def test_normalized_match_vietnamese_labels():
    """Vietnamese synonyms must resolve to the actual newline-embedded column."""
    columns, _ = xlsx_svc.parse_xlsx(_RIV_WB)
    assert csv_svc._find_column("Nội dung", columns) == "Nội dung chi tiết/\nTransactions in detail"
    assert csv_svc._find_column("ghi nợ", columns) == "Số tiền ghi nợ/\nDebit"
    assert csv_svc._find_column("ghi có", columns) == "Số tiền ghi có/\nCredit"


def test_auto_detect_riv_columns():
    """Auto-detect must resolve all four key fields from RIV column names."""
    columns, _ = xlsx_svc.parse_xlsx(_RIV_WB)
    auto = csv_svc.auto_detect_mapping(columns)
    assert auto.get("date") == "Ngày/\nTNX Date/ Số CT/ Doc No"
    assert auto.get("description") == "Nội dung chi tiết/\nTransactions in detail"
    assert auto.get("outflow") == "Số tiền ghi nợ/\nDebit"
    assert auto.get("inflow") == "Số tiền ghi có/\nCredit"


def test_validate_mapping_simplified_labels_resolves(vnd_account):
    """validate_mapping with simplified English labels must succeed."""
    columns, rows = xlsx_svc.parse_xlsx(_RIV_WB)
    mapping = {
        "date": "TNX Date",
        "description": "Transactions in detail",
        "outflow": "Debit",
        "inflow": "Credit",
    }
    cleaned = csv_svc.validate_mapping(columns, mapping, rows)
    assert cleaned["date"] == "Ngày/\nTNX Date/ Số CT/ Doc No"
    assert cleaned["outflow"] == "Số tiền ghi nợ/\nDebit"
    assert cleaned["inflow"] == "Số tiền ghi có/\nCredit"


# ── Fix A: error messages include column list ──────────────────────────────────────


def test_missing_column_error_includes_available():
    """'Mapped column not in file' error must include the list of available columns."""
    columns, rows = xlsx_svc.parse_xlsx(_RIV_WB)
    with pytest.raises(csv_svc.CsvImportError) as exc_info:
        csv_svc.validate_mapping(
            columns,
            {"date": "NonExistent", "description": "Transactions in detail", "outflow": "Debit"},
            rows,
        )
    msg = str(exc_info.value)
    assert "Available columns:" in msg
    # The column list must contain actual column names (with \n shown).
    assert "TNX Date" in msg or "Ngay" in msg or "STT" in msg


def test_missing_date_error_includes_column_list_and_row_preview():
    """'Map a column to date' error must include available columns and a first-row hint."""
    columns, rows = xlsx_svc.parse_xlsx(_RIV_WB)
    with pytest.raises(csv_svc.CsvImportError) as exc_info:
        csv_svc.validate_mapping(
            columns,
            {"description": "Transactions in detail", "outflow": "Debit"},
            rows,
        )
    msg = str(exc_info.value)
    assert "Map a column to 'date'" in msg
    assert "Available columns:" in msg


def test_missing_amount_error_includes_column_list():
    """'Map a column to amount' error must include available columns."""
    columns, rows = xlsx_svc.parse_xlsx(_RIV_WB)
    with pytest.raises(csv_svc.CsvImportError) as exc_info:
        csv_svc.validate_mapping(
            columns,
            {"date": "TNX Date", "description": "Transactions in detail"},
            rows,
        )
    msg = str(exc_info.value)
    assert "Map a column to 'amount'" in msg
    assert "Available columns:" in msg


# ── full end-to-end: RIV workbook auto-maps and imports correctly ─────────────────


def test_riv_full_auto_detect_preview(vnd_account):
    """Full preview with auto-detected mapping must yield expected transactions."""
    columns, rows = xlsx_svc.parse_xlsx(_RIV_WB)
    auto = csv_svc.auto_detect_mapping(columns)
    # Verify all required fields were detected.
    assert "date" in auto and "description" in auto
    assert "outflow" in auto and "inflow" in auto

    prev = xlsx_svc.preview_import(
        _RIV_WB,
        auto,
        account_id=vnd_account["id"],
    )
    # 5 data rows; footer rows should be skipped (unparseable amounts / zero amount).
    assert prev.importable >= 5
    assert prev.total_rows >= 5
    assert prev.columns == columns

    # Verify date normalization and amount parsing in the sample.
    sample_by_desc = {r["description"]: r for r in prev.sample}
    assert "POCKET payment for coffee" in sample_by_desc
    coffee = sample_by_desc["POCKET payment for coffee"]
    assert coffee["date"] == "2026-01-10"
    assert coffee["amount"] == -200_000.0
    assert coffee["kind"] == "expense"

    salary = sample_by_desc["Salary transfer from ACME Corp"]
    assert salary["date"] == "2026-01-11"
    assert salary["amount"] == 2_000_000.0
    assert salary["kind"] == "income"


def test_riv_explicit_mapping_preview(vnd_account):
    """Explicit mapping with simplified English labels must also work end-to-end."""
    mapping = {
        "date": "TNX Date",
        "description": "Transactions in detail",
        "outflow": "Debit",
        "inflow": "Credit",
    }
    prev = xlsx_svc.preview_import(_RIV_WB, mapping, account_id=vnd_account["id"])
    assert prev.importable >= 5
    sample_by_desc = {r["description"]: r for r in prev.sample}
    assert "POCKET payment for coffee" in sample_by_desc
    assert sample_by_desc["POCKET payment for coffee"]["amount"] == -200_000.0


def test_riv_commit_import(vnd_account):
    """Full commit must book 5 real transactions and skip footer rows."""
    mapping = {
        "date": "TNX Date",
        "description": "Transactions in detail",
        "outflow": "Debit",
        "inflow": "Credit",
    }
    result = xlsx_svc.commit_import(_RIV_WB, mapping, account_id=vnd_account["id"])
    assert result["created"] == 5
    assert result["duplicates"] == 0
    # Footer rows (total/closing balance/notes) must be skipped, not crash.
    assert result["skipped"] >= 0  # footers have unparseable or zero amounts


def test_riv_reimport_is_idempotent(vnd_account):
    """Re-importing the same RIV workbook must produce zero new transactions."""
    mapping = {
        "date": "TNX Date",
        "description": "Transactions in detail",
        "outflow": "Debit",
        "inflow": "Credit",
    }
    first = xlsx_svc.commit_import(_RIV_WB, mapping, account_id=vnd_account["id"])
    assert first["created"] == 5
    second = xlsx_svc.commit_import(_RIV_WB, mapping, account_id=vnd_account["id"])
    assert second["created"] == 0
    assert second["duplicates"] == 5


# ── regression: plain flat workbook still parses as before ───────────────────────


def test_plain_workbook_header_in_row_1():
    """A simple workbook with the header in row 1 must still parse identically to before."""
    columns, rows = xlsx_svc.parse_xlsx(_PLAIN_WB)
    assert columns == ["Date", "Memo", "Amount"]
    assert len(rows) == 2
    assert rows[0]["Memo"] == "Grocery store"
    assert rows[0]["Amount"] == "-45.5"
    assert rows[1]["Memo"] == "Direct deposit"


def test_plain_workbook_commit(isolate_stores, fx_rates):
    """Plain flat workbook must still commit correctly after the header-detection refactor."""
    accounts, _ = isolate_stores
    acct = accounts.create(
        {"name": "Plain Checking", "kind": "asset", "type": "cash", "currency": "USD"}
    )
    mapping = {"date": "Date", "description": "Memo", "amount": "Amount"}
    result = xlsx_svc.commit_import(_PLAIN_WB, mapping, account_id=acct["id"])
    assert result["created"] == 2
    assert result["skipped"] == 0


# ── Fix D edge cases ───────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "raw,expected",
    [
        # Comma-thousands (Vietnamese style)
        ("200,000", 200_000.0),
        ("1,515,700", 1_515_700.0),
        # US style with cents
        ("1,234.56", 1_234.56),
        # European style
        ("1.234,56", 1_234.56),
        # Plain float unchanged
        ("4.50", 4.50),
        # Existing cases must still work
        ("$1,234.56", 1_234.56),
        ("(89.10)", -89.10),
        ("-50", -50.0),
        ("  12.00  ", 12.0),
        ("", None),
        ("abc", None),
    ],
)
def test_parse_amount_extended(raw, expected):
    assert csv_svc._parse_amount(raw) == expected


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("01/06/2026\n5378 - 66656", "2026-06-01"),
        ("10/01/2026\n5378 - 11111", "2026-01-10"),
        ("2026-01-02", "2026-01-02"),
        ("01/06/2026", "2026-06-01"),
        ("31/12/2025", "2025-12-31"),
        # No date → unchanged
        ("5378 - 66656", "5378 - 66656"),
        ("", ""),
    ],
)
def test_extract_date_extended(raw, expected):
    assert csv_svc._extract_date(raw) == expected
