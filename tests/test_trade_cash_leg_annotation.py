"""A trade's cash leg STORES what it traded — `ticker` and `quantity` on the ledger row
itself, written at the trade choke — and says it in the description ("Sold 4 KSTL @ 37.45 USD"),
not a bare "Sale proceeds" (found live 2026-10-15: every IBKR sale landed as an anonymous inflow)."""

from __future__ import annotations

import pytest

from backend.core import fx as core_fx
from backend.db.account_store import AccountStore
from backend.db.instrument_store import InstrumentStore
from backend.db.position_store import PositionStore
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


@pytest.fixture
def book(isolated):
    inst = InstrumentStore().create(
        {"symbol": "KSTL", "asset_class": "equity", "quote_currency": "USD"}
    )
    pos = PositionStore().create(
        {"instrument_id": inst["id"], "account_id": "acc-1", "quantity": 0, "cost_currency": "USD"}
    )
    cash = AccountStore().create(
        {"name": "Broker cash", "kind": "asset", "type": "investment", "currency": "USD"}
    )
    return pos, cash


def _live(txn_id):
    row = TransactionStore().find_by_id(txn_id)
    assert row and not row.get("deleted")
    return row


def _buy(pos, cash, qty=10):
    return svc.apply_lot_change(
        {
            "type": "acquire",
            "position_id": pos["id"],
            "quantity": qty,
            "unit_cost": 30,
            "cost_currency": "USD",
            "acquired_at": "2026-01-05",
            "funding_account_id": cash["id"],
        }
    )


def test_purchase_cash_leg_stores_ticker_and_quantity(book):
    pos, cash = book
    out = _buy(pos, cash)
    leg = _live(out["cash_leg_id"])
    assert (leg["ticker"], float(leg["quantity"])) == ("KSTL", 10.0)
    assert leg["description"] == "Bought 10 KSTL @ 30.00 USD"


def test_sale_cash_leg_stores_ticker_and_quantity(book):
    pos, cash = book
    _buy(pos, cash)
    out = svc.apply_lot_change(
        {
            "type": "dispose",
            "position_id": pos["id"],
            "quantity": 4,
            "proceeds_per_unit": 37.45,
            "disposal_fees": 1.5,
            "proceeds_currency": "USD",
            "method": "fifo",
            "disposed_at": "2026-06-01",
            "proceeds_account_id": cash["id"],
        }
    )
    leg = _live(out["cash_leg_id"])
    assert (leg["ticker"], float(leg["quantity"])) == ("KSTL", 4.0)
    assert leg["description"] == "Sold 4 KSTL @ 37.45 USD · fees 1.50 USD"
    assert leg["memo"] == "", "the memo stays free for the owner's own note"


def test_a_corrected_lot_rebooks_its_leg_with_the_new_quantity(book):
    pos, cash = book
    bought = _buy(pos, cash)
    out = svc.apply_lot_change({"type": "amend", "lot_id": bought["lot_id"], "quantity": 12})
    assert out.get("ok") is True
    leg = _live(out["cash_leg_id"])
    assert (leg["ticker"], float(leg["quantity"])) == ("KSTL", 12.0)
    assert leg["description"] == "Bought 12 KSTL @ 30.00 USD"


def test_a_fractional_crypto_style_trade_keeps_its_precision(book):
    pos, cash = book
    out = svc.apply_lot_change(
        {
            "type": "acquire",
            "position_id": pos["id"],
            "quantity": 0.0125,
            "unit_cost": 61234.5678,
            "cost_currency": "USD",
            "acquired_at": "2026-01-05",
            "funding_account_id": cash["id"],
        }
    )
    assert _live(out["cash_leg_id"])["description"] == "Bought 0.0125 KSTL @ 61,234.5678 USD"
