"""Stamp the vest-date FMV basis on every pending-release position.

A two-step vest (``release_required``) parks its units in the grant's pending position
with NO lot — the shares are not sellable yet. Until 2026-09-23 it also left the
position's ``cost_basis`` null, although the vest event knew the FMV; the Holdings row
merges pending units with the delivered shares of the same instrument, so one null leg
blanked the P&L of the whole holding. The write path now keeps the pending basis as the
quantity-weighted FMV of every still-parked vest (``equity_release.pending_basis``);
this revision backfills positions parked before that. Vests that disagree on currency
stay unknown — averaging raw unit costs across currencies fabricates a number.

Downgrade is a no-op: a derived figure that the write path keeps current is not worth
un-deriving.

Revision ID: 202609231130_pending_position_fmv_basis
Revises: 202609211200_remove_category_taxable_default
Create Date: 2026-09-23
"""

from __future__ import annotations

import json

import sqlalchemy as sa
from alembic import op

revision = "202609231130_pending_position_fmv_basis"
down_revision = "202609211200_remove_category_taxable_default"
branch_labels = None
depends_on = None


def _payload(raw) -> dict:
    return json.loads(raw) if isinstance(raw, str) else dict(raw or {})


def upgrade() -> None:
    conn = op.get_bind()
    pending_ids = {
        _payload(raw).get("pending_position_id")
        for (raw,) in conn.execute(sa.text("SELECT payload FROM grants")).fetchall()
    }
    pending_ids.discard(None)
    if not pending_ids:
        return

    legs: dict[str, list[tuple[float, float, str]]] = {pid: [] for pid in pending_ids}
    for (raw,) in conn.execute(sa.text("SELECT payload FROM vest_events")).fetchall():
        ev = _payload(raw)
        pid = ev.get("pending_position_id")
        if ev.get("pending_release") is not True or pid not in legs:
            continue
        units = float(ev.get("units") or 0)
        fmv = float(ev.get("fmv_per_unit") or 0)
        if units > 0 and fmv > 0:
            legs[pid].append((units, fmv, (ev.get("fmv_currency") or "").upper()))

    for pid, parked in legs.items():
        row = conn.execute(
            sa.text("SELECT payload FROM positions WHERE id = :id"), {"id": pid}
        ).fetchone()
        if row is None:
            continue
        payload = _payload(row[0])
        qty = sum(q for q, _, _ in parked)
        currencies = {ccy for _, _, ccy in parked}
        if qty and len(currencies) == 1:
            payload["cost_basis"] = round(sum(q * c for q, c, _ in parked) / qty, 10)
            payload["cost_currency"] = next(iter(currencies))
        else:
            payload["cost_basis"] = None
        conn.execute(
            sa.text("UPDATE positions SET payload = :payload WHERE id = :id"),
            {"id": pid, "payload": json.dumps(payload)},
        )


def downgrade() -> None:
    pass
