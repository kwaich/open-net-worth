"""A vest parked awaiting release counts in the rebuilt history (synthetic data only).

Live 2026-10-07: 488.75 shares vested on 15 Sep were parked on the grant's pending position
(no lot until release, by design), so today's net worth counted them and every month of the
net-worth history did not — 30 Sep read about SGD 25.7K low.
"""

from __future__ import annotations

from backend.db.position_store import PositionStore
from backend.db.vest_event_store import VestEventStore
from backend.services import portfolio_history as ph


def test_parked_units_count_from_the_vest_date_until_release(db_isolated):
    pending = PositionStore().create(
        {"instrument_id": "inst-acme", "account_id": "acc-plan", "quantity": 40}
    )
    VestEventStore().append(
        {
            "grant_id": "g-1",
            "tranche_id": "t-1",
            "date": "2026-09-15",
            "units": 40,
            "pending_release": True,
            "pending_position_id": pending["id"],
        }
    )
    released = {"date": "2026-06-15", "units": 25, "pending_release": False}
    VestEventStore().append({**released, "grant_id": "g-1", "tranche_id": "t-0"})

    parked = ph.parked_vest_events()

    assert parked == [("2026-09-15", 40.0, "inst-acme")]  # a released vest has its own lot
    kw = {"inception": "2026-01-01", "lots": [], "disposals": [], "parked": parked}
    assert ph.holdings_as_of("2026-09-14", **kw) == {}
    assert ph.holdings_as_of("2026-09-30", **kw) == {"inst-acme": 40.0}
    series = ph.holdings_series("2026-08-01", "2026-09-30", "monthly", **kw)
    assert [pt["holdings"] for pt in series] == [{}, {"inst-acme": 40.0}]


def test_injected_lots_without_parked_keep_the_old_pure_behaviour():
    lots = [
        {"instrument_id": "i", "position_id": "p", "original_qty": 5, "acquired_at": "2026-02-01"}
    ]
    held = ph.holdings_as_of(
        "2026-03-01",
        inception="2026-01-01",
        lots=lots,
        disposals=[],
        locked_position_ids=frozenset(),
    )
    assert held == {"i": 5.0}


def test_a_parked_vest_is_an_acquisition_flow_not_a_market_gain(db_isolated, monkeypatch):
    """Live 2026-10-07: after the holdings fix the parked KSTL shares appeared in the value
    series with no matching flow, so September's return read +9.6% and KSTL +SGD 24.6K."""
    from backend.db.instrument_store import InstrumentStore
    from backend.services import returns

    inst = InstrumentStore().create(
        {"symbol": "ACME", "asset_class": "equity", "currency": "EUR", "quote_currency": "EUR"}
    )
    pending = PositionStore().create(
        {"instrument_id": inst["id"], "account_id": "a", "quantity": 40}
    )
    VestEventStore().append(
        {
            "grant_id": "g-1",
            "tranche_id": "t-1",
            "date": "2026-09-15",
            "units": 40,
            "fmv_per_unit": 36.75,
            "fmv_currency": "EUR",
            "pending_release": True,
            "pending_position_id": pending["id"],
        }
    )

    flows, unknown = returns.tagged_cash_flows(
        "2026-09-01", "2026-09-30", "EUR", rates={"EUR": 1.0}
    )

    assert flows == [("2026-09-15", -1470.0, inst["id"])]  # 40 × 36.75, invested at vest
    assert unknown is False
