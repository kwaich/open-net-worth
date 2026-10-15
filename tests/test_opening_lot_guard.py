"""No placeholder ("opening") lot on an unvested grant position (synthetic data only).

Live 2026-10-07: viewing a grant's lots seeded a 1,955-share opening lot on the unvested
position; no vest ever reduced it, so it drifted from the position (1,466.25 vs 977.5). The
lazy seeding is gone altogether now (ADR 2026-10-07-a-position-quantity-is-derived-from-its-
records): a legacy holding's opening lot comes from revision 202610111200, never from a read.
"""

from __future__ import annotations

from backend.db.lot_store import LotStore
from backend.db.position_store import PositionStore
from backend.services import lots as lots_service


def _lots(position_id):
    return [lot for lot in LotStore().all() if lot.get("position_id") == position_id]


def test_reading_any_positions_lots_never_creates_one(db_isolated):
    grant = PositionStore().create(
        {"instrument_id": "inst-1", "account_id": "acc-1", "quantity": 1955, "bucket": "locked"}
    )
    parked = PositionStore().create(  # a parked two-step vest: vested, holds units, no lot
        {"instrument_id": "inst-1", "account_id": "acc-1", "quantity": 488.75}
    )

    for pos in (grant, parked):
        assert lots_service.lots_for_position(pos["id"]) == []
        assert _lots(pos["id"]) == []
    assert not hasattr(lots_service, "ensure_opening_lot")


def test_a_placeholder_already_on_a_grant_is_removed_without_touching_its_quantity(db_isolated):
    from backend.services.lot_integrity import repair_unvested_placeholder_lots

    grant = PositionStore().create(
        {"instrument_id": "inst-1", "account_id": "acc-1", "quantity": 977.5, "bucket": "locked"}
    )
    held = PositionStore().create(
        {"instrument_id": "inst-1", "account_id": "acc-1", "quantity": 10, "cost_currency": "USD"}
    )
    placeholder = {
        "instrument_id": "inst-1",
        "account_id": "acc-1",
        "acquired_at": "",
        "kind": "opening",
    }
    LotStore().insert(
        {
            **placeholder,
            "id": "lot-g",
            "position_id": grant["id"],
            "original_qty": 1955,
            "remaining_qty": 1466.25,
        }
    )
    LotStore().insert(  # a real holding's opening lot stays
        {
            **placeholder,
            "id": "lot-h",
            "position_id": held["id"],
            "original_qty": 10,
            "remaining_qty": 10,
        }
    )

    assert repair_unvested_placeholder_lots() == ["lot-g"]
    assert _lots(grant["id"]) == []
    assert PositionStore().find_by_id(grant["id"])["quantity"] == 977.5  # never resynced to 0
    assert [lot["kind"] for lot in _lots(held["id"])] == ["opening"]
    assert repair_unvested_placeholder_lots() == []  # idempotent
