# ruff: noqa: F811  (the shared `isolated` fixture is imported, then requested by name)
"""A lot's acquired date can't be in the future (synthetic data only).

Found 2026-10-12 driving Investments → Add holding on a copy of a real book: a purchase dated
2027-12-31 was accepted and counted in TODAY's quantity. The lot choke now refuses it on every
path — the form's route, Lucius's add, and an edit — before anything is written.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api.lots import router as lots_router
from backend.core.user_time import user_today
from backend.db.lot_store import LotStore
from backend.db.position_store import PositionStore
from backend.services import lots as lots_service
from backend.services import positions as positions_service
from tests.test_position_figures import _account, _instrument, isolated  # noqa: F401

_FUTURE = "2999-01-01"


@pytest.fixture
def client(isolated):
    app = FastAPI()
    app.include_router(lots_router)
    return TestClient(app)


def test_the_add_holding_route_refuses_a_future_date_and_creates_nothing(client):
    inst, acct = _instrument(), _account()

    r = client.post(
        f"/api/instruments/{inst['id']}/lots",
        json={"account_id": acct["id"], "quantity": 1, "unit_cost": 200, "acquired_at": _FUTURE},
    )

    assert r.status_code == 422 and "in the future" in r.text
    assert PositionStore().all() == [] and LotStore().all() == []


def test_today_is_still_a_valid_date(client):
    inst, acct = _instrument(), _account()

    r = client.post(
        f"/api/instruments/{inst['id']}/lots",
        json={
            "account_id": acct["id"],
            "quantity": 1,
            "unit_cost": 200,
            "acquired_at": user_today().isoformat(),
        },
    )

    assert r.status_code == 200, r.text
    assert PositionStore().all()[0]["quantity"] == 1.0


def test_an_add_with_a_future_date_leaves_no_empty_holding(isolated):
    inst, acct = _instrument(), _account()

    try:
        said = str(
            positions_service.apply_position_change(
                {
                    "type": "add",
                    "instrument_id": inst["id"],
                    "account_id": acct["id"],
                    "quantity": 2,
                    "acquired_at": _FUTURE,
                }
            )
        )
    except Exception as exc:  # noqa: BLE001 — either a refusal result or a raise is fine
        said = str(exc)

    assert "in the future" in said
    assert PositionStore().all() == [] and LotStore().all() == []


def test_editing_a_lot_to_a_future_date_is_refused(isolated):
    inst, acct = _instrument(), _account()
    made = lots_service.apply_lot_change(
        {
            "type": "acquire",
            "instrument_id": inst["id"],
            "account_id": acct["id"],
            "quantity": 3,
            "acquired_at": "2024-05-01",
        }
    )

    out = lots_service.apply_lot_change(
        {"type": "amend", "lot_id": made["lot_id"], "acquired_at": _FUTURE}
    )

    assert "in the future" in out["error"]
    assert LotStore().find_by_id(made["lot_id"])["acquired_at"] == "2024-05-01"
