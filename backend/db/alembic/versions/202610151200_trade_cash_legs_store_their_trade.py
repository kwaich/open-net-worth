"""Store ``ticker`` + ``quantity`` on every trade cash leg written before the choke did.

``lots._traded`` now stamps what a buy/sell traded onto its cash leg at the trade choke, so the
register reads "Sold 100 KSTL" instead of a bare "Sale proceeds" (2026-10-15). Legs booked before
that carry neither field, though the trade is already on the book: the lot a buy opened and the
realized event a sale settled each point back at the leg by ``txn_id``. This is that join — no
network, no re-derivation — written to the payload (raw number, the source of truth) and the
promoted columns (``quantity`` as ``repr(float)``, exactly as ``money_io._money_text`` writes it).

Only investment legs with NO ticker of their own are touched, and only when every linked fill is
the same instrument (one order, two fills sums; two instruments on one leg is ambiguous and left
alone). The price is deliberately NOT copied: it lives on the lot/event and is read from there.

Downgrade is a no-op — a backfilled annotation is indistinguishable from one the choke wrote.

Revision ID: 202610151200_trade_cash_legs_store_their_trade
Revises: 202610111300_grant_holdings_hold_no_lots
Create Date: 2026-10-15
"""

from __future__ import annotations

import json
import logging

import sqlalchemy as sa
from alembic import op

revision = "202610151200_trade_cash_legs_store_their_trade"
down_revision = "202610111300_grant_holdings_hold_no_lots"
branch_labels = None
depends_on = None

logger = logging.getLogger(__name__)


def _rows(conn, table: str):
    for row_id, payload in conn.execute(sa.text(f"SELECT id, payload FROM {table}")).fetchall():
        try:
            data = json.loads(payload) if isinstance(payload, str) else (payload or {})
        except (TypeError, ValueError):
            continue
        if isinstance(data, dict):
            yield row_id, data


def _fills(conn) -> dict[str, tuple[str, float] | None]:
    """``{txn_id -> (instrument_id, summed quantity)}``; None marks a mixed-instrument leg."""
    out: dict[str, tuple[str, float] | None] = {}
    for table, qty_key in (("lots", "original_qty"), ("realized_events", "quantity")):
        for _, rec in _rows(conn, table):
            txn_id, inst = rec.get("txn_id"), rec.get("instrument_id")
            if not txn_id or not inst:
                continue
            try:
                qty = abs(float(rec.get(qty_key) or 0))
            except (TypeError, ValueError):
                qty = 0.0
            prev = out.get(txn_id, (inst, 0.0))
            out[txn_id] = None if prev is None or prev[0] != inst else (inst, prev[1] + qty)
    return out


def upgrade() -> None:
    conn = op.get_bind()
    fills = _fills(conn)
    if not fills:
        return
    symbols = {
        row_id: (data.get("symbol") or "").strip() for row_id, data in _rows(conn, "instruments")
    }
    stamped = 0
    for txn_id, data in _rows(conn, "transactions"):
        fill = fills.get(txn_id)
        if not fill or data.get("kind") != "investment" or data.get("ticker"):
            continue
        symbol = symbols.get(fill[0])
        if not symbol:
            continue
        data["ticker"] = symbol
        qty = round(fill[1], 8) or None
        if qty:
            data["quantity"] = qty
        conn.execute(
            sa.text(
                "UPDATE transactions SET payload = :p, ticker = :t, "
                "quantity = COALESCE(:q, quantity) WHERE id = :i"
            ),
            {
                "p": json.dumps(data),
                "t": symbol,
                "q": repr(float(qty)) if qty else None,
                "i": txn_id,
            },
        )
        stamped += 1
    logger.info("trade cash legs: stored ticker/quantity on %d transaction(s)", stamped)


def downgrade() -> None:
    pass
