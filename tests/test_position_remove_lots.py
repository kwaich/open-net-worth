"""Removing a position takes the lots only it owned with it (synthetic data only).

Live 2026-10-01: a test gold position was added (which books a buy lot) and later removed —
the position row went, the lot stayed "held", and the net-worth history, portfolio return and
per-holding results kept counting SGD 179K of gold that wasn't there.
"""

from __future__ import annotations

import pytest

from backend.db.instrument_store import InstrumentStore
from backend.db.lot_store import LotStore
from backend.db.position_store import PositionStore


@pytest.fixture
def isolated(tmp_path, monkeypatch, db_isolated):
    return tmp_path


def _add_gold():
    from backend.services import positions as ps

    gold = InstrumentStore().create(
        {
            "symbol": "GLDX",
            "asset_class": "commodity",
            "price_source": "manual",
            "price_source_id": "GLDX",
            "quote_currency": "SGD",
            "currency": "SGD",
        }
    )
    res = ps.apply_position_change(
        {
            "type": "add",
            "instrument_id": gold["id"],
            "account_name": "Test Broker",
            "account_type": "investment",
            "quantity": 10,
            "cost_basis": 100.0,
            "cost_currency": "SGD",
            "acquired_at": "2026-08-25",  # a dated add books its buy lot, as the gold one did
        }
    )
    assert res.get("ok"), res
    return res["position_id"]


def _lots(position_id):
    return [lot for lot in LotStore().all() if lot.get("position_id") == position_id]


def test_removing_a_never_sold_position_removes_its_lot(isolated):
    from backend.services import portfolio_history as ph
    from backend.services import positions as ps

    pid = _add_gold()
    assert len(_lots(pid)) == 1  # adding a position books its buy lot

    res = ps.apply_position_change({"type": "remove", "position_id": pid})

    assert res == {"ok": True, "position_id": pid}
    assert PositionStore().find_by_id(pid) is None
    assert _lots(pid) == []  # no phantom left behind
    assert ph.holdings_as_of("2099-01-01") == {}  # history no longer counts it as held


def test_a_partly_sold_position_is_not_removed_and_nothing_changes(isolated):
    from backend.services import positions as ps

    pid = _add_gold()
    lot = _lots(pid)[0]
    LotStore().update_by_id(lot["id"], {"remaining_qty": 4})  # 6 of 10 sold

    res = ps.apply_position_change({"type": "remove", "position_id": pid})

    assert "partly sold" in res["error"]
    assert PositionStore().find_by_id(pid) is not None
    assert _lots(pid)[0]["remaining_qty"] == 4


def test_a_fully_sold_position_keeps_its_history_when_removed(isolated):
    from backend.services import positions as ps

    pid = _add_gold()
    lot = _lots(pid)[0]
    LotStore().update_by_id(lot["id"], {"remaining_qty": 0})  # all sold: real history

    assert ps.remove_position(pid) is True
    assert PositionStore().find_by_id(pid) is None
    assert [kept["id"] for kept in _lots(pid)] == [lot["id"]]


def test_the_delete_route_refuses_a_partly_sold_position(isolated):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from backend.api.portfolio import router

    pid = _add_gold()
    LotStore().update_by_id(_lots(pid)[0]["id"], {"remaining_qty": 4})
    app = FastAPI()
    app.include_router(router)

    res = TestClient(app).delete(f"/api/positions/{pid}")

    assert res.status_code in (400, 409, 422)
    assert PositionStore().find_by_id(pid) is not None


def _grant_on(position_id, status):
    from backend.db.grant_store import GrantStore

    return GrantStore().insert(
        {"id": f"grant_{status}", "status": status, "locked_position_id": position_id}
    )


def test_a_position_a_live_grant_still_holds_is_not_removed(isolated):
    # 2026-10-13 audit: removal deleted the grant's locked position outright, leaving the
    # grant pointing at nothing — vests then fell back to a dead account/position.
    from backend.services import positions as ps

    pid = _add_gold()
    _grant_on(pid, "active")

    res = ps.apply_position_change({"type": "remove", "position_id": pid})

    assert "equity grant" in res["error"]
    assert PositionStore().find_by_id(pid) is not None
    assert len(_lots(pid)) == 1


def test_a_cancelled_grant_lets_go_of_the_position_it_held(isolated):
    from backend.db.grant_store import GrantStore
    from backend.services import positions as ps

    pid = _add_gold()
    _grant_on(pid, "cancelled")

    res = ps.apply_position_change({"type": "remove", "position_id": pid})

    assert res == {"ok": True, "position_id": pid}
    assert GrantStore().find_by_id("grant_cancelled")["locked_position_id"] is None


def test_adding_a_position_to_an_unknown_account_id_is_refused_not_minted(isolated):
    # 2026-10-13 audit: an unknown account_id silently minted an empty "Brokerage" account and
    # booked the holding there. Only an account_name may create a venue.
    from backend.core.errors import ValidationError
    from backend.db.account_store import AccountStore
    from backend.services import positions as ps

    gold = InstrumentStore().create(
        {"symbol": "GLDY", "price_source": "manual", "quote_currency": "SGD", "currency": "SGD"}
    )
    with pytest.raises(ValidationError, match="Unknown account 'acc_gone'"):
        ps.apply_position_change(
            {
                "type": "add",
                "instrument_id": gold["id"],
                "account_id": "acc_gone",
                "quantity": 1,
            }
        )
    assert AccountStore().all() == []
    assert PositionStore().all() == []


def test_adding_a_position_by_account_name_in_account_id_uses_that_account(isolated):
    from backend.db.account_store import AccountStore
    from backend.services import positions as ps

    acct = AccountStore().create({"name": "Vantage Brokerage", "type": "investment"})
    gold = InstrumentStore().create(
        {"symbol": "GLDZ", "price_source": "manual", "quote_currency": "SGD", "currency": "SGD"}
    )
    res = ps.apply_position_change(
        {
            "type": "add",
            "instrument_id": gold["id"],
            "account_id": "Vantage Brokerage",
            "quantity": 1,
        }
    )
    assert PositionStore().find_by_id(res["position_id"])["account_id"] == acct["id"]
    assert len(AccountStore().all()) == 1
