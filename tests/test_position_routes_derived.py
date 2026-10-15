# ruff: noqa: F811  (the shared `isolated` fixture is imported, then requested by name)
"""The REST routes the app itself calls, under derived quantities (synthetic data only).

ADR 2026-10-07-a-position-quantity-is-derived-from-its-records. The service tests prove the
rules; these prove the HTTP layer the frontend actually hits keeps them — the Move dialog's
``PATCH /api/positions/{id}`` (whose lots-left-behind bug filed 23 lots under settlement
wallets) and ``POST /api/positions``, which used to bypass the position choke entirely.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api.lots import router as lots_router
from backend.api.portfolio import router as portfolio_router
from backend.db.lot_store import LotStore
from backend.db.position_store import PositionStore
from backend.services import lots as lots_service
from tests.test_position_figures import _account, _instrument, isolated  # noqa: F401


@pytest.fixture
def client(isolated):
    app = FastAPI()
    app.include_router(portfolio_router)
    app.include_router(lots_router)
    return TestClient(app)


def _create(client, **body) -> dict:
    inst, acct = _instrument(), _account()
    r = client.post(
        "/api/positions",
        json={"instrument_id": inst["id"], "account_id": acct["id"], "quantity": 10, **body},
    )
    return {"response": r, "account": acct}


def test_post_positions_books_the_holdings_lot(client):
    r = _create(client, cost_basis=5.0)["response"]

    assert r.status_code == 200, r.text
    pos = r.json()["position"]
    (lot,) = LotStore().lots_for_position(pos["id"])
    assert (pos["quantity"], lot["kind"], lot["original_qty"]) == (10.0, "opening", 10.0)


def test_post_positions_with_a_date_books_a_buy(client):
    r = _create(client, acquired_at="2025-02-03", cost_basis=5.0)["response"]

    (lot,) = LotStore().lots_for_position(r.json()["position"]["id"])
    assert (lot["kind"], lot["acquired_at"], lot["unit_cost"]) == ("buy", "2025-02-03", 5.0)


@pytest.mark.parametrize(
    "body, fragment",
    [({"quantity": -1}, "positive"), ({"vesting_state": "unvested"}, "grant")],
)
def test_post_positions_refusals_leave_nothing_behind(client, body, fragment):
    r = _create(client, **body)["response"]

    assert r.status_code == 422 and fragment in r.json()["detail"]
    assert PositionStore().all() == [] and LotStore().all() == []


def test_the_move_dialog_moves_every_lot_with_the_position(client):
    made = _create(client, acquired_at="2025-02-03", cost_basis=5.0)
    pid = made["response"].json()["position"]["id"]
    lots_service.apply_lot_change(
        {"type": "acquire", "position_id": pid, "quantity": 2, "acquired_at": "2025-06-01"}
    )
    lots_service.apply_lot_change(
        {
            "type": "dispose",
            "position_id": pid,
            "quantity": 10,
            "proceeds_per_unit": 6.0,
            "proceeds_currency": "SGD",
            "method": "fifo",
            "disposed_at": "2025-07-01",
        }
    )  # the first lot is closed now; it must move too
    broker = _account("Broker")

    r = client.patch(f"/api/positions/{pid}", json={"changes": {"account_id": broker["id"]}})

    assert r.status_code == 200, r.text
    lots = LotStore().lots_for_position(pid, include_closed=True)
    assert len(lots) == 2 and {lot["account_id"] for lot in lots} == {broker["id"]}
    assert PositionStore().find_by_id(pid)["quantity"] == 2.0


@pytest.mark.parametrize("field", ["quantity", "cost_basis", "cost_currency"])
def test_patching_a_figure_is_refused_with_guidance(client, field):
    pid = _create(client)["response"].json()["position"]["id"]

    r = client.patch(f"/api/positions/{pid}", json={"changes": {field: 1}})

    assert r.status_code == 422
    assert "derived from its records" in r.json()["detail"]
    assert PositionStore().find_by_id(pid)["quantity"] == 10.0


def test_patching_a_missing_position_is_404(client):
    r = client.patch("/api/positions/pos_nope", json={"changes": {"quantity": 1}})
    assert r.status_code == 404


def test_viewing_lots_then_selling_works_on_a_route_added_holding(client):
    pid = _create(client, cost_basis=5.0)["response"].json()["position"]["id"]
    before = LotStore().all()

    assert len(client.get(f"/api/positions/{pid}/lots").json()["lots"]) == 1
    assert LotStore().all() == before, "the lots read wrote something"
    r = client.post(
        f"/api/positions/{pid}/disposals",
        json={"quantity": 4, "proceeds_per_unit": 6, "proceeds_currency": "SGD", "method": "fifo"},
    )
    assert r.status_code == 200, r.text
    assert PositionStore().find_by_id(pid)["quantity"] == 6.0
