"""get_lots window mode — every lot acquired in a date window, across holdings (synthetic).

Live 2026-10-06: the monthly report listed three of September's trades and could not say what
was bought — get_lots read one position at a time, undated, and the ledger's investment rows
carry no instrument.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from backend.db.instrument_store import InstrumentStore
from backend.db.position_store import PositionStore
from backend.services import lots as lots_svc
from backend.services import lots_window


@pytest.fixture
def book(db_isolated, monkeypatch):
    monkeypatch.setattr(lots_window, "base_currency", lambda: "USD")
    monkeypatch.setattr(lots_window, "load_fx_cache", lambda: {"USD": 1.0, "EUR": 1.10})
    monkeypatch.setattr(lots_svc, "base_currency", lambda: "USD")
    monkeypatch.setattr(lots_svc, "_load_rates", lambda: {"USD": 1.0, "EUR": 1.10})
    inst = InstrumentStore().create(
        {"symbol": "ACME", "name": "Acme Corp", "quote_currency": "USD", "currency": "USD"}
    )
    pos = PositionStore().create(
        {"instrument_id": inst["id"], "account_id": "acc-1", "quantity": 0, "cost_currency": "USD"}
    )
    return pos["id"]


def _acquire(pos_id, qty, unit_cost, when, fees=0.0, kind="buy"):
    res = lots_svc.apply_lot_change(
        {
            "type": "acquire",
            "position_id": pos_id,
            "quantity": qty,
            "unit_cost": unit_cost,
            "cost_currency": "USD",
            "fees": fees,
            "acquired_at": when,
            "kind": kind,
        }
    )
    assert res.get("ok"), res


def test_every_purchase_in_the_window_with_what_was_paid(book):
    _acquire(book, 10, 100.0, "2026-08-20")  # before the window
    _acquire(book, 5, 110.0, "2026-09-03", fees=1.5)
    _acquire(book, 2, 120.0, "2026-09-10")

    out = lots_window.lots_acquired("2026-09-01", "2026-09-30")

    assert out["total"] == 2 and not out["has_more"]
    assert [(lot["acquired_at"], lot["original_qty"], lot["paid_base"]) for lot in out["lots"]] == [
        ("2026-09-03", 5.0, 551.5),  # 5 × 110 + 1.50 fee
        ("2026-09-10", 2.0, 240.0),
    ]


def test_shares_that_arrived_without_money_carry_a_cost_basis_not_a_payment(book):
    """An in-kind transfer between brokers is neither money invested nor a purchase."""
    _acquire(book, 488, 37.74, "2026-09-11", kind="transfer_in")
    _acquire(book, 3, 50.0, "2026-09-12")

    moved, bought = lots_window.lots_acquired("2026-09-01", "2026-09-30")["lots"]

    assert moved["kind"] == "transfer_in"
    assert "paid_base" not in moved and moved["cost_basis_base"] == 18417.12
    assert bought["paid_base"] == 150.0


def test_get_lots_with_dates_and_no_position_is_the_window(book):
    from backend.ai.tools.registry import dispatch

    _acquire(book, 1, 99.0, "2026-09-05")
    text, proposed = asyncio.run(
        dispatch("get_lots", {"date_from": "2026-09-01", "date_to": "2026-09-30"})
    )
    assert proposed is None
    lot = json.loads(text)["lots"][0]
    assert (lot["paid_base"], lot["instrument_ticker"]) == (99.0, "ACME")
