"""
API smoke tests for the lots / realized-P&L read router (P2-INV1b).

Offline + isolated, mirroring tests/test_commitments_api.py: a minimal FastAPI app
mounts only the lots router, and the store file paths are monkeypatched into tmp_path
so requests never touch live data. The lots service constructs LotStore() /
RealizedEventStore() fresh per read call, so patching the module path constant before
the request takes effect. No LLM, no network, no full-app lifespan.

These assert status + response SHAPE (the endpoint smoke contract). The exact
realized-gain arithmetic is pinned offline in tests/test_lots_service.py.

Run:
    .venv/bin/pytest tests/test_lots_api.py -v
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api.lots import router


@pytest.fixture
def client(tmp_path, monkeypatch, db_isolated, fx_rates):
    app = FastAPI()
    app.include_router(router)
    return TestClient(app), tmp_path


@pytest.fixture
def write_client(tmp_path, monkeypatch, db_isolated):
    """A client whose write routes hit isolated stores + identity FX (USD base).

    positions/lots/realized_events are DB-backed (plan 4b task 2) — db_isolated gives
    every store a fresh per-test database; the lots service is pinned to a USD-only
    rate table so no fx_cache file is needed.
    """
    from backend.services import lots as lots_service

    monkeypatch.setattr(lots_service, "_load_rates", lambda: {"USD": 1.0})
    monkeypatch.setattr(lots_service, "base_currency", lambda: "USD")
    app = FastAPI()
    app.include_router(router)
    return TestClient(app), tmp_path


def _seed_position():
    from backend.db.position_store import PositionStore

    return PositionStore().create(
        {"instrument_id": "inst-1", "account_id": "acc-1", "quantity": 0, "cost_currency": "USD"}
    )


def test_lots_empty_position(client):
    c, _ = client
    r = c.get("/api/positions/pos-1/lots")
    assert r.status_code == 200
    body = r.json()
    assert body["position_id"] == "pos-1"
    assert body["lots"] == []


def test_lots_for_position_shape(client):
    from backend.db.lot_store import LotStore

    c, tmp = client
    LotStore().bulk_insert(
        [
            {
                "id": "L1",
                "position_id": "pos-1",
                "instrument_id": "inst-1",
                "account_id": "acc-1",
                "acquired_at": "2025-01-01",
                "original_qty": 10.0,
                "remaining_qty": 6.0,
                "unit_cost": 100.0,
                "cost_currency": "USD",
                "fees": 0.0,
                "kind": "buy",
            },
            # a different position's lot must NOT leak in
            {
                "id": "L9",
                "position_id": "pos-OTHER",
                "instrument_id": "inst-9",
                "account_id": "acc-9",
                "acquired_at": "2025-02-01",
                "original_qty": 5.0,
                "remaining_qty": 5.0,
                "unit_cost": 50.0,
                "cost_currency": "USD",
                "fees": 0.0,
                "kind": "buy",
            },
        ],
    )
    r = c.get("/api/positions/pos-1/lots")
    assert r.status_code == 200
    lots = r.json()["lots"]
    assert len(lots) == 1
    lot = lots[0]
    assert lot["id"] == "L1"
    # service annotations are present (computed in services, not the router)
    assert lot["effective_unit_cost"] == 100.0
    assert lot["remaining_basis"] == 600.0  # 6 × 100
    assert lot["term_to_date"] in ("long", "short", "unknown")


def test_realized_pnl_empty(client):
    c, _ = client
    r = c.get("/api/realized-pnl")
    assert r.status_code == 200
    body = r.json()
    assert body["events"] == []
    assert body["realized_total_base"] == 0.0
    assert body["by_term"] == {"long": 0.0, "short": 0.0, "unknown": 0.0}
    assert body["by_instrument"] == {}


def test_realized_pnl_aggregates_and_filters(client):
    from backend.db.realized_event_store import RealizedEventStore

    c, tmp = client
    # Two USD disposals; base defaults to the configured base. To keep the assertion
    # base-currency-agnostic we set proceeds_currency == base via the event itself by
    # using the service's base. We assert the per-event native gain round-trips and the
    # by_instrument / by_term split is shaped correctly.
    RealizedEventStore().bulk_insert(
        [
            {
                "id": "E1",
                "position_id": "pos-1",
                "instrument_id": "inst-1",
                "account_id": "acc-1",
                "disposed_at": "2026-03-01",
                "quantity": 10.0,
                "proceeds_per_unit": 130.0,
                "proceeds_currency": "USD",
                "method": "fifo",
                "is_taxable": True,
                "cost_basis_known": True,
                "consumptions": [
                    {
                        "lot_id": "L1",
                        "qty": 10.0,
                        "unit_cost": 100.0,
                        "cost_currency": "USD",
                        "acquired_at": "2024-01-01",
                        "term": "long",
                        "realized_gain": 300.0,
                    }
                ],
                "realized_gain": 300.0,
                "realized_gain_base": 300.0,
                "base_currency": "USD",
                "fx_rate": 1.0,
                "created_at": "2026-03-01T00:00:00Z",
            },
            {
                "id": "E2",
                "position_id": "pos-2",
                "instrument_id": "inst-2",
                "account_id": "acc-1",
                "disposed_at": "2026-05-01",
                "quantity": 5.0,
                "proceeds_per_unit": 80.0,
                "proceeds_currency": "USD",
                "method": "fifo",
                "is_taxable": True,
                "cost_basis_known": True,
                "consumptions": [
                    {
                        "lot_id": "L2",
                        "qty": 5.0,
                        "unit_cost": 100.0,
                        "cost_currency": "USD",
                        "acquired_at": "2026-04-01",
                        "term": "short",
                        "realized_gain": -100.0,
                    }
                ],
                "realized_gain": -100.0,
                "realized_gain_base": -100.0,
                "base_currency": "USD",
                "fx_rate": 1.0,
                "created_at": "2026-05-01T00:00:00Z",
            },
        ],
    )
    r = c.get("/api/realized-pnl")
    assert r.status_code == 200
    body = r.json()
    assert {"base_currency", "events", "realized_total_base", "by_term", "by_instrument"} <= set(
        body
    )
    assert len(body["events"]) == 2
    # each event carries a (recomputed) base figure
    for ev in body["events"]:
        assert "realized_gain_base" in ev
    # by_instrument keyed by instrument id, two entries
    assert set(body["by_instrument"]) == {"inst-1", "inst-2"}

    # date filter: only the March disposal
    r2 = c.get("/api/realized-pnl", params={"date_to": "2026-03-31"})
    assert r2.status_code == 200
    assert [e["id"] for e in r2.json()["events"]] == ["E1"]

    # instrument filter
    r3 = c.get("/api/realized-pnl", params={"instrument_id": "inst-2"})
    assert [e["id"] for e in r3.json()["events"]] == ["E2"]


# ── Write routes (P2-INV1c-a): acquire + dispose through apply_lot_change ────────


def test_post_acquire_lot_creates_lot_and_resyncs(write_client):
    c, _ = write_client
    pos = _seed_position()
    r = c.post(
        f"/api/positions/{pos['id']}/lots",
        json={
            "quantity": 10,
            "unit_cost": 100,
            "cost_currency": "USD",
            "acquired_at": "2026-01-01",
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["lot_id"]
    # position resynced from the lot
    assert body["quantity"] == 10.0
    assert body["cost_basis"] == 100.0
    # the lot is now readable via the GET route
    lots = c.get(f"/api/positions/{pos['id']}/lots").json()["lots"]
    assert len(lots) == 1


def test_post_disposal_fifo_books_realized_event(write_client):
    c, _ = write_client
    pos = _seed_position()
    c.post(
        f"/api/positions/{pos['id']}/lots",
        json={
            "quantity": 10,
            "unit_cost": 100,
            "cost_currency": "USD",
            "acquired_at": "2026-01-01",
        },
    )
    r = c.post(
        f"/api/positions/{pos['id']}/disposals",
        json={
            "quantity": 4,
            "proceeds_per_unit": 130,
            "proceeds_currency": "USD",
            "method": "fifo",
            "disposed_at": "2026-06-01",
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["event_id"]
    # the disposal produced the expected realized event: 4 × (130 − 100) = +120
    assert body["realized_gain"] == 120.00
    assert body["quantity"] == 6.0  # position resynced to remaining qty
    # surfaced through the realized-pnl read
    pnl = c.get("/api/realized-pnl").json()
    assert len(pnl["events"]) == 1
    assert pnl["realized_total_base"] == 120.00


def test_post_disposal_specific_id(write_client):
    c, _ = write_client
    pos = _seed_position()
    c.post(
        f"/api/positions/{pos['id']}/lots",
        json={
            "quantity": 10,
            "unit_cost": 100,
            "cost_currency": "USD",
            "acquired_at": "2026-01-01",
        },
    )
    c.post(
        f"/api/positions/{pos['id']}/lots",
        json={
            "quantity": 10,
            "unit_cost": 120,
            "cost_currency": "USD",
            "acquired_at": "2026-03-01",
        },
    )
    lots = c.get(f"/api/positions/{pos['id']}/lots").json()["lots"]
    l2 = next(lot for lot in lots if lot["unit_cost"] == 120)
    r = c.post(
        f"/api/positions/{pos['id']}/disposals",
        json={
            "quantity": 10,
            "proceeds_per_unit": 130,
            "proceeds_currency": "USD",
            "method": "specific_id",
            "lot_selection": [{"lot_id": l2["id"], "qty": 10}],
            "disposed_at": "2026-06-01",
        },
    )
    assert r.status_code == 200
    # specific-ID consuming the high-cost lot books 10 × (130 − 120) = +100 (not 300).
    assert r.json()["realized_gain"] == 100.00


def test_post_disposal_oversell_returns_422(write_client):
    c, _ = write_client
    pos = _seed_position()
    c.post(
        f"/api/positions/{pos['id']}/lots",
        json={"quantity": 5, "unit_cost": 100, "cost_currency": "USD", "acquired_at": "2026-01-01"},
    )
    r = c.post(
        f"/api/positions/{pos['id']}/disposals",
        json={
            "quantity": 10,
            "proceeds_per_unit": 130,
            "proceeds_currency": "USD",
            "method": "fifo",
        },
    )
    assert r.status_code == 422
    # nothing booked
    assert c.get("/api/realized-pnl").json()["events"] == []


def test_post_disposal_bad_lot_id_returns_422(write_client):
    c, _ = write_client
    pos = _seed_position()
    c.post(
        f"/api/positions/{pos['id']}/lots",
        json={
            "quantity": 10,
            "unit_cost": 100,
            "cost_currency": "USD",
            "acquired_at": "2026-01-01",
        },
    )
    r = c.post(
        f"/api/positions/{pos['id']}/disposals",
        json={
            "quantity": 5,
            "proceeds_per_unit": 130,
            "proceeds_currency": "USD",
            "method": "specific_id",
            "lot_selection": [{"lot_id": "does-not-exist", "qty": 5}],
        },
    )
    assert r.status_code == 422


def _with_opening_lot(pos: dict) -> dict:
    """The upgrading-user case AFTER revision 202610111200: a pre-lots holding carries ONE
    opening lot of exactly its quantity and basis."""
    from backend.db.lot_store import LotStore

    LotStore().create(
        {
            "position_id": pos["id"],
            "instrument_id": pos["instrument_id"],
            "account_id": pos["account_id"],
            "original_qty": pos["quantity"],
            "unit_cost": pos["cost_basis"],
            "cost_currency": pos["cost_currency"],
            "kind": "opening",
        }
    )
    return pos


def _seed_existing_position(quantity=10.0, cost_basis=100.0):
    from backend.db.position_store import PositionStore

    return _with_opening_lot(
        PositionStore().create(
            {
                "instrument_id": "inst-1",
                "account_id": "acc-1",
                "quantity": quantity,
                "cost_basis": cost_basis,
                "cost_currency": "USD",
            }
        )
    )


def test_a_legacy_holding_sells_from_its_opening_lot(write_client):
    # P2-INV1c adoption: an upgrading user's pre-lots holding is sellable straight from the
    # UI — its opening lot now comes from the migration, never from viewing it.
    c, _ = write_client
    pos = _seed_existing_position(quantity=10.0, cost_basis=100.0)
    lots = c.get(f"/api/positions/{pos['id']}/lots").json()["lots"]
    assert len(lots) == 1
    assert lots[0]["kind"] == "opening"
    assert lots[0]["remaining_qty"] == 10.0
    # And a FIFO disposal books a realized event instead of raising InsufficientLots.
    r = c.post(
        f"/api/positions/{pos['id']}/disposals",
        json={
            "quantity": 4,
            "proceeds_per_unit": 130,
            "proceeds_currency": "USD",
            "method": "fifo",
        },
    )
    assert r.status_code == 200
    assert r.json()["realized_gain"] == 120.00  # 4 × (130 − 100)


def test_viewing_a_positions_lots_never_writes_one(write_client):
    # The lots route is a pure read (the KSTL placeholder, 2026-10-07): a holding with no lot
    # — a grant's parked-vest row is the live case — stays without one however often viewed.
    from backend.db.lot_store import LotStore
    from backend.db.position_store import PositionStore

    c, _ = write_client
    pos = PositionStore().create(
        {"instrument_id": "inst-1", "account_id": "acc-1", "quantity": 10.0}
    )
    for _ in range(2):
        assert c.get(f"/api/positions/{pos['id']}/lots").json()["lots"] == []
    assert LotStore().all() == []


def test_taxable_zero_proceeds_disposal_returns_422_no_fake_loss(write_client):
    # The money guard reaches the REST path: a taxable $0 sale is refused (422), not booked
    # as a fabricated full-cost loss.
    c, _ = write_client
    pos = _seed_existing_position(quantity=10.0, cost_basis=100.0)
    r = c.post(
        f"/api/positions/{pos['id']}/disposals",
        json={"quantity": 10, "proceeds_per_unit": 0, "proceeds_currency": "USD", "method": "fifo"},
    )
    assert r.status_code == 422
    assert c.get("/api/realized-pnl").json()["events"] == []


def test_transfer_out_zero_proceeds_disposal_is_allowed(write_client):
    # The intentional $0 path (transfer-out / gift) still works and books no gain.
    c, _ = write_client
    pos = _seed_existing_position(quantity=10.0, cost_basis=100.0)
    r = c.post(
        f"/api/positions/{pos['id']}/disposals",
        json={
            "quantity": 10,
            "proceeds_per_unit": 0,
            "proceeds_currency": "USD",
            "method": "fifo",
            "is_taxable": False,
        },
    )
    assert r.status_code == 200
    assert r.json()["realized_gain"] is None


def _seed_two_brokerages():
    from backend.db.account_store import AccountStore
    from backend.db.position_store import PositionStore

    src = AccountStore().create(
        {"name": "Workplace Custodian", "kind": "asset", "type": "investment", "currency": "USD"}
    )
    dst = AccountStore().create(
        {"name": "IBKR", "kind": "asset", "type": "investment", "currency": "USD"}
    )
    pos = _with_opening_lot(
        PositionStore().create(
            {
                "instrument_id": "inst-1",
                "account_id": src["id"],
                "quantity": 10.0,
                "cost_basis": 100.0,
                "cost_currency": "USD",
            }
        )
    )
    return src, dst, pos


def test_transfer_with_destination_stores_the_resolved_account(write_client):
    # A share move between the user's own accounts names where it went (by exact name here);
    # the route passes it through and the event carries the resolved id, not the label.
    c, _ = write_client
    src, dst, pos = _seed_two_brokerages()
    r = c.post(
        f"/api/positions/{pos['id']}/disposals",
        json={
            "quantity": 10,
            "proceeds_per_unit": 0,
            "proceeds_currency": "USD",
            "method": "fifo",
            "is_taxable": False,
            "transfer_to_account_id": "IBKR",
        },
    )
    assert r.status_code == 200, r.text
    assert r.json()["transfer_to_account_id"] == dst["id"]
    events = c.get("/api/realized-pnl").json()["events"]
    assert [e["transfer_to_account_id"] for e in events] == [dst["id"]]
    assert events[0]["is_taxable"] is False


@pytest.mark.parametrize(
    "body",
    [
        {"is_taxable": True, "proceeds_per_unit": 130},  # a sale is not a move
        {"is_taxable": False, "proceeds_per_unit": 0, "transfer_to_account_id": "Nowhere"},
    ],
)
def test_transfer_destination_refusals_reach_the_route_as_422(write_client, body):
    c, _ = write_client
    src, dst, pos = _seed_two_brokerages()
    r = c.post(
        f"/api/positions/{pos['id']}/disposals",
        json={
            "quantity": 10,
            "proceeds_currency": "USD",
            "method": "fifo",
            "transfer_to_account_id": "IBKR",
            **body,
        },
    )
    assert r.status_code == 422, r.text
    assert c.get("/api/realized-pnl").json()["events"] == []


def test_post_acquire_unknown_position_returns_404(write_client):
    c, _ = write_client
    r = c.post(
        "/api/positions/nope/lots",
        json={"quantity": 10, "unit_cost": 100, "cost_currency": "USD"},
    )
    assert r.status_code == 404


def test_post_disposal_unknown_position_returns_404(write_client):
    c, _ = write_client
    r = c.post(
        "/api/positions/nope/disposals",
        json={"quantity": 1, "proceeds_per_unit": 1, "proceeds_currency": "USD"},
    )
    assert r.status_code == 404


def test_post_identical_acquire_returns_409_unless_allow_duplicate(write_client):
    """Regression (double-book guard): a second identical buy lot is refused with 409
    (not a generic 404), and allow_duplicate=true overrides for genuine repeat fills."""
    c, _ = write_client
    pos = _seed_position()
    body = {"quantity": 1, "unit_cost": 167.48, "cost_currency": "USD", "acquired_at": "2026-06-12"}

    assert c.post(f"/api/positions/{pos['id']}/lots", json=body).status_code == 200

    dup = c.post(f"/api/positions/{pos['id']}/lots", json=body)
    assert dup.status_code == 409
    assert "identical open lot" in dup.json()["detail"]

    override = c.post(f"/api/positions/{pos['id']}/lots", json={**body, "allow_duplicate": True})
    assert override.status_code == 200


def test_delete_lot_route(write_client):
    c, _ = write_client
    pos = _seed_position()
    lot_id = c.post(
        f"/api/positions/{pos['id']}/lots",
        json={"quantity": 10, "unit_cost": 100, "acquired_at": "2026-01-01"},
    ).json()["lot_id"]
    r = c.delete(f"/api/lots/{lot_id}")
    assert r.status_code == 200
    assert r.json()["ok"] is True
    assert c.get(f"/api/positions/{pos['id']}/lots").json()["lots"] == []


def test_patch_lot_route(write_client):
    c, _ = write_client
    pos = _seed_position()
    lot_id = c.post(
        f"/api/positions/{pos['id']}/lots",
        json={"quantity": 10, "unit_cost": 100, "acquired_at": "2026-01-01"},
    ).json()["lot_id"]
    r = c.patch(f"/api/lots/{lot_id}", json={"quantity": 7, "note": "fix"})
    assert r.status_code == 200
    lots = c.get(f"/api/positions/{pos['id']}/lots").json()["lots"]
    assert lots[0]["original_qty"] == 7
    assert lots[0]["note"] == "fix"


def test_patch_disposal_route(write_client):
    c, _ = write_client
    pos = _seed_position()
    c.post(
        f"/api/positions/{pos['id']}/lots",
        json={"quantity": 10, "unit_cost": 100, "acquired_at": "2026-01-01"},
    )
    sold = c.post(
        f"/api/positions/{pos['id']}/disposals",
        json={"quantity": 4, "proceeds_per_unit": 130, "disposed_at": "2026-06-01"},
    ).json()
    r = c.patch(
        f"/api/disposals/{sold['event_id']}",
        json={"quantity": 4, "proceeds_per_unit": 140, "disposed_at": "2026-06-01"},
    )
    assert r.status_code == 200
    assert r.json()["realized_gain"] == 160.00


def test_post_instrument_lot_first_hold(write_client):
    from backend.db.account_store import AccountStore
    from backend.db.instrument_store import InstrumentStore

    c, _ = write_client
    inst = InstrumentStore().create(
        {"symbol": "PL", "price_source_id": "PL", "quote_currency": "USD", "asset_class": "equity"}
    )
    acc = AccountStore().create(
        {"name": "IBKR", "kind": "asset", "type": "investment", "currency": "USD"}
    )
    r = c.post(
        f"/api/instruments/{inst['id']}/lots",
        json={
            "account_id": acc["id"],
            "quantity": 5,
            "unit_cost": 11,
            "acquired_at": "2026-01-01",
        },
    )
    assert r.status_code == 200
    assert r.json()["ok"] is True
    assert r.json()["quantity"] == 5.0
