"""Self-repair for lots left behind by a removed position.

Until 2026-10-01, removing a position deleted only the position row; a lot it owned stayed
"held", so the net-worth history, returns and per-holding P&L kept counting a holding that no
longer existed (a test gold position counted as SGD 179K; a removed 10-share TSLA entry as an
extra 10 shares). ``positions.remove_position`` now removes those lots itself; this repairs any
left over from before, once per boot, idempotently.

Only the bug's exact signature is repaired, through the lots choke: the lot's position no longer
exists, it was never sold from, it carries no linked ledger transaction, and its position has no
sale history. Anything else is left untouched and logged — never guessed at. (No other code
path deletes a position; deleting an account is refused while it holds live positions.)
"""

from __future__ import annotations

import logging

from backend.db.instrument_store import InstrumentStore
from backend.db.lot_store import LotStore
from backend.db.position_store import PositionStore, vesting_state_of
from backend.db.realized_event_store import RealizedEventStore

logger = logging.getLogger(__name__)


def _never_sold(lot: dict) -> bool:
    return float(lot.get("remaining_qty") or 0) >= float(lot.get("original_qty") or 0) > 0


def find_orphan_lots() -> tuple[list[dict], list[dict]]:
    """(repairable, left_alone): lots whose position no longer exists, split by whether the
    leftover-from-removal signature holds exactly."""
    live = {p.get("id") for p in PositionStore().all()}
    sold_positions = {e.get("position_id") for e in RealizedEventStore().query_events()}
    repairable, left_alone = [], []
    for lot in LotStore().all():
        position_id = lot.get("position_id")
        if not position_id or position_id in live:
            continue
        if _never_sold(lot) and not lot.get("txn_id") and position_id not in sold_positions:
            repairable.append(lot)
        else:
            left_alone.append(lot)
    return repairable, left_alone


def repair_orphan_lots() -> dict:
    """Delete every repairable orphan lot through the lots choke; tell the user what went."""
    from backend.services import lots as lots_service

    repairable, left_alone = find_orphan_lots()
    removed = []
    for lot in repairable:
        result = lots_service.apply_lot_change({"type": "delete", "lot_id": lot["id"]})
        if result.get("ok"):
            removed.append(lot)
        else:
            logger.warning("orphan lot %s not repaired: %s", lot["id"], result.get("error"))
    for lot in left_alone:
        logger.warning(
            "orphan lot %s left alone (sold from, linked to a transaction, or its position has "
            "sale history) — review it by hand",
            lot["id"],
        )
    if removed:
        _tell_the_user(removed)
    return {
        "removed": [lot["id"] for lot in removed],
        "left_alone": [lot["id"] for lot in left_alone],
    }


def _tell_the_user(removed: list[dict]) -> None:
    from backend.db import NotificationLogStore

    instruments = {i.get("id"): i for i in InstrumentStore().all()}
    items = ", ".join(
        f"{lot.get('original_qty'):g} {instruments.get(lot.get('instrument_id'), {}).get('symbol') or 'units'}"
        f" (bought {lot.get('acquired_at') or 'undated'})"
        for lot in removed
    )
    NotificationLogStore().append(
        {
            "type": "notification",
            "title": "Cleaned up leftover holdings",
            "summary": (
                f"Removed {len(removed)} leftover purchase record(s) from positions you had already "
                f"deleted: {items}. They were still being counted in your net-worth history and "
                "returns; your live balances were never affected."
            ),
            "trigger": "data_repair",
        }
    )
    logger.info("repaired %d orphan lot(s): %s", len(removed), items)


def repair_unvested_placeholder_lots() -> list[str]:
    """Delete every placeholder ("opening") lot on an UNVESTED grant position.

    Unvested units are not acquired yet, so they never have a lot — the grant tracks them on
    the position's own quantity. Until 2026-10-07 the lots read route seeded an opening lot
    for any lot-less position just by being viewed, grants included, and no vest ever reduced
    it: KSTL's grant carried a 1,955-share placeholder at 1,466.25 beside a 977.5-share
    position. Removed directly, NOT through the lots choke: that choke refuses a partly
    consumed lot and resyncs the position from its lots, which would zero the grant's
    unvested quantity. No figure moves — every reader already skips lots of unvested
    positions. Idempotent; logged only.
    """
    from backend.db.engine import locked_unit_of_work
    from backend.db.tables import lots as lots_tbl

    unvested = {p.get("id") for p in PositionStore().all() if vesting_state_of(p) == "unvested"}
    stale = [
        lot
        for lot in LotStore().all()
        if lot.get("kind") == "opening"
        and lot.get("position_id") in unvested
        and not lot.get("txn_id")
    ]
    if not stale:
        return []
    with locked_unit_of_work(lots_tbl) as conn:
        for lot in stale:
            LotStore().delete(lot["id"], conn=conn)
    ids = [lot["id"] for lot in stale]
    logger.info("removed %d placeholder lot(s) from unvested grant positions: %s", len(ids), ids)
    return ids
