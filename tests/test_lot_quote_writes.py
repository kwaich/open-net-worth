"""Acquire-side cash debit, lot amend, dispose_amend, first-hold find-or-create.

ADR 2026-09-11 — optional funding_account_id on acquire; sale edits undo+rebook.
"""

from __future__ import annotations

import pytest

from backend.core import fx as core_fx
from backend.db.account_store import AccountStore
from backend.db.instrument_store import InstrumentStore
from backend.db.lot_store import LotStore
from backend.db.position_store import PositionStore
from backend.db.realized_event_store import RealizedEventStore
from backend.db.transaction_store import TransactionStore
from backend.services import ledger as ledger_service
from backend.services import lots as svc


@pytest.fixture
def isolated(tmp_path, monkeypatch, db_isolated):
    monkeypatch.setattr(ledger_service, "accounts", AccountStore())
    monkeypatch.setattr(ledger_service, "transactions", TransactionStore())
    monkeypatch.setattr(svc, "_load_rates", lambda: {"USD": 1.0})
    monkeypatch.setattr(svc, "base_currency", lambda: "USD")
    monkeypatch.setattr(ledger_service, "base_currency", lambda: "USD")
    monkeypatch.setattr(core_fx, "base_currency", lambda: "USD")
    monkeypatch.setattr(core_fx, "_fx_rates_loader", lambda: {"USD": 1.0})
    yield tmp_path


def _seed_position(cost_currency="USD"):
    return PositionStore().create(
        {
            "instrument_id": "inst-1",
            "account_id": "acc-1",
            "quantity": 0,
            "cost_currency": cost_currency,
        }
    )


def _cash():
    return AccountStore().create(
        {"name": "Brokerage cash", "kind": "asset", "type": "investment", "currency": "USD"}
    )


def _live_txns():
    return [t for t in TransactionStore().all() if not t.get("deleted")]


def test_acquire_with_funding_debits_gross(isolated):
    pos = _seed_position()
    cash = _cash()
    out = svc.apply_lot_change(
        {
            "type": "acquire",
            "position_id": pos["id"],
            "quantity": 10,
            "unit_cost": 100,
            "fees": 5,
            "cost_currency": "USD",
            "acquired_at": "2026-01-01",
            "funding_account_id": cash["id"],
        }
    )
    assert out["ok"] is True
    assert out["cash_leg_id"]
    dest = AccountStore().find_by_id(cash["id"])
    txns = _live_txns()
    assert (
        ledger_service.account_balance(dest, txns, ledger_service.opening_date(dest, txns))
        == -1005.0
    )
    lot = LotStore().find_by_id(out["lot_id"])
    assert lot["txn_id"] == out["cash_leg_id"]
    leg = txns[0]
    assert leg["kind"] == "investment"
    assert leg["from_account_id"] == cash["id"]
    assert leg["to_account_id"] is None
    assert leg["from_amount"] == 1005.0
    assert leg["date"] == "2026-01-01"


def test_acquire_skip_funding_books_no_cash(isolated):
    pos = _seed_position()
    out = svc.apply_lot_change(
        {
            "type": "acquire",
            "position_id": pos["id"],
            "quantity": 10,
            "unit_cost": 100,
            "acquired_at": "2026-01-01",
        }
    )
    assert out["ok"] is True
    assert out.get("cash_leg_id") is None
    assert _live_txns() == []


def test_acquire_unknown_cost_skips_cash_leg(isolated):
    pos = _seed_position()
    cash = _cash()
    out = svc.apply_lot_change(
        {
            "type": "acquire",
            "position_id": pos["id"],
            "quantity": 10,
            "unit_cost": None,
            "acquired_at": "2026-01-01",
            "funding_account_id": cash["id"],
        }
    )
    assert out["ok"] is True
    assert out.get("cash_leg_id") is None
    assert _live_txns() == []


def test_delete_lot_reverses_funding_cash(isolated):
    pos = _seed_position()
    cash = _cash()
    out = svc.apply_lot_change(
        {
            "type": "acquire",
            "position_id": pos["id"],
            "quantity": 10,
            "unit_cost": 50,
            "acquired_at": "2026-01-01",
            "funding_account_id": cash["id"],
        }
    )
    deleted = svc.apply_lot_change({"type": "delete", "lot_id": out["lot_id"]})
    assert deleted["ok"] is True
    assert LotStore().find_by_id(out["lot_id"]) is None
    dest = AccountStore().find_by_id(cash["id"])
    txns = TransactionStore().all()
    assert (
        ledger_service.account_balance(dest, txns, ledger_service.opening_date(dest, txns)) == 0.0
    )


def test_amend_fully_open_lot_rewrites_qty_and_cash(isolated):
    pos = _seed_position()
    cash = _cash()
    out = svc.apply_lot_change(
        {
            "type": "acquire",
            "position_id": pos["id"],
            "quantity": 10,
            "unit_cost": 100,
            "acquired_at": "2026-01-01",
            "funding_account_id": cash["id"],
        }
    )
    amended = svc.apply_lot_change(
        {
            "type": "amend",
            "lot_id": out["lot_id"],
            "quantity": 8,
            "unit_cost": 100,
            "funding_account_id": cash["id"],
            "acquired_at": "2026-01-02",
        }
    )
    assert amended["ok"] is True
    lot = LotStore().find_by_id(out["lot_id"])
    assert lot["original_qty"] == 8
    assert lot["remaining_qty"] == 8
    assert lot["acquired_at"] == "2026-01-02"
    dest = AccountStore().find_by_id(cash["id"])
    txns = _live_txns()
    assert (
        ledger_service.account_balance(dest, txns, ledger_service.opening_date(dest, txns))
        == -800.0
    )


def test_amend_partial_lot_corrects_it_but_never_below_what_was_sold(isolated):
    """A sold lot is correctable (2026-10-13 — it used to be frozen); its quantity can't
    drop below the units a sale already took."""
    pos = _seed_position()
    svc.apply_lot_change(
        {
            "type": "acquire",
            "position_id": pos["id"],
            "quantity": 10,
            "unit_cost": 100,
            "acquired_at": "2026-01-01",
        }
    )
    lot = LotStore().lots_for_position(pos["id"])[0]
    svc.apply_lot_change(
        {
            "type": "dispose",
            "position_id": pos["id"],
            "quantity": 4,
            "proceeds_per_unit": 130,
            "disposed_at": "2026-06-01",
        }
    )
    out = svc.apply_lot_change({"type": "amend", "lot_id": lot["id"], "quantity": 12})
    assert out["ok"] is True
    fixed = LotStore().find_by_id(lot["id"])
    assert (fixed["original_qty"], fixed["remaining_qty"]) == (12, 8)
    too_few = svc.apply_lot_change({"type": "amend", "lot_id": lot["id"], "quantity": 3})
    assert "already been sold" in too_few["error"]
    note_only = svc.apply_lot_change({"type": "amend", "lot_id": lot["id"], "note": "kept"})
    assert note_only["ok"] is True
    assert LotStore().find_by_id(lot["id"])["note"] == "kept"


def test_dispose_amend_updates_gain_and_rolls_back_on_oversell(isolated):
    pos = _seed_position()
    svc.apply_lot_change(
        {
            "type": "acquire",
            "position_id": pos["id"],
            "quantity": 10,
            "unit_cost": 100,
            "acquired_at": "2026-01-01",
        }
    )
    sold = svc.apply_lot_change(
        {
            "type": "dispose",
            "position_id": pos["id"],
            "quantity": 4,
            "proceeds_per_unit": 130,
            "disposed_at": "2026-06-01",
        }
    )
    assert sold["realized_gain"] == 120.00
    amended = svc.apply_lot_change(
        {
            "type": "dispose_amend",
            "event_id": sold["event_id"],
            "quantity": 4,
            "proceeds_per_unit": 150,
            "disposed_at": "2026-06-01",
        }
    )
    assert amended["ok"] is True
    assert amended["event_id"] != sold["event_id"]
    assert amended["realized_gain"] == 200.00  # 4 × (150 − 100)
    assert RealizedEventStore().find_by_id(sold["event_id"]) is None
    assert RealizedEventStore().find_by_id(amended["event_id"])

    from backend.core.errors import InsufficientLots

    with pytest.raises(InsufficientLots):
        svc.apply_lot_change(
            {
                "type": "dispose_amend",
                "event_id": amended["event_id"],
                "quantity": 99,
                "proceeds_per_unit": 150,
                "disposed_at": "2026-06-01",
            }
        )
    # Oversell rolled back — the amended sale is still the live event, remaining qty 6.
    assert RealizedEventStore().find_by_id(amended["event_id"])
    pos_after = PositionStore().find_by_id(pos["id"])
    assert pos_after["quantity"] == 6.0


def test_first_hold_find_or_create_reuses_sold_out_position(isolated):
    inst = InstrumentStore().create(
        {"symbol": "PL", "price_source_id": "PL", "quote_currency": "USD", "asset_class": "equity"}
    )
    acc = AccountStore().create(
        {"name": "IBKR", "kind": "asset", "type": "investment", "currency": "USD"}
    )
    first = svc.apply_lot_change(
        {
            "type": "acquire",
            "instrument_id": inst["id"],
            "account_id": acc["id"],
            "quantity": 10,
            "unit_cost": 20,
            "acquired_at": "2026-01-01",
        }
    )
    assert first["ok"] is True
    pos_id = first["position_id"]
    svc.apply_lot_change(
        {
            "type": "dispose",
            "position_id": pos_id,
            "quantity": 10,
            "proceeds_per_unit": 25,
            "disposed_at": "2026-06-01",
        }
    )
    reopened = svc.apply_lot_change(
        {
            "type": "acquire",
            "instrument_id": inst["id"],
            "account_id": acc["id"],
            "quantity": 3,
            "unit_cost": 22,
            "acquired_at": "2026-07-01",
        }
    )
    assert reopened["position_id"] == pos_id
    assert PositionStore().find_by_id(pos_id)["quantity"] == 3.0
    assert len(PositionStore().query_positions(instrument_id=inst["id"], account_id=acc["id"])) == 1
