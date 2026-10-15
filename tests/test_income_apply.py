"""
Apply-path tests for backend.services.income — the double-counting regression guard (P2-INV2).

Exercises REAL stores in tmp_path (no live data). The critical invariant: recording a CASH
dividend creates EXACTLY ONE kind="income" transaction (no double-count) + one event linking
them; a DRP acquires ONE lot and books ZERO cash txns; idempotency-key re-apply is a no-op;
base figures revalue on a base-currency change.
"""

from __future__ import annotations

import pytest

from backend.db.account_store import AccountStore
from backend.db.income_event_store import IncomeEventStore
from backend.db.instrument_store import InstrumentStore
from backend.db.lot_store import LotStore
from backend.db.position_store import PositionStore
from backend.db.transaction_store import TransactionStore
from backend.services import income as svc
from backend.services import ledger as ledger_service
from backend.services import lots as lots_service


@pytest.fixture
def isolated(tmp_path, monkeypatch, db_isolated):
    """Redirect every backing file into a temp dir + identity USD FX.

    accounts/transactions are DB-backed now (plan 3 task 4); income_events too (plan 4a task
    3) — db_isolated covers all three."""

    # Rebind the module-level store handles that hold a path captured at import time.
    monkeypatch.setattr(svc, "events", IncomeEventStore())
    monkeypatch.setattr(svc, "instruments", InstrumentStore())
    monkeypatch.setattr(svc, "positions", PositionStore())
    monkeypatch.setattr(lots_service, "instruments", InstrumentStore())
    monkeypatch.setattr(ledger_service, "accounts", AccountStore())
    monkeypatch.setattr(ledger_service, "transactions", TransactionStore())

    # Single-base USD ⇒ FX identity (no fx_cache file).
    monkeypatch.setattr(svc, "base_currency", lambda: "USD")
    monkeypatch.setattr(lots_service, "_load_rates", lambda: {"USD": 1.0})
    monkeypatch.setattr(lots_service, "base_currency", lambda: "USD")
    monkeypatch.setattr(ledger_service, "base_currency", lambda: "USD")
    monkeypatch.setattr("backend.core.fx.base_currency", lambda: "USD")
    monkeypatch.setattr(
        "backend.services.ledger.to_base", lambda a, c, b=None, rates=None: round(float(a or 0), 2)
    )
    return tmp_path


def _seed(qty=100, unit_cost=10.0):
    inst = InstrumentStore().create(
        {"symbol": "ABC", "name": "ABC Corp", "quote_currency": "USD", "price_source_id": "ABC"}
    )
    acct = AccountStore().create(
        {"name": "Cash", "kind": "asset", "type": "cash", "currency": "USD"}
    )
    pos = PositionStore().create(
        {
            "instrument_id": inst["id"],
            "account_id": acct["id"],
            "quantity": qty,
            "cost_basis": unit_cost,
            "cost_currency": "USD",
        }
    )
    return inst, acct, pos


# ── the double-counting regression ───────────────────────────────────────────────


def test_cash_dividend_creates_exactly_one_income_txn(isolated):
    inst, acct, pos = _seed()
    out = svc.apply_income_change(
        {
            "type": "record",
            "position_id": pos["id"],
            "gross_amount": 100.0,
            "tax_withheld": 15.0,
            "currency": "USD",
            "account_id": acct["id"],
            "paid_at": "2026-05-01",
        }
    )
    assert out["ok"] is True
    # EXACTLY one transaction, kind=income, tagged dividend, net of tax.
    txns = TransactionStore().all()
    assert len(txns) == 1
    txn = txns[0]
    assert txn["kind"] == "income"
    assert txn["to_amount"] == 85.0  # 100 - 15
    assert "dividend" in (txn.get("tags") or [])
    # Exactly one event, linking the txn.
    events = IncomeEventStore().all()
    assert len(events) == 1
    assert events[0]["txn_id"] == txn["id"]
    assert events[0]["is_drp"] is False
    assert out["txn_id"] == txn["id"]


def _record_dividend(pos, account):
    return svc.apply_income_change(
        {
            "type": "record",
            "position_id": pos["id"],
            "gross_amount": 100.0,
            "tax_withheld": 0.0,
            "currency": "USD",
            "account_id": account,
            "paid_at": "2026-05-01",
        }
    )


def test_cash_dividend_given_the_account_name_stores_its_id(isolated):
    # The ledger leg resolves a name, but the event stored it raw as its "account_id" — the
    # same bug class as a sale's proceeds_account_id (2026-10-13).
    inst, acct, pos = _seed()
    _record_dividend(pos, "cash")
    assert IncomeEventStore().all()[0]["account_id"] == acct["id"]


def test_cash_dividend_refuses_an_unknown_account_before_writing(isolated):
    from backend.core.errors import ValidationError

    inst, acct, pos = _seed()
    with pytest.raises(ValidationError, match="Unknown account 'Nowhere'"):
        _record_dividend(pos, "Nowhere")
    assert IncomeEventStore().all() == []
    assert TransactionStore().all() == []


def test_drp_acquires_lot_books_no_cash(isolated):
    inst, acct, pos = _seed()
    out = svc.apply_income_change(
        {
            "type": "record",
            "position_id": pos["id"],
            "gross_amount": 50.0,
            "tax_withheld": 0.0,
            "currency": "USD",
            "is_drp": True,
            "drp_quantity": 5.0,
            "drp_unit_cost": 10.0,
            "paid_at": "2026-05-01",
        }
    )
    assert out["ok"] is True
    # ZERO cash transactions.
    assert TransactionStore().all() == []
    # One lot acquired, event stamps drp_lot_id, txn_id None.
    lots = LotStore().all()
    assert len(lots) == 1
    ev = IncomeEventStore().all()[0]
    assert ev["is_drp"] is True
    assert ev["drp_lot_id"] == out["drp_lot_id"] == lots[0]["id"]
    assert ev["txn_id"] is None
    # Provenance survives in lots.json: a reinvested dividend is kind="drp", not an
    # ordinary purchase (C6 cleanup) — FIFO/term/double-book treat it exactly like buy.
    assert lots[0]["kind"] == "drp"


def test_idempotency_key_reapply_is_noop(isolated):
    inst, acct, pos = _seed()
    change = {
        "type": "record",
        "position_id": pos["id"],
        "gross_amount": 100.0,
        "currency": "USD",
        "account_id": acct["id"],
        "paid_at": "2026-05-01",
        "idempotency_key": "div-abc",
    }
    first = svc.apply_income_change(change)
    second = svc.apply_income_change(change)
    assert second.get("idempotent") is True
    assert second["event_id"] == first["event_id"]
    # No double-book.
    assert len(TransactionStore().all()) == 1
    assert len(IncomeEventStore().all()) == 1


def test_record_unknown_position_raises(isolated):
    from backend.core.errors import ValidationError

    with pytest.raises(ValidationError):
        svc.apply_income_change({"type": "record", "position_id": "ghost", "gross_amount": 10})


def test_delete_removes_event(isolated):
    inst, acct, pos = _seed()
    out = svc.apply_income_change(
        {
            "type": "record",
            "position_id": pos["id"],
            "gross_amount": 100.0,
            "currency": "USD",
            "account_id": acct["id"],
        }
    )
    svc.apply_income_change({"type": "delete", "event_id": out["event_id"]})
    assert IncomeEventStore().all() == []


def test_base_figures_revalue_on_base_change(isolated, monkeypatch):
    """An event written under USD base reads back in SGD via current FX, not the stored snapshot."""
    inst, acct, pos = _seed()
    svc.apply_income_change(
        {
            "type": "record",
            "position_id": pos["id"],
            "gross_amount": 100.0,
            "currency": "USD",
            "account_id": acct["id"],
            "paid_at": "2026-05-01",
        }
    )
    # Now "change the base" to SGD with 1 USD = 1.5 SGD.
    monkeypatch.setattr(svc, "base_currency", lambda: "SGD")
    monkeypatch.setattr(svc.lots_service, "_load_rates", lambda: {"SGD": 1.0, "USD": 1.5})
    monkeypatch.setattr(svc.positions, "find_by_id", lambda _id: None)
    out = svc.income_summary()
    assert out["base_currency"] == "SGD"
    assert out["total_base"] == pytest.approx(150.0)  # 100 USD × 1.5 (NOT the stored USD snapshot)


def test_drp_identical_reinvestment_is_refused_before_event_append(isolated):
    """Regression (double-book guard): re-recording the same DRP (same position, date,
    qty, unit cost, no idempotency key) must refuse the WHOLE record — no second lot
    and, critically, no dangling income event with drp_lot_id=None."""
    from backend.core.errors import ValidationError

    inst, acct, pos = _seed()
    change = {
        "type": "record",
        "position_id": pos["id"],
        "gross_amount": 50.0,
        "currency": "USD",
        "is_drp": True,
        "drp_quantity": 5.0,
        "drp_unit_cost": 10.0,
        "paid_at": "2026-05-01",
    }
    assert svc.apply_income_change(dict(change))["ok"] is True

    with pytest.raises(ValidationError):
        svc.apply_income_change(dict(change))

    assert len(LotStore().all()) == 1
    assert len(IncomeEventStore().all()) == 1
