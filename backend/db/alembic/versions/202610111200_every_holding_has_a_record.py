"""Every holding has a record: an opening lot for each lot-less owned position, and every lot
filed under its position's account.

ADR 2026-10-07-a-position-quantity-is-derived-from-its-records. A position's quantity is now
re-derived from its records (lots, parked vests, the grant schedule) and nothing else writes
it. Two kinds of rows predate that and are fixed here, both without moving any figure:

(a) An owned position holding units with NO lot (added before lots existed, or by a
    quantity-only add) gets ONE ``opening`` lot of exactly its quantity and cost basis —
    the construction ``lots.ensure_opening_lot`` used to perform lazily on the first sale
    or, until 2026-10-07, on merely viewing the position's lots. Same deterministic id, so a
    book that already got one lazily is left alone. Never for a grant's locked or
    pending-release position: those units are not lots.
(b) A lot whose ``account_id`` differs from its live position's moves to the position's
    account. Moving a position used to leave its lots behind (23 lots stranded under
    settlement wallets on the owner's book, June 2026), which blocked deleting those
    wallets and showed the buys under the wrong account. Values are unchanged — nothing
    sums lots by account.

Afterwards every owned position's stored quantity is compared with Σ its open lots and any
difference is LOGGED, not raised: refusing to boot would lock a user out over a figure the
boot-time ``position_figures.reconcile`` then corrects and reports to them.

Downgrade removes the opening lots this revision minted while still untouched (fully open,
no linked transaction); re-filed lots stay with their position — the old filing was the bug.

Revision ID: 202610111200_every_holding_has_a_record
Revises: 202610101200_connectors_table
Create Date: 2026-10-08
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timezone

import sqlalchemy as sa
from alembic import op

revision = "202610111200_every_holding_has_a_record"
down_revision = "202610101200_connectors_table"
branch_labels = None
depends_on = None

logger = logging.getLogger("alembic.runtime.migration")

_MINTED_NOTE = "opening balance — held before this holding had lots (migration 202610111200)"


def _payload(raw) -> dict:
    return json.loads(raw) if isinstance(raw, str) else dict(raw or {})


def _opening_lot_id(position_id: str) -> str:
    """Byte-identical to the retired ``lots._opening_lot_id`` — a lazily seeded lot and this
    revision's lot for the same position share one id, so neither is ever doubled."""
    return "lot_" + hashlib.sha256(f"opening:{position_id}".encode()).hexdigest()[:12]


def _is_unvested(pos: dict) -> bool:
    state = pos.get("vesting_state")
    if state in ("vested", "unvested"):
        return state == "unvested"
    return pos.get("bucket") == "locked"


def _rows(conn, table: str) -> list[tuple]:
    return conn.execute(sa.text(f"SELECT book_id, id, payload FROM {table}")).fetchall()  # noqa: S608


def _grant_managed_ids(conn) -> set[str]:
    ids: set[str] = set()
    for (raw,) in conn.execute(sa.text("SELECT payload FROM grants")).fetchall():
        grant = _payload(raw)
        for key in ("locked_position_id", "pending_position_id"):
            if grant.get(key):
                ids.add(grant[key])
    return ids


def _opening_lot(pos: dict, quote_ccy: str | None, now: str) -> dict:
    qty = float(pos.get("quantity") or 0)
    cost = pos.get("cost_basis")
    return {
        "id": _opening_lot_id(pos["id"]),
        "position_id": pos["id"],
        "instrument_id": pos.get("instrument_id", ""),
        "account_id": pos.get("account_id", ""),
        "acquired_at": pos.get("acquired_at") or "",
        "original_qty": qty,
        "remaining_qty": qty,
        "unit_cost": float(cost) if cost not in (None, "", 0, 0.0) else None,
        "cost_currency": (pos.get("cost_currency") or quote_ccy or "").upper() or None,
        "fees": 0.0,
        "fx_rate": None,
        "base_currency": None,
        "kind": "opening",
        "txn_id": None,
        "idempotency_key": None,
        "note": _MINTED_NOTE,
        "created_at": now,
        "updated_at": now,
    }


def _mint_opening_lots(conn, positions: list[tuple], lots: list[dict], now: str) -> int:
    grant_managed = _grant_managed_ids(conn)
    with_lots = {lot.get("position_id") for lot in lots}
    quote = {
        row[0]: _payload(row[1]).get("quote_currency")
        for row in conn.execute(sa.text("SELECT id, payload FROM instruments")).fetchall()
    }
    next_seq = conn.execute(sa.text("SELECT COALESCE(MAX(seq), -1) + 1 FROM lots")).scalar_one()
    minted = 0
    for book_id, _, pos in positions:
        if pos["id"] in grant_managed or pos["id"] in with_lots or _is_unvested(pos):
            continue
        if float(pos.get("quantity") or 0) <= 0:
            continue
        lot = _opening_lot(pos, quote.get(pos.get("instrument_id")), now)
        conn.execute(
            sa.text("INSERT INTO lots (book_id, seq, id, payload) VALUES (:b, :s, :i, :p)"),
            {"b": book_id, "s": next_seq, "i": lot["id"], "p": json.dumps(lot)},
        )
        next_seq += 1
        minted += 1
    return minted


def _refile_lots(conn, positions: list[tuple], lots_rows: list[tuple], now: str) -> int:
    venue = {pos["id"]: pos.get("account_id") for _, _, pos in positions}
    refiled = 0
    for _, lot_id, lot in lots_rows:
        account = venue.get(lot.get("position_id"))
        if not account or lot.get("account_id") == account:
            continue
        lot = {**lot, "account_id": account, "updated_at": now}
        conn.execute(
            sa.text("UPDATE lots SET payload = :p WHERE id = :i"),
            {"i": lot_id, "p": json.dumps(lot)},
        )
        refiled += 1
    return refiled


def _log_parity(conn, positions: list[tuple]) -> None:
    grant_managed = _grant_managed_ids(conn)
    open_qty: dict[str, float] = {}
    for _, _, lot in [(r[0], r[1], _payload(r[2])) for r in _rows(conn, "lots")]:
        open_qty[lot.get("position_id")] = open_qty.get(lot.get("position_id"), 0.0) + float(
            lot.get("remaining_qty") or 0
        )
    for _, _, pos in positions:
        if pos["id"] in grant_managed or _is_unvested(pos):
            continue
        stored = float(pos.get("quantity") or 0)
        derived = round(open_qty.get(pos["id"], 0.0), 10)
        if abs(stored - derived) > 1e-9 * max(1.0, abs(derived)):
            logger.warning(
                "position %s: stored quantity %s != Σ open lots %s — the boot reconcile "
                "re-derives it and notifies the user",
                pos["id"],
                stored,
                derived,
            )


def upgrade() -> None:
    conn = op.get_bind()
    now = datetime.now(timezone.utc).isoformat()
    positions = [(r[0], r[1], _payload(r[2])) for r in _rows(conn, "positions")]
    lots_rows = [(r[0], r[1], _payload(r[2])) for r in _rows(conn, "lots")]
    minted = _mint_opening_lots(conn, positions, [lot for _, _, lot in lots_rows], now)
    refiled = _refile_lots(conn, positions, lots_rows, now)
    logger.info(
        "every holding has a record: %d opening lot(s) minted, %d lot(s) re-filed", minted, refiled
    )
    _log_parity(conn, positions)


def downgrade() -> None:
    conn = op.get_bind()
    for _, lot_id, lot in [(r[0], r[1], _payload(r[2])) for r in _rows(conn, "lots")]:
        if lot.get("note") != _MINTED_NOTE or lot.get("txn_id"):
            continue
        if float(lot.get("remaining_qty") or 0) < float(lot.get("original_qty") or 0):
            continue
        conn.execute(sa.text("DELETE FROM lots WHERE id = :i"), {"i": lot_id})
