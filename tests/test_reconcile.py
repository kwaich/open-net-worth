"""
Integration tests for backend/reconcile.py.

Each test:
  1. Loads a real bank statement via parse_upload() — same code path as production.
  2. Calls parse_bank_statement() which uses the LLM.
  3. Verifies structure, counts, and (where possible) balance arithmetic.

ALL tests are marked @pytest.mark.integration and are skipped unless
ANTHROPIC_API_KEY is set in the environment.  Run with:

    cd <project-root>
    ANTHROPIC_API_KEY=sk-ant-... .venv/bin/pytest tests/test_reconcile.py -v

Design principles (per the project's generic-solution requirement):
  - No hardcoded bank names or language-specific assertions.
  - Each test verifies *observable facts* about the statement (count ranges,
    balance math, field validity) rather than internal parsing implementation.
  - Balance checks use known ground-truth values provided by the account owner.
  - Count checks use ±5% tolerance to stay robust against minor LLM variation.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from backend.api.upload import parse_upload
from backend.services.reconcile import diff_against_system, parse_bank_statement

# ── fixtures & helpers ─────────────────────────────────────────────────────

STATEMENTS_DIR = Path(__file__).parent.parent / "test_transactions"

# MIME types for test files
_MIME = {
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".xls": "application/vnd.ms-excel",
    ".pdf": "application/pdf",
    ".csv": "text/csv",
}


def load_statement(filename: str) -> dict:
    """Load a test statement file through parse_upload (same as production upload)."""
    path = STATEMENTS_DIR / filename
    content = path.read_bytes()
    suffix = path.suffix.lower()
    mime = _MIME.get(suffix, "application/octet-stream")
    return parse_upload(filename, mime, content)


def within_pct(actual: int, expected: int, pct: float = 5.0) -> bool:
    """Return True if actual is within pct% of expected."""
    if expected == 0:
        return actual == 0
    return abs(actual - expected) / expected * 100 <= pct


def balance_ok(
    txns: list[dict], opening: float, closing: float, tolerance_pct: float = 0.1
) -> bool:
    """
    Verify: opening + sum(IN) - sum(OUT) ≈ closing.
    Uses a percentage tolerance to allow minor rounding differences.
    """
    total_in = sum(t["amount"] for t in txns if t["direction"] == "IN")
    total_out = sum(t["amount"] for t in txns if t["direction"] == "OUT")
    computed = opening + total_in - total_out
    if closing == 0:
        return abs(computed) / max(abs(opening), 1) * 100 <= tolerance_pct
    return abs(computed - closing) / abs(closing) * 100 <= tolerance_pct


def assert_valid_structure(txns: list[dict], label: str):
    """Each transaction must have date (YYYY-MM-DD), positive amount, and IN/OUT direction."""
    import re

    assert txns, f"{label}: no transactions parsed"
    date_re = re.compile(r"^\d{4}-\d{2}-\d{2}$")
    for i, t in enumerate(txns):
        assert "date" in t, f"{label}[{i}]: missing 'date'"
        assert "amount" in t, f"{label}[{i}]: missing 'amount'"
        assert "direction" in t, f"{label}[{i}]: missing 'direction'"
        assert date_re.match(t["date"]), f"{label}[{i}]: invalid date {t['date']!r}"
        assert isinstance(t["amount"], (int, float)), f"{label}[{i}]: amount not numeric"
        assert t["amount"] > 0, f"{label}[{i}]: amount not positive ({t['amount']})"
        assert t["direction"] in ("IN", "OUT"), (
            f"{label}[{i}]: unknown direction {t['direction']!r}"
        )


def assert_date_range(txns: list[dict], year_month_prefixes: list[str], label: str):
    """All dates must start with one of the given YYYY-MM prefixes."""
    bad = [t["date"] for t in txns if not any(t["date"].startswith(p) for p in year_month_prefixes)]
    assert not bad, f"{label}: dates outside expected range: {bad[:5]}"


# ═══════════════════════════════════════════════════════════════════════════
# 1. Riverside Bank — Excel / CSV format
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.integration
@pytest.mark.asyncio
async def test_riverside_april_structure():
    """Riverside Bank April statement parses cleanly and all fields are valid."""
    upload = load_statement("riverside_april.xlsx")
    txns, _, _, _ = await parse_bank_statement(upload)
    assert_valid_structure(txns, "Riverside Bank April")
    assert_date_range(txns, ["2026-04"], "Riverside Bank April")


@pytest.mark.integration
@pytest.mark.asyncio
async def test_riverside_april_count():
    """Riverside Bank April has exactly 130 transactions (verified against raw xlsx)."""
    upload = load_statement("riverside_april.xlsx")
    txns, _, _, _ = await parse_bank_statement(upload)
    assert within_pct(len(txns), 130), (
        f"Riverside Bank April: expected ~130 transactions, got {len(txns)}"
    )


@pytest.mark.integration
@pytest.mark.asyncio
async def test_riverside_april_balance():
    """
    Riverside Bank April: opening + net transactions = closing.
    Ground truth from account owner.
    """
    upload = load_statement("riverside_april.xlsx")
    txns, _, _, _ = await parse_bank_statement(upload)
    assert balance_ok(txns, opening=1_040_600, closing=185_220_400), (
        f"Riverside Bank April: balance check failed. "
        f"IN={sum(t['amount'] for t in txns if t['direction'] == 'IN'):,.0f}  "
        f"OUT={sum(t['amount'] for t in txns if t['direction'] == 'OUT'):,.0f}"
    )


@pytest.mark.integration
@pytest.mark.asyncio
async def test_riverside_may_structure():
    upload = load_statement("riverside_may.xlsx")
    txns, _, _, _ = await parse_bank_statement(upload)
    assert_valid_structure(txns, "Riverside Bank May")
    assert_date_range(txns, ["2026-05"], "Riverside Bank May")


@pytest.mark.integration
@pytest.mark.asyncio
async def test_riverside_may_count():
    """Riverside Bank May has exactly 245 transactions."""
    upload = load_statement("riverside_may.xlsx")
    txns, _, _, _ = await parse_bank_statement(upload)
    assert within_pct(len(txns), 245), (
        f"Riverside Bank May: expected ~245 transactions, got {len(txns)}"
    )


@pytest.mark.integration
@pytest.mark.asyncio
async def test_riverside_may_balance():
    """Riverside Bank May: opening → closing balance, continuous with April's closing."""
    upload = load_statement("riverside_may.xlsx")
    txns, _, _, _ = await parse_bank_statement(upload)
    assert balance_ok(txns, opening=185_220_400, closing=2_075_900), (
        f"Riverside Bank May: balance check failed. "
        f"IN={sum(t['amount'] for t in txns if t['direction'] == 'IN'):,.0f}  "
        f"OUT={sum(t['amount'] for t in txns if t['direction'] == 'OUT'):,.0f}"
    )


# ═══════════════════════════════════════════════════════════════════════════
# 2. PocketPay — Excel / CSV format with status column
#    The statement has both successful and failed transactions.
#    The parser must exclude failed rows.
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.integration
@pytest.mark.asyncio
async def test_pocketpay_april_structure():
    upload = load_statement("pocketpay_april.xlsx")
    txns, _, _, _ = await parse_bank_statement(upload)
    assert_valid_structure(txns, "PocketPay April")
    assert_date_range(txns, ["2026-04"], "PocketPay April")


@pytest.mark.integration
@pytest.mark.asyncio
async def test_pocketpay_april_excludes_failed():
    """
    PocketPay April has 190 successful and 20 failed transactions.
    The parser must include only the 190 successful ones.
    """
    upload = load_statement("pocketpay_april.xlsx")
    txns, _, _, _ = await parse_bank_statement(upload)
    # If failed rows are included count jumps to 210
    assert len(txns) <= 190 + 5, (
        f"PocketPay April: got {len(txns)} transactions — looks like failed rows are included (expected ≤195)"
    )
    assert within_pct(len(txns), 190, pct=5), (
        f"PocketPay April: expected ~190 successful transactions, got {len(txns)}"
    )


@pytest.mark.integration
@pytest.mark.asyncio
async def test_pocketpay_may_structure():
    upload = load_statement("pocketpay_may.xlsx")
    txns, _, _, _ = await parse_bank_statement(upload)
    assert_valid_structure(txns, "PocketPay May")
    assert_date_range(txns, ["2026-05"], "PocketPay May")


@pytest.mark.integration
@pytest.mark.asyncio
async def test_pocketpay_may_excludes_failed():
    """
    PocketPay May has 320 successful and 40 failed transactions.
    """
    upload = load_statement("pocketpay_may.xlsx")
    txns, _, _, _ = await parse_bank_statement(upload)
    assert len(txns) <= 320 + 5, (
        f"PocketPay May: got {len(txns)} — looks like failed rows included (expected ≤325)"
    )
    assert within_pct(len(txns), 320, pct=5), (
        f"PocketPay May: expected ~320 successful transactions, got {len(txns)}"
    )


# ═══════════════════════════════════════════════════════════════════════════
# 3. Meridian Bank Multiplier — text-extractable PDF, debit/credit columns
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.integration
@pytest.mark.asyncio
async def test_meridian_multiplier_structure():
    upload = load_statement("meridian_multiplier_april.pdf")
    txns, _, _, _ = await parse_bank_statement(upload)
    assert_valid_structure(txns, "Meridian Bank Multiplier")
    assert_date_range(txns, ["2026-04"], "Meridian Bank Multiplier")


@pytest.mark.integration
@pytest.mark.asyncio
async def test_meridian_multiplier_count():
    """Meridian Bank Multiplier April has transactions visible across 5 pages."""
    upload = load_statement("meridian_multiplier_april.pdf")
    txns, _, _, _ = await parse_bank_statement(upload)
    # Manual count from PDF: ~25 transactions
    assert len(txns) >= 20, f"Meridian Bank Multiplier: too few transactions ({len(txns)})"
    assert len(txns) <= 40, (
        f"Meridian Bank Multiplier: suspiciously many transactions ({len(txns)})"
    )


@pytest.mark.integration
@pytest.mark.asyncio
async def test_meridian_multiplier_balance():
    """
    Meridian Bank Multiplier April: opening → closing SGD balance.
    Both values visible in the PDF statement.
    """
    upload = load_statement("meridian_multiplier_april.pdf")
    txns, _, _, _ = await parse_bank_statement(upload)
    assert balance_ok(txns, opening=30_540.22, closing=27_115.09, tolerance_pct=0.5), (
        f"Meridian Bank Multiplier: balance check failed. "
        f"IN={sum(t['amount'] for t in txns if t['direction'] == 'IN'):.2f}  "
        f"OUT={sum(t['amount'] for t in txns if t['direction'] == 'OUT'):.2f}"
    )


# ═══════════════════════════════════════════════════════════════════════════
# 4. Meridian Bank Wallet — text-extractable PDF, CR/DB suffixes
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.integration
@pytest.mark.asyncio
async def test_meridian_wallet_structure():
    upload = load_statement("meridian_wallet_mar-april.pdf")
    txns, _, _, _ = await parse_bank_statement(upload)
    assert_valid_structure(txns, "Meridian Bank Wallet")
    assert_date_range(txns, ["2026-03", "2026-04"], "Meridian Bank Wallet")


@pytest.mark.integration
@pytest.mark.asyncio
async def test_meridian_wallet_count():
    """
    Meridian Bank Wallet Mar–Apr statement is entirely pass-through (top-up + payment pairs).
    Expected: ~34 transactions (17 CR + 17 DB visible in PDF, plus 1 final DB).
    """
    upload = load_statement("meridian_wallet_mar-april.pdf")
    txns, _, _, _ = await parse_bank_statement(upload)
    assert len(txns) >= 25, f"Meridian Bank Wallet: too few transactions ({len(txns)})"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_meridian_wallet_balanced_wallet():
    """
    Meridian Bank Wallet statement shows opening and closing balance of 0.00 SGD
    (pass-through wallet — every top-up is immediately spent).
    """
    upload = load_statement("meridian_wallet_mar-april.pdf")
    txns, _, _, _ = await parse_bank_statement(upload)
    assert balance_ok(txns, opening=0.0, closing=0.0, tolerance_pct=1.0), (
        f"Meridian Bank Wallet: expected net-zero balance. "
        f"IN={sum(t['amount'] for t in txns if t['direction'] == 'IN'):.2f}  "
        f"OUT={sum(t['amount'] for t in txns if t['direction'] == 'OUT'):.2f}"
    )


# ═══════════════════════════════════════════════════════════════════════════
# 5. Meridian Bank Visa Infinite — text-extractable PDF, credit card
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.integration
@pytest.mark.asyncio
async def test_meridian_visa_infinite_structure():
    upload = load_statement("meridian_visa_infinite_april-may.pdf")
    txns, _, _, _ = await parse_bank_statement(upload)
    assert_valid_structure(txns, "Meridian Bank Visa Infinite")
    assert_date_range(txns, ["2026-04", "2026-05"], "Meridian Bank Visa Infinite")


@pytest.mark.integration
@pytest.mark.asyncio
async def test_meridian_visa_infinite_count():
    """Meridian Bank Visa Infinite April–May credit card statement has transactions across multiple pages."""
    upload = load_statement("meridian_visa_infinite_april-may.pdf")
    txns, _, _, _ = await parse_bank_statement(upload)
    assert len(txns) >= 15, f"Meridian Bank Visa Infinite: too few transactions ({len(txns)})"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_meridian_visa_infinite_has_only_charges():
    """
    Credit card statement: the vast majority of transactions should be OUT
    (charges).  Credits (refunds/payments) may appear but should be a minority.
    """
    upload = load_statement("meridian_visa_infinite_april-may.pdf")
    txns, _, _, _ = await parse_bank_statement(upload)
    out_count = sum(1 for t in txns if t["direction"] == "OUT")
    assert out_count >= len(txns) * 0.6, (
        f"Meridian Bank Visa Infinite: expected mostly OUT transactions, got {out_count}/{len(txns)} OUT"
    )


# ═══════════════════════════════════════════════════════════════════════════
# 6. Meridian Bank Women's World Card — text-extractable PDF, credit card
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.integration
@pytest.mark.asyncio
async def test_meridian_wwc_structure():
    upload = load_statement("meridian_wwc_april_may.pdf")
    txns, _, _, _ = await parse_bank_statement(upload)
    assert_valid_structure(txns, "Meridian Bank WWC")
    assert_date_range(txns, ["2026-04", "2026-05"], "Meridian Bank WWC")


@pytest.mark.integration
@pytest.mark.asyncio
async def test_meridian_wwc_count():
    upload = load_statement("meridian_wwc_april_may.pdf")
    txns, _, _, _ = await parse_bank_statement(upload)
    assert len(txns) >= 10, f"Meridian Bank WWC: too few transactions ({len(txns)})"


# ═══════════════════════════════════════════════════════════════════════════
# 7. Meridian Bank Elite — image-only PDF (scanned), requires vision
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.integration
@pytest.mark.asyncio
async def test_meridian_elite_structure():
    """Meridian Bank Elite is a scanned PDF — parsed via vision API."""
    upload = load_statement("meridian_elite_april_may.pdf")
    assert upload["kind"] == "pdf_pages", "Meridian Bank Elite should be detected as image PDF"
    txns, _, _, _ = await parse_bank_statement(upload)
    assert_valid_structure(txns, "Meridian Bank Elite")


@pytest.mark.integration
@pytest.mark.asyncio
async def test_meridian_elite_count():
    upload = load_statement("meridian_elite_april_may.pdf")
    txns, _, _, _ = await parse_bank_statement(upload)
    assert len(txns) >= 5, f"Meridian Bank Elite: too few transactions ({len(txns)})"


# ═══════════════════════════════════════════════════════════════════════════
# 8. Meridian Bank World — image-only PDF (scanned), requires vision
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.integration
@pytest.mark.asyncio
async def test_meridian_world_structure():
    """Meridian Bank World is a scanned PDF — parsed via vision API."""
    upload = load_statement("meridian_world_april_may.pdf")
    assert upload["kind"] == "pdf_pages", "Meridian Bank World should be detected as image PDF"
    txns, _, _, _ = await parse_bank_statement(upload)
    assert_valid_structure(txns, "Meridian Bank World")


@pytest.mark.integration
@pytest.mark.asyncio
async def test_meridian_world_count():
    upload = load_statement("meridian_world_april_may.pdf")
    txns, _, _, _ = await parse_bank_statement(upload)
    assert len(txns) >= 5, f"Meridian Bank World: too few transactions ({len(txns)})"


# ═══════════════════════════════════════════════════════════════════════════
# 9. diff_against_system — unit tests (no LLM)
# ═══════════════════════════════════════════════════════════════════════════


def test_diff_all_missing_when_system_empty():
    """When no system transactions exist, everything from the bank is 'missing'."""
    bank = [
        {"date": "2026-04-01", "amount": 100.0, "direction": "OUT", "description": "Coffee"},
        {"date": "2026-04-02", "amount": 200.0, "direction": "IN", "description": "Salary"},
    ]
    result = diff_against_system(bank, [], account_id="acc-1")
    assert result["matched"] == 0
    assert result["bank_count"] == 2
    assert result["system_count"] == 0
    assert len(result["missing"]) == 2
    assert len(result["extra"]) == 0


def test_diff_all_matched():
    """When system perfectly mirrors the bank statement, nothing is missing or extra."""
    bank = [
        {"date": "2026-04-01", "amount": 100.0, "direction": "OUT", "description": "Coffee"},
    ]
    system = [
        {
            "id": "t1",
            "date": "2026-04-01",
            "deleted": False,
            "from_account_id": "acc-1",
            "from_amount": 100.0,
            "to_account_id": None,
            "to_amount": None,
        }
    ]
    result = diff_against_system(bank, system, account_id="acc-1")
    assert result["matched"] == 1
    assert len(result["missing"]) == 0
    assert len(result["extra"]) == 0


def test_diff_extra_transaction_in_system():
    """A system transaction within the statement range that doesn't match is 'extra'."""
    bank = [
        {"date": "2026-04-01", "amount": 100.0, "direction": "OUT", "description": "Coffee"},
        {"date": "2026-04-30", "amount": 1.0, "direction": "OUT", "description": "marker"},
    ]
    system = [
        {
            "id": "t1",
            "date": "2026-04-01",
            "deleted": False,
            "from_account_id": "acc-1",
            "from_amount": 100.0,
            "to_account_id": None,
            "to_amount": None,
        },
        {
            # Same date range but different amount — no matching bank entry
            "id": "t2",
            "date": "2026-04-15",
            "deleted": False,
            "from_account_id": "acc-1",
            "from_amount": 999.0,
            "to_account_id": None,
            "to_amount": None,
        },
        {
            "id": "t3",
            "date": "2026-04-30",
            "deleted": False,
            "from_account_id": "acc-1",
            "from_amount": 1.0,
            "to_account_id": None,
            "to_amount": None,
        },
    ]
    result = diff_against_system(bank, system, account_id="acc-1")
    assert result["matched"] == 2
    assert len(result["missing"]) == 0
    assert len(result["extra"]) == 1
    assert result["extra"][0]["id"] == "t2"


def test_diff_deleted_transactions_ignored():
    """Soft-deleted system transactions are not counted as matched or extra."""
    bank = [
        {"date": "2026-04-01", "amount": 100.0, "direction": "OUT", "description": "Coffee"},
    ]
    system = [
        {
            "id": "t1",
            "date": "2026-04-01",
            "deleted": True,
            "from_account_id": "acc-1",
            "from_amount": 100.0,
            "to_account_id": None,
            "to_amount": None,
        },
    ]
    result = diff_against_system(bank, system, account_id="acc-1")
    assert result["matched"] == 0
    assert len(result["missing"]) == 1  # deleted = invisible → bank entry is "missing"
    assert len(result["extra"]) == 0


def test_diff_multiset_duplicates():
    """Same (date, direction, amount) appearing twice in bank needs two system entries."""
    bank = [
        {"date": "2026-04-01", "amount": 50.0, "direction": "OUT", "description": "A"},
        {"date": "2026-04-01", "amount": 50.0, "direction": "OUT", "description": "B"},
    ]
    system = [
        {
            "id": "t1",
            "date": "2026-04-01",
            "deleted": False,
            "from_account_id": "acc-1",
            "from_amount": 50.0,
            "to_account_id": None,
            "to_amount": None,
        },
    ]
    result = diff_against_system(bank, system, account_id="acc-1")
    assert result["matched"] == 1
    assert len(result["missing"]) == 1  # second 50.0 not in system
    assert len(result["extra"]) == 0


def test_diff_date_range_filters_system():
    """System transactions outside the bank statement date range are not counted."""
    bank = [
        {"date": "2026-04-15", "amount": 100.0, "direction": "OUT", "description": "Mid-month"},
    ]
    system = [
        {
            "id": "t-before",
            "date": "2026-03-31",
            "deleted": False,
            "from_account_id": "acc-1",
            "from_amount": 100.0,
            "to_account_id": None,
            "to_amount": None,
        },
        {
            "id": "t-after",
            "date": "2026-05-01",
            "deleted": False,
            "from_account_id": "acc-1",
            "from_amount": 100.0,
            "to_account_id": None,
            "to_amount": None,
        },
    ]
    result = diff_against_system(bank, system, account_id="acc-1")
    assert result["system_count"] == 0  # both outside range
    assert len(result["missing"]) == 1
    assert len(result["extra"]) == 0


def test_diff_empty_bank_returns_error():
    result = diff_against_system([], [], account_id="acc-1")
    assert "error" in result


# 10–20. (removed) — the reconcile-internal parser these sections pinned moved to
# services/statements/ (RFC statement-import-unified P1); equivalent coverage lives in
# tests/test_statements_parser.py (locale/date/amount parsing, schema apply, chunking,
# JSON tolerance, vision routing) and tests/test_reconcile_fence.py (fencing + money-path).
# ═══════════════════════════════════════════════════════════════════════════

import asyncio  # noqa: E402


def test_parse_bank_statement_unsupported_kind():
    with pytest.raises(ValueError, match="Unsupported upload kind"):
        asyncio.run(parse_bank_statement({"kind": "docx", "text": "whatever"}))


def test_parse_bank_statement_maps_canonical_rows_and_drops_skips():
    """The delegate keeps reconcile's flat row contract and excludes skip rows."""
    from unittest.mock import patch

    from backend.services.statements.canonical import CanonicalRow, ParseResult, StatementMeta

    rows = [
        CanonicalRow(date="2026-04-01", direction="IN", amount=100.0, description="Deposit"),
        CanonicalRow(date="", direction="", amount=0.0, skip=True, skip_reason="missing date"),
    ]

    meta = StatementMeta()

    async def fake_parse(upload, schema=None):
        return ParseResult(meta=meta, rows=rows, tier="text", reconcile={"status": "ok"})

    with patch("backend.services.reconcile.parse_statement", side_effect=fake_parse):
        result, result_meta, result_reconcile, result_tier = asyncio.run(
            parse_bank_statement({"kind": "text", "text": "x"})
        )
    assert result == [
        {"date": "2026-04-01", "amount": 100.0, "direction": "IN", "description": "Deposit"}
    ]
    assert result_meta is meta
    assert result_reconcile == {"status": "ok"}
    assert result_tier == "text"


# ═══════════════════════════════════════════════════════════════════════════
# 21. diff_against_system — additional edge cases
# ═══════════════════════════════════════════════════════════════════════════


def test_diff_to_account_legs_counted_as_in():
    """When a system transaction has to_account_id == account_id, it's an IN leg."""
    bank = [
        {"date": "2026-04-01", "amount": 500.0, "direction": "IN", "description": "Transfer in"},
    ]
    system = [
        {
            "id": "t1",
            "date": "2026-04-01",
            "deleted": False,
            "from_account_id": "other-acc",
            "from_amount": 500.0,
            "to_account_id": "acc-1",
            "to_amount": 500.0,
        }
    ]
    result = diff_against_system(bank, system, account_id="acc-1")
    assert result["matched"] == 1
    assert result["system_count"] == 1
    assert len(result["missing"]) == 0
    assert len(result["extra"]) == 0


def test_diff_transaction_for_different_account_ignored():
    """System transactions not belonging to the account_id are excluded."""
    bank = [
        {"date": "2026-04-01", "amount": 100.0, "direction": "OUT", "description": "Coffee"},
    ]
    system = [
        {
            "id": "t1",
            "date": "2026-04-01",
            "deleted": False,
            "from_account_id": "other-acc",  # not "acc-1"
            "from_amount": 100.0,
            "to_account_id": None,
            "to_amount": None,
        }
    ]
    result = diff_against_system(bank, system, account_id="acc-1")
    assert result["system_count"] == 0
    assert len(result["missing"]) == 1


def test_diff_amount_rounding_in_key():
    """Matching uses round(amount), so 100.49 and 100.51 both round to 100 and match."""
    bank = [
        {"date": "2026-04-01", "amount": 100.49, "direction": "OUT", "description": "Coffee"},
    ]
    system = [
        {
            "id": "t1",
            "date": "2026-04-01",
            "deleted": False,
            "from_account_id": "acc-1",
            "from_amount": 100.51,
            "to_account_id": None,
            "to_amount": None,
        }
    ]
    result = diff_against_system(bank, system, account_id="acc-1")
    # round(100.49)=100, round(100.51)=101 → these do NOT match
    # The key insight: rounding means amounts differing by <0.5 around the whole match
    # 100.49 → 100, 100.51 → 101 → different keys → no match
    assert result["matched"] == 0
    assert len(result["missing"]) == 1
    assert len(result["extra"]) == 1


def test_diff_amount_cents_not_rounded_to_whole_unit():
    """Regression (MATH-6): a 50.45 bank row and a 50.00 system leg must NOT match —
    whole-unit rounding used to hash them to the same key, hiding a real 0.45
    discrepancy."""
    bank = [
        {"date": "2026-04-01", "amount": 50.45, "direction": "OUT", "description": "Coffee"},
    ]
    system = [
        {
            "id": "t1",
            "date": "2026-04-01",
            "deleted": False,
            "from_account_id": "acc-1",
            "from_amount": 50.00,
            "to_account_id": None,
            "to_amount": None,
        }
    ]
    result = diff_against_system(bank, system, account_id="acc-1")
    assert result["matched"] == 0
    assert len(result["missing"]) == 1
    assert result["missing"][0]["amount"] == 50.45
    assert len(result["extra"]) == 1
    assert result["extra"][0]["id"] == "t1"


def test_diff_amount_exact_cents_still_match():
    """Companion to the MATH-6 regression: identical cent amounts still match."""
    bank = [
        {"date": "2026-04-01", "amount": 50.45, "direction": "OUT", "description": "Coffee"},
    ]
    system = [
        {
            "id": "t1",
            "date": "2026-04-01",
            "deleted": False,
            "from_account_id": "acc-1",
            "from_amount": 50.45,
            "to_account_id": None,
            "to_amount": None,
        }
    ]
    result = diff_against_system(bank, system, account_id="acc-1")
    assert result["matched"] == 1
    assert len(result["missing"]) == 0
    assert len(result["extra"]) == 0


def test_diff_amount_float_noise_still_matches():
    """Companion to the MATH-6 regression: sub-cent float noise still matches, since
    2-dp rounding absorbs it."""
    bank = [
        {"date": "2026-04-01", "amount": 50.449999, "direction": "OUT", "description": "Coffee"},
    ]
    system = [
        {
            "id": "t1",
            "date": "2026-04-01",
            "deleted": False,
            "from_account_id": "acc-1",
            "from_amount": 50.45,
            "to_account_id": None,
            "to_amount": None,
        }
    ]
    result = diff_against_system(bank, system, account_id="acc-1")
    assert result["matched"] == 1
    assert len(result["missing"]) == 0
    assert len(result["extra"]) == 0


def test_diff_date_range_is_inclusive():
    """Bank min/max dates are inclusive — boundary-date system txns are included."""
    bank = [
        {"date": "2026-04-01", "amount": 100.0, "direction": "OUT", "description": "First"},
        {"date": "2026-04-30", "amount": 200.0, "direction": "OUT", "description": "Last"},
    ]
    system = [
        {
            "id": "t1",
            "date": "2026-04-01",  # exactly at date_from
            "deleted": False,
            "from_account_id": "acc-1",
            "from_amount": 100.0,
            "to_account_id": None,
            "to_amount": None,
        },
        {
            "id": "t2",
            "date": "2026-04-30",  # exactly at date_to
            "deleted": False,
            "from_account_id": "acc-1",
            "from_amount": 200.0,
            "to_account_id": None,
            "to_amount": None,
        },
    ]
    result = diff_against_system(bank, system, account_id="acc-1")
    assert result["matched"] == 2
    assert result["system_count"] == 2


def test_diff_missing_sorted_by_date():
    """Missing transactions are returned sorted by date."""
    bank = [
        {"date": "2026-04-10", "amount": 30.0, "direction": "OUT", "description": "C"},
        {"date": "2026-04-01", "amount": 10.0, "direction": "OUT", "description": "A"},
        {"date": "2026-04-05", "amount": 20.0, "direction": "OUT", "description": "B"},
    ]
    result = diff_against_system(bank, [], account_id="acc-1")
    dates = [t["date"] for t in result["missing"]]
    assert dates == sorted(dates)


def test_diff_extra_sorted_by_date():
    """Extra transactions are returned sorted by date."""
    bank = [
        {"date": "2026-04-01", "amount": 1.0, "direction": "OUT", "description": "anchor"},
        {"date": "2026-04-30", "amount": 1.0, "direction": "OUT", "description": "anchor2"},
    ]
    system = [
        {
            "id": "t3",
            "date": "2026-04-20",
            "deleted": False,
            "from_account_id": "acc-1",
            "from_amount": 999.0,
            "to_account_id": None,
            "to_amount": None,
        },
        {
            "id": "t1",
            "date": "2026-04-05",
            "deleted": False,
            "from_account_id": "acc-1",
            "from_amount": 888.0,
            "to_account_id": None,
            "to_amount": None,
        },
    ]
    result = diff_against_system(bank, system, account_id="acc-1")
    dates = [t["date"] for t in result["extra"]]
    assert dates == sorted(dates)


def test_diff_result_includes_date_range_keys():
    """Result dict always includes date_from, date_to, bank_count, system_count."""
    bank = [
        {"date": "2026-04-01", "amount": 100.0, "direction": "OUT", "description": "A"},
        {"date": "2026-04-15", "amount": 200.0, "direction": "IN", "description": "B"},
    ]
    result = diff_against_system(bank, [], account_id="acc-1")
    assert result["date_from"] == "2026-04-01"
    assert result["date_to"] == "2026-04-15"
    assert result["bank_count"] == 2
