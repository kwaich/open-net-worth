"""An investment cash leg names what it traded — from the lot it opened or the realized
event it settled — when the row carries no ticker of its own (both linked by `txn_id`)."""

from backend.db.instrument_store import InstrumentStore
from backend.db.lot_store import LotStore
from backend.db.realized_event_store import RealizedEventStore
from backend.services import labels


def _stores(monkeypatch, lots, events, instruments):
    monkeypatch.setattr(LotStore, "all", lambda self, **_: lots)
    monkeypatch.setattr(RealizedEventStore, "all", lambda self, **_: events)
    monkeypatch.setattr(InstrumentStore, "all", lambda self, **_: instruments)


def test_buy_and_sell_legs_name_their_trade(monkeypatch):
    _stores(
        monkeypatch,
        lots=[
            {"txn_id": "t_buy", "instrument_id": "i_voo", "original_qty": 1.5},
            {"txn_id": "t_buy", "instrument_id": "i_voo", "original_qty": 0.5},
        ],
        events=[{"txn_id": "t_sell", "instrument_id": "i_msft", "quantity": 10}],
        instruments=[{"id": "i_voo", "symbol": "VOO"}, {"id": "i_msft", "symbol": "MSFT"}],
    )
    rows = [
        {"id": "t_buy", "kind": "investment"},
        {"id": "t_sell", "kind": "investment"},
        {"id": "t_own", "kind": "investment", "ticker": "AAPL", "quantity": 3},
        {"id": "t_exp", "kind": "expense"},
    ]
    labels.attach_traded(rows)
    assert (rows[0]["ticker"], rows[0]["quantity"]) == ("VOO", 2.0)
    assert (rows[1]["ticker"], rows[1]["quantity"]) == ("MSFT", 10.0)
    assert (rows[2]["ticker"], rows[2]["quantity"]) == ("AAPL", 3)
    assert "ticker" not in rows[3]


def test_unlinked_leg_is_left_alone(monkeypatch):
    _stores(monkeypatch, lots=[], events=[], instruments=[])
    rows = [{"id": "t1", "kind": "investment"}]
    labels.attach_traded(rows)
    assert rows == [{"id": "t1", "kind": "investment"}]


def test_leg_carries_the_trade_price_weighted_across_fills(monkeypatch):
    _stores(
        monkeypatch,
        lots=[
            {
                "txn_id": "t_buy",
                "instrument_id": "i_voo",
                "original_qty": 1,
                "unit_cost": 500.0,
                "cost_currency": "USD",
            },
            {
                "txn_id": "t_buy",
                "instrument_id": "i_voo",
                "original_qty": 3,
                "unit_cost": 520.0,
                "cost_currency": "USD",
            },
        ],
        events=[
            {
                "txn_id": "t_sell",
                "instrument_id": "i_kstl",
                "quantity": 100,
                "proceeds_per_unit": 37.45,
                "proceeds_currency": "EUR",
            }
        ],
        instruments=[{"id": "i_voo", "symbol": "VOO"}, {"id": "i_kstl", "symbol": "KSTL"}],
    )
    rows = [{"id": "t_buy", "kind": "investment"}, {"id": "t_sell", "kind": "investment"}]
    labels.attach_traded(rows)
    assert (rows[0]["unit_price"], rows[0]["unit_price_currency"]) == (515.0, "USD")
    assert (rows[1]["unit_price"], rows[1]["unit_price_currency"]) == (37.45, "EUR")


def test_no_price_when_a_fill_has_none(monkeypatch):
    _stores(
        monkeypatch,
        lots=[{"txn_id": "t_buy", "instrument_id": "i_voo", "original_qty": 2}],
        events=[],
        instruments=[{"id": "i_voo", "symbol": "VOO"}],
    )
    rows = [{"id": "t_buy", "kind": "investment"}]
    labels.attach_traded(rows)
    assert rows[0]["ticker"] == "VOO" and "unit_price" not in rows[0]


def test_stored_ticker_is_kept_and_the_price_still_comes_from_the_trade(monkeypatch):
    _stores(
        monkeypatch,
        lots=[],
        events=[
            {
                "txn_id": "t_sell",
                "instrument_id": "i_kstl",
                "quantity": 100,
                "proceeds_per_unit": 37.45,
                "proceeds_currency": "EUR",
            }
        ],
        instruments=[{"id": "i_kstl", "symbol": "KSTL"}],
    )
    rows = [{"id": "t_sell", "kind": "investment", "ticker": "KSTL.DE", "quantity": 100}]
    labels.attach_traded(rows)
    assert (rows[0]["ticker"], rows[0]["unit_price"]) == ("KSTL.DE", 37.45)
