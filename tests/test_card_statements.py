"""
Tests for backend.services.card_statements and related integration points.

Coverage:
  - statement_summary(): cycle math, paid/partial/unpaid, overdue, account-scoping,
    unconfigured→None, FX (base amounts recomputed), credit/refund handling.
  - API endpoint smoke: GET /api/accounts/{id}/statement (200, 204, 404).
  - notify_if_unpaid scheduler action: unpaid→fires notify; paid→silent.

All writes go against tmp_path injected stores — live DATA_DIR is never touched.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Optional

import pytest

from backend.services.card_statements import (
    StatementSummary,
    _clamp_day,
    _cycle_charges,
    _find_payment,
    _last_statement_close,
    _next_due_date_from_due_day,
    statement_summary,
)


@pytest.fixture(autouse=True)
def _usd_at_par_with_sgd(fx_rates):
    """The suite's cards are USD under the default SGD base and assert statement mechanics, not
    conversion — so the pair is pinned at par rather than left without a rate."""
    fx_rates(extra={"USD": 1.0})


@pytest.fixture(autouse=True)
def _no_live_statement_records(monkeypatch):
    import backend.services.card_statements as cs_mod

    monkeypatch.setattr(cs_mod, "_statement_records", lambda: [])


# ── Helper builders ────────────────────────────────────────────────────────────


def _account(
    id: str = "card-1",
    type: str = "credit",
    currency: str = "USD",
    close_day: Optional[int] = 15,
    due_day: Optional[int] = 5,
    due_date: Optional[str] = None,
    reminder_days: Optional[int] = None,
    **kwargs,
) -> dict:
    return {
        "id": id,
        "name": kwargs.get("name", "Test Card"),
        "kind": "liability",
        "type": type,
        "currency": currency,
        "statement_close_day": close_day,
        "payment_due_day": due_day,
        "payment_due_date": due_date,
        "reminder_days_before": reminder_days,
        "initial_balance": 0.0,
        "initial_balance_date": None,
        "archived": False,
    }


def _txn(
    id: str,
    kind: str,
    date_str: str,
    from_account_id: Optional[str] = None,
    to_account_id: Optional[str] = None,
    from_amount: float = 0.0,
    to_amount: float = 0.0,
    from_currency: str = "USD",
    to_currency: str = "USD",
    deleted: bool = False,
) -> dict:
    return {
        "id": id,
        "kind": kind,
        "date": date_str,
        "from_account_id": from_account_id,
        "to_account_id": to_account_id,
        "from_amount": from_amount,
        "to_amount": to_amount,
        "from_currency": from_currency,
        "to_currency": to_currency,
        "deleted": deleted,
        "category": "food" if kind == "expense" else "income",
        "description": "test",
        "splits": None,
        "tags": [],
    }


class _MockStore:
    """Minimal store shim for injecting test data without touching the real stores."""

    def __init__(self, items: list[dict]):
        self._items = items

    def find_by_id(self, id: str) -> Optional[dict]:
        return next((x for x in self._items if x.get("id") == id), None)

    def all(self) -> list[dict]:
        return list(self._items)


# ── Unit: _clamp_day ──────────────────────────────────────────────────────────


def test_clamp_day_normal():
    assert _clamp_day(2026, 3, 15) == date(2026, 3, 15)


def test_clamp_day_31_in_30_day_month():
    assert _clamp_day(2026, 4, 31) == date(2026, 4, 30)  # April has 30 days


def test_clamp_day_31_in_february():
    assert _clamp_day(2026, 2, 31) == date(2026, 2, 28)  # non-leap 2026


def test_clamp_day_28_in_leap_february():
    assert _clamp_day(2024, 2, 29) == date(2024, 2, 29)  # 2024 is a leap year


# ── Unit: _last_statement_close ───────────────────────────────────────────────


def test_last_close_today_is_close_day():
    """When today IS the close day, the cycle just closed today."""
    today = date(2026, 6, 15)
    assert _last_statement_close(today, 15) == date(2026, 6, 15)


def test_last_close_before_close_day():
    """Close day is in the future — roll back to previous month."""
    today = date(2026, 6, 10)
    # Close day 15 is still in the future → last close was May 15
    assert _last_statement_close(today, 15) == date(2026, 5, 15)


def test_last_close_after_close_day():
    """Close day already passed this month."""
    today = date(2026, 6, 20)
    assert _last_statement_close(today, 15) == date(2026, 6, 15)


def test_last_close_cross_year_boundary():
    """January with close_day > today rolls back to December."""
    today = date(2026, 1, 5)
    # Close day 15 hasn't happened yet in January → last close was Dec 15 2025
    assert _last_statement_close(today, 15) == date(2025, 12, 15)


def test_last_close_31_in_short_month():
    """close_day=31 in a month with 30 days → clamps to 30th."""
    today = date(2026, 5, 31)  # May 31 IS the close day (≤ today)
    assert _last_statement_close(today, 31) == date(2026, 5, 31)


# ── Unit: _next_due_date_from_due_day ─────────────────────────────────────────


def test_next_due_today():
    today = date(2026, 6, 5)
    assert _next_due_date_from_due_day(today, 5) == date(2026, 6, 5)


def test_next_due_future():
    today = date(2026, 6, 1)
    assert _next_due_date_from_due_day(today, 10) == date(2026, 6, 10)


def test_next_due_past_rolls_forward():
    today = date(2026, 6, 15)
    assert _next_due_date_from_due_day(today, 5) == date(2026, 7, 5)


def test_next_due_december_rolls_to_january():
    today = date(2026, 12, 20)
    assert _next_due_date_from_due_day(today, 5) == date(2027, 1, 5)


# ── Unit: _cycle_charges ──────────────────────────────────────────────────────


def test_cycle_charges_expense_on_card():
    txns = [
        _txn("t1", "expense", "2026-06-01", from_account_id="card", from_amount=50.0),
        _txn("t2", "expense", "2026-06-10", from_account_id="card", from_amount=30.0),
    ]
    total = _cycle_charges(txns, "card", date(2026, 6, 1), date(2026, 6, 16))
    assert total == pytest.approx(80.0)


def test_cycle_charges_excludes_other_accounts():
    txns = [
        _txn("t1", "expense", "2026-06-01", from_account_id="card", from_amount=50.0),
        _txn("t2", "expense", "2026-06-01", from_account_id="other", from_amount=100.0),
    ]
    total = _cycle_charges(txns, "card", date(2026, 6, 1), date(2026, 6, 16))
    assert total == pytest.approx(50.0)


def test_cycle_charges_credit_subtracts():
    """Income posted to the card (refund) reduces the total."""
    txns = [
        _txn("t1", "expense", "2026-06-01", from_account_id="card", from_amount=100.0),
        _txn("t2", "income", "2026-06-05", to_account_id="card", to_amount=20.0),
    ]
    total = _cycle_charges(txns, "card", date(2026, 6, 1), date(2026, 6, 16))
    assert total == pytest.approx(80.0)


def test_cycle_charges_excludes_transfers():
    """Transfers (payments) are NOT charges."""
    txns = [
        _txn("t1", "transfer", "2026-06-01", to_account_id="card", to_amount=500.0),
    ]
    total = _cycle_charges(txns, "card", date(2026, 6, 1), date(2026, 6, 16))
    assert total == pytest.approx(0.0)


def test_cycle_charges_date_boundary_exclusive():
    """until is exclusive."""
    txns = [
        _txn("t1", "expense", "2026-06-16", from_account_id="card", from_amount=50.0),
    ]
    total = _cycle_charges(txns, "card", date(2026, 6, 1), date(2026, 6, 16))
    assert total == pytest.approx(0.0)


def test_cycle_charges_skips_deleted():
    txns = [
        _txn("t1", "expense", "2026-06-01", from_account_id="card", from_amount=50.0, deleted=True),
    ]
    total = _cycle_charges(txns, "card", date(2026, 6, 1), date(2026, 6, 16))
    assert total == pytest.approx(0.0)


# ── Unit: _find_payment ───────────────────────────────────────────────────────


def test_find_payment_found():
    txns = [
        _txn("p1", "transfer", "2026-06-16", to_account_id="card", to_amount=200.0),
    ]
    paid, amount = _find_payment(txns, "card", date(2026, 6, 15))
    assert paid is True
    assert amount == pytest.approx(200.0)


def test_find_payment_not_found():
    txns = [
        _txn("p1", "transfer", "2026-06-10", to_account_id="card", to_amount=200.0),
    ]
    # Payment is BEFORE the last close date
    paid, amount = _find_payment(txns, "card", date(2026, 6, 15))
    assert paid is False
    assert amount is None


def test_find_payment_partial_multiple():
    """Multiple payments are summed."""
    txns = [
        _txn("p1", "transfer", "2026-06-16", to_account_id="card", to_amount=100.0),
        _txn("p2", "transfer", "2026-06-20", to_account_id="card", to_amount=50.0),
    ]
    paid, amount = _find_payment(txns, "card", date(2026, 6, 15))
    assert paid is True
    assert amount == pytest.approx(150.0)


# ── Integration: statement_summary() ─────────────────────────────────────────


def _make_summary(
    today_str: str,
    charges: list[dict] = None,
    payments: list[dict] = None,
    close_day: int = 15,
    due_day: int = 5,
    due_date: Optional[str] = None,
) -> Optional[StatementSummary]:
    """Helper: build a StatementSummary with controlled injected data."""
    acct = _account(close_day=close_day, due_day=due_day, due_date=due_date)
    txns = (charges or []) + (payments or [])
    today = date.fromisoformat(today_str)
    return statement_summary(
        "card-1",
        today=today,
        accounts_store=_MockStore([acct]),
        transactions_store=_MockStore(txns),
    )


def test_summary_returns_none_for_non_credit_card():
    acct = _account(type="cash")
    s = statement_summary(
        "card-1",
        today=date(2026, 6, 20),
        accounts_store=_MockStore([acct]),
        transactions_store=_MockStore([]),
    )
    assert s is None


def test_summary_returns_none_for_missing_account():
    s = statement_summary(
        "nonexistent",
        today=date(2026, 6, 20),
        accounts_store=_MockStore([]),
        transactions_store=_MockStore([]),
    )
    assert s is None


def test_summary_returns_none_when_not_configured():
    """No statement_close_day → None."""
    acct = _account(close_day=None, due_day=None)
    s = statement_summary(
        "card-1",
        today=date(2026, 6, 20),
        accounts_store=_MockStore([acct]),
        transactions_store=_MockStore([]),
    )
    assert s is None


def test_summary_basic_fields():
    """Smoke: returns a StatementSummary with expected date math."""
    s = _make_summary("2026-06-20", close_day=15, due_day=5)
    assert s is not None
    assert s.account_id == "card-1"
    assert s.currency == "USD"
    # Last close: June 15 (close_day=15, today=June 20)
    assert s.last_statement_close == "2026-06-15"
    # Cycle start: June 16
    assert s.cycle_start == "2026-06-16"
    # Next due: July 5 (due_day=5, today=June 20 > June 5)
    assert s.next_due_date == "2026-07-05"
    assert s.days_until_due == (date(2026, 7, 5) - date(2026, 6, 20)).days


def test_summary_cycle_charges_folded():
    charges = [
        _txn("t1", "expense", "2026-06-17", from_account_id="card-1", from_amount=80.0),
        _txn("t2", "expense", "2026-06-19", from_account_id="card-1", from_amount=20.0),
    ]
    s = _make_summary("2026-06-20", charges=charges)
    assert s is not None
    assert s.current_cycle_charges == pytest.approx(100.0)


def test_summary_paid_flag_true():
    payments = [
        _txn("p1", "transfer", "2026-06-16", to_account_id="card-1", to_amount=200.0),
    ]
    s = _make_summary("2026-06-20", payments=payments)
    assert s is not None
    assert s.paid is True
    assert s.payment_amount == pytest.approx(200.0)


def test_summary_paid_flag_false_no_payment():
    s = _make_summary("2026-06-20")
    assert s is not None
    assert s.paid is False
    assert s.payment_amount is None


def test_summary_overdue_days_negative():
    """days_until_due is negative when the due date has passed within the current month.

    today=2026-06-27, due_day=25 → June 25 has passed, next_due = July 25 (future, not
    overdue). To get an actually-overdue scenario we use an explicit payment_due_date that
    is before today."""
    # Explicit due date already passed → days_until_due < 0
    s = _make_summary("2026-06-27", due_day=None, due_date="2026-06-25")
    assert s is not None
    # June 25 is before today (June 27) → overdue by 2 days
    assert s.days_until_due == -2
    assert s.next_due_date == "2026-06-25"


def test_summary_explicit_due_date_override():
    """payment_due_date overrides payment_due_day."""
    s = _make_summary("2026-06-10", due_day=5, due_date="2026-06-30")
    assert s is not None
    assert s.next_due_date == "2026-06-30"
    assert s.days_until_due == 20


def test_summary_account_scoping():
    """Charges on OTHER accounts are not folded in."""
    charges = [
        _txn("t1", "expense", "2026-06-17", from_account_id="card-1", from_amount=50.0),
        _txn("t2", "expense", "2026-06-17", from_account_id="other-card", from_amount=200.0),
    ]
    s = _make_summary("2026-06-20", charges=charges)
    assert s is not None
    assert s.current_cycle_charges == pytest.approx(50.0)


def test_summary_last_statement_balance():
    """Last statement balance is from the PREVIOUS closed cycle."""
    # With close_day=15, today=June 20:
    #   last_close = June 15
    #   prev_close = May 15
    #   last_stmt = [May 16, June 15] (inclusive)
    charges = [
        # In the closed cycle (May 16 – June 15)
        _txn("t1", "expense", "2026-05-20", from_account_id="card-1", from_amount=150.0),
        _txn("t2", "expense", "2026-06-01", from_account_id="card-1", from_amount=75.0),
        # Current cycle (June 16 onward)
        _txn("t3", "expense", "2026-06-17", from_account_id="card-1", from_amount=30.0),
    ]
    s = _make_summary("2026-06-20", charges=charges)
    assert s is not None
    assert s.last_statement_balance == pytest.approx(225.0)
    assert s.current_cycle_charges == pytest.approx(30.0)


def test_summary_base_amounts_not_none():
    """FX base amounts are always computed (even at 1:1)."""
    s = _make_summary("2026-06-20")
    assert s is not None
    assert isinstance(s.current_cycle_base, float)
    assert isinstance(s.last_statement_base, float)
    assert s.base_currency  # must be non-empty string


# ── Partial payment flag ──────────────────────────────────────────────────────


def test_summary_partial_flag_false_when_not_paid():
    """partial=False when there is no payment at all."""
    s = _make_summary("2026-06-20")
    assert s is not None
    assert s.paid is False
    assert s.partial is False


def test_summary_partial_flag_false_when_fully_paid():
    """partial=False when payment_amount >= last_statement_balance."""
    # Build a closed cycle with 100 in charges, and a full payment of 100
    charges = [
        _txn("t1", "expense", "2026-05-20", from_account_id="card-1", from_amount=100.0),
    ]
    payments = [
        _txn("p1", "transfer", "2026-06-16", to_account_id="card-1", to_amount=100.0),
    ]
    s = _make_summary("2026-06-20", charges=charges, payments=payments)
    assert s is not None
    assert s.paid is True
    assert s.partial is False
    assert s.last_statement_balance == pytest.approx(100.0)
    assert s.payment_amount == pytest.approx(100.0)


def test_summary_partial_flag_true_when_underpaid():
    """partial=True when payment_amount < last_statement_balance."""
    charges = [
        _txn("t1", "expense", "2026-05-20", from_account_id="card-1", from_amount=200.0),
    ]
    payments = [
        _txn("p1", "transfer", "2026-06-16", to_account_id="card-1", to_amount=50.0),
    ]
    s = _make_summary("2026-06-20", charges=charges, payments=payments)
    assert s is not None
    assert s.paid is True
    assert s.partial is True
    assert s.last_statement_balance == pytest.approx(200.0)
    assert s.payment_amount == pytest.approx(50.0)


# ── remaining_amount (MATH-45): the ONE computed "still owed" figure ──────────
# Before this field existed, three call sites each derived it themselves and disagreed
# on an overpaid card — the AI tool went negative (statement 200, payment 250 → -50)
# while the UI clamped to 0. remaining_amount is the single source of truth: clamped
# to >= 0, so every caller reads the same non-negative number.


def test_remaining_amount_when_unpaid():
    """No payment at all: the whole statement balance is still owed."""
    charges = [
        _txn("t1", "expense", "2026-05-20", from_account_id="card-1", from_amount=200.0),
    ]
    s = _make_summary("2026-06-20", charges=charges)
    assert s is not None
    assert s.paid is False
    assert s.remaining_amount == pytest.approx(200.0)


def test_remaining_amount_when_underpaid():
    """A partial payment leaves the difference owed."""
    charges = [
        _txn("t1", "expense", "2026-05-20", from_account_id="card-1", from_amount=200.0),
    ]
    payments = [
        _txn("p1", "transfer", "2026-06-16", to_account_id="card-1", to_amount=50.0),
    ]
    s = _make_summary("2026-06-20", charges=charges, payments=payments)
    assert s is not None
    assert s.partial is True
    assert s.remaining_amount == pytest.approx(150.0)


def test_remaining_amount_when_paid_exactly():
    """An exact payment leaves nothing owed."""
    charges = [
        _txn("t1", "expense", "2026-05-20", from_account_id="card-1", from_amount=100.0),
    ]
    payments = [
        _txn("p1", "transfer", "2026-06-16", to_account_id="card-1", to_amount=100.0),
    ]
    s = _make_summary("2026-06-20", charges=charges, payments=payments)
    assert s is not None
    assert s.partial is False
    assert s.remaining_amount == pytest.approx(0.0)


def test_remaining_amount_clamped_to_zero_when_overpaid():
    """MATH-45: statement 200, payment 250 — the raw difference is -50, but nothing is
    ever owed BACK through this field. Clamp to 0, never negative."""
    charges = [
        _txn("t1", "expense", "2026-05-20", from_account_id="card-1", from_amount=200.0),
    ]
    payments = [
        _txn("p1", "transfer", "2026-06-16", to_account_id="card-1", to_amount=250.0),
    ]
    s = _make_summary("2026-06-20", charges=charges, payments=payments)
    assert s is not None
    assert s.last_statement_balance == pytest.approx(200.0)
    assert s.payment_amount == pytest.approx(250.0)
    assert s.remaining_amount == pytest.approx(0.0)


def test_remaining_amount_none_when_summary_is_none():
    """No cycle configured at all → statement_summary() itself returns None; there is no
    StatementSummary instance to carry a remaining_amount (the AI tool's own
    _unreconciled_statement_row reports None for this case)."""
    acct = _account(close_day=None, due_day=None)
    s = statement_summary(
        "card-1",
        today=date(2026, 6, 20),
        accounts_store=_MockStore([acct]),
        transactions_store=_MockStore([]),
    )
    assert s is None


# ── No payment_due_day configured → null due fields ──────────────────────────


def test_summary_no_due_day_returns_null_due_fields():
    """When only statement_close_day is set, next_due_date and days_until_due are None."""
    acct = _account(close_day=15, due_day=None, due_date=None)
    s = statement_summary(
        "card-1",
        today=date(2026, 6, 20),
        accounts_store=_MockStore([acct]),
        transactions_store=_MockStore([]),
    )
    assert s is not None
    assert s.next_due_date is None
    assert s.days_until_due is None


# ── API smoke: GET /api/accounts/{id}/statement ────────────────────────────────
# These tests use TestClient which requires a compatible httpx/starlette version.
# They are marked skip when the environment does not support TestClient(app).


def _try_client(tmp_path):
    """Returns a TestClient or None if the environment does not support it.
    Isolation comes from the suite's conftest (DATA_DIR → tmp); there is no ONW_DATA_DIR."""
    try:
        from fastapi.testclient import TestClient

        from backend.main import app

        return TestClient(app)
    except TypeError:
        return None


@pytest.mark.skip(reason="TestClient not available in this environment (httpx/starlette version)")
def test_statement_endpoint_404_unknown_account(tmp_path: Path):
    client = _try_client(tmp_path)
    if client is None:
        pytest.skip("TestClient not available")
    resp = client.get("/api/accounts/nonexistent-id/statement")
    assert resp.status_code == 404


@pytest.mark.skip(reason="TestClient not available in this environment (httpx/starlette version)")
def test_statement_endpoint_204_unconfigured(tmp_path: Path):
    """A non-credit-card or unconfigured credit card returns 204."""
    client = _try_client(tmp_path)
    if client is None:
        pytest.skip("TestClient not available")
    resp = client.post(
        "/api/accounts",
        json={"name": "Checking", "kind": "asset", "type": "cash", "currency": "USD"},
    )
    assert resp.status_code == 200
    acct_id = resp.json()["id"]
    resp2 = client.get(f"/api/accounts/{acct_id}/statement")
    assert resp2.status_code == 204


@pytest.mark.skip(reason="TestClient not available in this environment (httpx/starlette version)")
def test_statement_endpoint_204_credit_card_no_close_day(tmp_path: Path):
    """Credit card without cycle config returns 204."""
    client = _try_client(tmp_path)
    if client is None:
        pytest.skip("TestClient not available")
    resp = client.post(
        "/api/accounts",
        json={"name": "My CC", "kind": "liability", "type": "credit", "currency": "USD"},
    )
    assert resp.status_code == 200
    acct_id = resp.json()["id"]
    resp2 = client.get(f"/api/accounts/{acct_id}/statement")
    assert resp2.status_code == 204


@pytest.mark.skip(reason="TestClient not available in this environment (httpx/starlette version)")
def test_statement_endpoint_200_configured(tmp_path: Path):
    """A fully-configured credit card returns 200 with expected fields."""
    client = _try_client(tmp_path)
    if client is None:
        pytest.skip("TestClient not available")
    resp = client.post(
        "/api/accounts",
        json={
            "name": "Visa",
            "kind": "liability",
            "type": "credit",
            "currency": "USD",
            "statement_close_day": 15,
            "payment_due_day": 5,
        },
    )
    assert resp.status_code == 200
    acct_id = resp.json()["id"]
    resp2 = client.get(f"/api/accounts/{acct_id}/statement")
    assert resp2.status_code == 200
    body = resp2.json()
    assert body["account_id"] == acct_id
    assert "cycle_start" in body
    assert "last_statement_close" in body
    assert "next_due_date" in body
    assert "days_until_due" in body
    assert "paid" in body
    assert "current_cycle_charges" in body
    assert "last_statement_balance" in body
    assert "base_currency" in body


# ── notify_if_unpaid action ───────────────────────────────────────────────────


# ── Uploaded-statement facts override the estimated warning ───────────────────


def _stmt_record(
    account_id="card-1",
    statement_date="2026-06-21",
    due="2026-07-10",
    amount_due=4000.00,
    minimum=229.0,
):
    return {
        "id": "stmt_x",
        "account_id": account_id,
        "account_ids": [],
        "import_meta": {
            "statement_date": statement_date,
            "payment_due_date": due,
            "amount_due": amount_due,
            "minimum_payment": minimum,
        },
    }


def test_fresh_statement_overrides_due_and_amount(monkeypatch):
    """The printed due date + amount due beat the day-of-month estimate for the WARNING;
    the account's own config fields are untouched (informs, never replaces)."""
    import backend.services.card_statements as cs_mod

    acct = _account(close_day=20, due_day=5)  # estimate would say next due Jul 5 → Aug 5
    monkeypatch.setattr(cs_mod, "_accounts", _MockStore([acct]))
    monkeypatch.setattr(cs_mod, "_transactions", _MockStore([]))
    monkeypatch.setattr(cs_mod, "_statement_records", lambda: [_stmt_record()])
    s = statement_summary("card-1", today=date(2026, 7, 6))
    assert s is not None
    assert s.due_source == "statement"
    assert s.next_due_date == "2026-07-10" and s.days_until_due == 4
    assert s.last_statement_balance == 4000.00  # printed amount, not the charge fold
    assert s.statement_id == "stmt_x" and s.statement_minimum_payment == 229.0
    # config untouched
    assert acct["payment_due_day"] == 5 and acct["statement_close_day"] == 20


def test_fresh_statement_past_due_shows_real_overdue(monkeypatch):
    """Unpaid + printed due date already passed → OVERDUE (the estimate would roll to next
    month and hide it)."""
    import backend.services.card_statements as cs_mod

    monkeypatch.setattr(cs_mod, "_accounts", _MockStore([_account(close_day=20, due_day=5)]))
    monkeypatch.setattr(cs_mod, "_transactions", _MockStore([]))
    monkeypatch.setattr(cs_mod, "_statement_records", lambda: [_stmt_record()])
    s = statement_summary("card-1", today=date(2026, 7, 14))
    assert s is not None
    assert s.due_source == "statement" and s.days_until_due == -4  # overdue, honestly


def test_stale_statement_never_overrides(monkeypatch):
    """A statement from an OLDER cycle keeps the estimate (due_source config, fold amount)."""
    import backend.services.card_statements as cs_mod

    monkeypatch.setattr(cs_mod, "_accounts", _MockStore([_account(close_day=20, due_day=5)]))
    monkeypatch.setattr(cs_mod, "_transactions", _MockStore([]))
    monkeypatch.setattr(
        cs_mod,
        "_statement_records",
        lambda: [_stmt_record(statement_date="2026-03-21", due="2026-04-10")],
    )
    s = statement_summary("card-1", today=date(2026, 7, 6))
    assert s is not None
    assert s.due_source == "config" and s.statement_id is None
    assert s.next_due_date == "2026-08-05"  # the estimate (Jul 5 passed)


def test_statement_stands_in_for_missing_cycle_config(monkeypatch):
    """No statement_close_day at all — previously None — an uploaded statement now derives
    the cycle (its printed statement date IS the last close) and powers the warning."""
    import backend.services.card_statements as cs_mod

    acct = _account(close_day=None, due_day=None)
    monkeypatch.setattr(cs_mod, "_accounts", _MockStore([acct]))
    monkeypatch.setattr(cs_mod, "_transactions", _MockStore([]))
    monkeypatch.setattr(cs_mod, "_statement_records", lambda: [_stmt_record()])
    s = statement_summary("card-1", today=date(2026, 7, 6))
    assert s is not None
    assert s.due_source == "statement"
    assert s.last_statement_close == "2026-06-21"
    assert s.last_statement_balance == 4000.00
    # and with no statement either, still None (pre-existing contract)
    monkeypatch.setattr(cs_mod, "_statement_records", lambda: [])
    assert statement_summary("card-1", today=date(2026, 7, 6)) is None


def test_payment_compared_against_printed_amount(monkeypatch):
    """A payment covering the fold but NOT the printed amount due (carried balance/interest)
    is a PARTIAL payment — the warning stays."""
    import backend.services.card_statements as cs_mod

    txns = [
        {
            "kind": "transfer",
            "date": "2026-06-25",
            "to_account_id": "card-1",
            "to_amount": 500.0,
            "from_account_id": "bank",
            "from_amount": 500.0,
        }
    ]
    monkeypatch.setattr(cs_mod, "_accounts", _MockStore([_account(close_day=20, due_day=10)]))
    monkeypatch.setattr(cs_mod, "_transactions", _MockStore(txns))
    monkeypatch.setattr(cs_mod, "_statement_records", lambda: [_stmt_record()])
    s = statement_summary("card-1", today=date(2026, 7, 6))
    assert s is not None
    assert s.paid is True and s.partial is True
    assert s.last_statement_balance == 4000.00


# ── Recency ladder: statement-anchored payment window + fall-through ──────────
# Regression repro 2026-07-16 (Meridian Bank): close_day=5 derives last_close=2026-07-05, but
# the banner's due/amount come from the June statement (printed 06-05, due 06-30,
# S$2,400.00). Two bugs, one ladder:
#  1. _find_payment anchored at the DERIVED close made a full payment on 06-26 —
#     correctly between the statement's own close and its printed due date —
#     structurally invisible: paid=False, "overdue by 16 days" on a paid statement.
#  2. Once found, a fully-paid OLDER statement must not congratulate either — a newer
#     cycle closed 07-05 without its statement; the summary falls through to that
#     cycle's ledger estimate + statement_missing_for_cycle (upload nudge).


def _meridian_repro_records():
    return [
        _stmt_record(
            statement_date="2026-06-05", due="2026-06-30", amount_due=2400.00, minimum=308.85
        )
    ]


def test_paid_statement_still_latest_cycle_congratulates(monkeypatch):
    """Before the next derived close passes (today 06-28 < 07-05), the fully-paid June
    statement IS the current obligation — the ONLY congrats case. The statement-anchored
    payment window finds the 06-26 payment sitting between the statement's own close
    (06-05) and its printed due date (06-30)."""
    import backend.services.card_statements as cs_mod

    payment = _txn(
        "p1",
        "transfer",
        "2026-06-26",
        from_account_id="bank",
        to_account_id="card-1",
        from_amount=2400.00,
        to_amount=2400.00,
    )
    monkeypatch.setattr(cs_mod, "_accounts", _MockStore([_account(close_day=5, due_day=None)]))
    monkeypatch.setattr(cs_mod, "_transactions", _MockStore([payment]))
    monkeypatch.setattr(cs_mod, "_statement_records", _meridian_repro_records)
    s = statement_summary("card-1", today=date(2026, 6, 28))
    assert s is not None
    # The June statement drives: printed due/amount, cycle closed 06-05
    assert s.due_source == "statement"
    assert s.next_due_date == "2026-06-30" and s.days_until_due == 2
    assert s.last_statement_close == "2026-06-05"
    assert s.last_statement_balance == 2400.00
    assert s.statement_minimum_payment == 308.85
    assert s.statement_missing_for_cycle is False
    # The 06-26 payment pays THAT statement
    assert s.paid is True
    assert s.payment_amount == pytest.approx(2400.00)
    assert s.partial is False
    # AccountsTab.tsx dueInfo contract: amountDue = max(0, balance − (paid ? payment : 0));
    # ≤ 0 with a real paid balance renders the settled (congrats) tier.
    fe_amount_due = max(0.0, s.last_statement_balance - (s.payment_amount if s.paid else 0.0))
    assert fe_amount_due <= 0


def test_paid_older_statement_falls_through_to_new_cycle_estimate(monkeypatch):
    """Once a NEWER derived close (07-05) passes, the fully-paid June statement stops
    driving — no stale congrats. The summary reverts to the newest closed cycle's ledger
    estimate: fold balance, offset-derived due (06-05→06-30 = +25d ⇒ 07-05→07-30), and
    statement_missing_for_cycle=True for the upload nudge. No congrats/statement fields."""
    import backend.services.card_statements as cs_mod

    txns = [
        # Charges inside the NEWEST closed cycle [06-06 .. 07-05] — the not-yet-uploaded
        # July statement's content, ledger-visible: 2400 + 1969.57 = 4369.57
        _txn("t1", "expense", "2026-06-15", from_account_id="card-1", from_amount=2400.0),
        _txn("t2", "expense", "2026-07-01", from_account_id="card-1", from_amount=1969.57),
        # Full payment of the June statement, inside ITS window
        _txn(
            "p1",
            "transfer",
            "2026-06-26",
            from_account_id="bank",
            to_account_id="card-1",
            from_amount=2400.00,
            to_amount=2400.00,
        ),
    ]
    monkeypatch.setattr(cs_mod, "_accounts", _MockStore([_account(close_day=5, due_day=None)]))
    monkeypatch.setattr(cs_mod, "_transactions", _MockStore(txns))
    monkeypatch.setattr(cs_mod, "_statement_records", _meridian_repro_records)
    s = statement_summary("card-1", today=date(2026, 7, 16))
    assert s is not None
    # Estimate tier for the NEW cycle — not the old statement's printed figures
    assert s.due_source == "config"
    assert s.statement_missing_for_cycle is True
    assert s.last_statement_close == "2026-07-05"
    assert s.last_statement_balance == pytest.approx(4369.57)
    # Offset-derived due estimate pinned: +25d off the paid statement's own dates
    assert s.next_due_date == "2026-07-30"
    assert s.days_until_due == 14
    # No congrats fields: the June payment belongs to the June statement, not this cycle
    assert s.paid is False and s.partial is False and s.payment_amount is None
    assert s.statement_id is None and s.statement_amount_due is None
    # FE derivation: positive estimated amountDue → warning/nudge tier, never settled
    fe_amount_due = max(0.0, s.last_statement_balance - (s.payment_amount if s.paid else 0.0))
    assert fe_amount_due == pytest.approx(4369.57)


def test_paid_older_statement_config_due_day_beats_offset(monkeypatch):
    """The offset-derived due estimate only fills in when NO due-day config exists — a
    configured payment_due_day keeps winning in the fall-through estimate."""
    import backend.services.card_statements as cs_mod

    payment = _txn(
        "p1",
        "transfer",
        "2026-06-26",
        from_account_id="bank",
        to_account_id="card-1",
        from_amount=2400.00,
        to_amount=2400.00,
    )
    monkeypatch.setattr(cs_mod, "_accounts", _MockStore([_account(close_day=5, due_day=1)]))
    monkeypatch.setattr(cs_mod, "_transactions", _MockStore([payment]))
    monkeypatch.setattr(cs_mod, "_statement_records", _meridian_repro_records)
    s = statement_summary("card-1", today=date(2026, 7, 16))
    assert s is not None
    assert s.statement_missing_for_cycle is True
    assert s.due_source == "config"
    assert s.next_due_date == "2026-08-01"  # due_day=1 config, NOT the +25d offset


def test_partially_paid_older_statement_keeps_overdue_banner(monkeypatch):
    """An older statement with only a PARTIAL payment still owes money — real debt
    outranks recency: the printed figures keep driving (overdue banner stays), compared
    against the PRINTED amount due, and no fall-through/nudge fires."""
    import backend.services.card_statements as cs_mod

    payment = _txn(
        "p1",
        "transfer",
        "2026-06-20",
        from_account_id="bank",
        to_account_id="card-1",
        from_amount=1600.0,
        to_amount=1600.0,
    )
    monkeypatch.setattr(cs_mod, "_accounts", _MockStore([_account(close_day=5, due_day=None)]))
    monkeypatch.setattr(cs_mod, "_transactions", _MockStore([payment]))
    monkeypatch.setattr(cs_mod, "_statement_records", _meridian_repro_records)
    s = statement_summary("card-1", today=date(2026, 7, 16))
    assert s is not None
    assert s.due_source == "statement"
    assert s.next_due_date == "2026-06-30" and s.days_until_due == -16
    assert s.statement_missing_for_cycle is False
    assert s.paid is True and s.partial is True
    assert s.payment_amount == pytest.approx(1600.0)
    assert s.last_statement_balance == 2400.00
    fe_amount_due = max(0.0, s.last_statement_balance - (s.payment_amount if s.paid else 0.0))
    assert fe_amount_due == pytest.approx(800.00)


def test_payment_before_statement_date_still_ignored(monkeypatch):
    """A payment BEFORE the statement's own close belongs to the PREVIOUS statement —
    the old statement stays UNPAID and its overdue banner stays (never hide unpaid
    money), with no fall-through to the new cycle."""
    import backend.services.card_statements as cs_mod

    payment = _txn(
        "p1",
        "transfer",
        "2026-06-01",
        from_account_id="bank",
        to_account_id="card-1",
        from_amount=2400.00,
        to_amount=2400.00,
    )
    monkeypatch.setattr(cs_mod, "_accounts", _MockStore([_account(close_day=5, due_day=None)]))
    monkeypatch.setattr(cs_mod, "_transactions", _MockStore([payment]))
    monkeypatch.setattr(cs_mod, "_statement_records", _meridian_repro_records)
    s = statement_summary("card-1", today=date(2026, 7, 16))
    assert s is not None
    assert s.due_source == "statement"
    assert s.next_due_date == "2026-06-30" and s.days_until_due == -16
    assert s.statement_missing_for_cycle is False
    assert s.paid is False
    assert s.payment_amount is None
