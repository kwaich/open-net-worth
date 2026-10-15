"""A grant's unvested and awaiting-release holdings hold no lots: remove the opening lots the old
lots read wrote onto them.

ADR 2026-10-07-a-position-quantity-is-derived-from-its-records. A grant's LOCKED position
(unvested units) and its PENDING-RELEASE position (vested units parked until delivery) take
their figures from the grant and its vest events — never from lots; delivered shares get
their lot when the release is recorded. Until 2026-10-07, merely VIEWING a position's lots
(``GET /api/positions/{id}/lots`` → ``lots.ensure_opening_lot``) seeded an ``opening`` lot of
the stored quantity onto any position with more units than lots — these two included. Found
2026-10-11 on the owner's book: 488.75 parked KSTL units got one. It moves no figure (both
roles ignore lots) but it is sellable, so shares not yet delivered could be sold, and the
release would book a second lot for the same units.

Removed only when the row is exactly what that read wrote: kind ``opening``, no linked
transaction, nothing ever sold from it (remaining == original), on a grant's locked or
pending position. Anything else on such a position is left alone and reported at boot by
``position_figures.stray_grant_lots``. Each removal is logged with its figures.

Downgrade restores nothing: the rows were never valid records.

Revision ID: 202610111300_grant_holdings_hold_no_lots
Revises: 202610111200_every_holding_has_a_record
Create Date: 2026-10-11
"""

from __future__ import annotations

import json
import logging

import sqlalchemy as sa
from alembic import op

revision = "202610111300_grant_holdings_hold_no_lots"
down_revision = "202610111200_every_holding_has_a_record"
branch_labels = None
depends_on = None

logger = logging.getLogger("alembic.runtime.migration")

_QTY_EPS = 1e-9


def _payload(raw) -> dict:
    return json.loads(raw) if isinstance(raw, str) else dict(raw or {})


def _grant_managed_ids(conn) -> set[str]:
    ids: set[str] = set()
    for (raw,) in conn.execute(sa.text("SELECT payload FROM grants")).fetchall():
        grant = _payload(raw)
        for key in ("locked_position_id", "pending_position_id"):
            if grant.get(key):
                ids.add(grant[key])
    return ids


def _written_by_the_old_read(lot: dict) -> bool:
    original = float(lot.get("original_qty") or 0)
    remaining = float(lot.get("remaining_qty") or 0)
    return (
        lot.get("kind") == "opening"
        and not lot.get("txn_id")
        and abs(original - remaining) <= _QTY_EPS * max(1.0, abs(original))
    )


def upgrade() -> None:
    conn = op.get_bind()
    managed = _grant_managed_ids(conn)
    if not managed:
        return
    removed = 0
    for lot_id, raw in conn.execute(sa.text("SELECT id, payload FROM lots")).fetchall():
        lot = _payload(raw)
        if lot.get("position_id") not in managed or not _written_by_the_old_read(lot):
            continue
        conn.execute(sa.text("DELETE FROM lots WHERE id = :i"), {"i": lot_id})
        removed += 1
        logger.warning(
            "removed opening lot %s (%s units, unit cost %s %s) from grant holding %s — "
            "a grant's unvested/awaiting-release units are not lots",
            lot_id,
            lot.get("original_qty"),
            lot.get("unit_cost"),
            lot.get("cost_currency") or "",
            lot.get("position_id"),
        )
    if removed:
        logger.warning("grant holdings: removed %d stray opening lot(s)", removed)


def downgrade() -> None:
    pass
