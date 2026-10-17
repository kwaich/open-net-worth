"""Write the trade into the memo of every trade cash leg booked before the choke did.

``lots._traded`` now writes a readable memo ("Sold 60 KSTL @ 150.00 USD") on a buy/sell's cash
leg, because the owner reads the register's text, not its hidden ``ticker``/``quantity`` fields
(2026-10-17). Legs booked earlier have an empty memo, though the trade is on the book: the lot a
buy opened and the realized event a sale settled point back at the leg by ``txn_id``. This joins
them and writes the same text the choke writes (``core.trade_text.trade_memo``) into the payload.

Only an investment leg whose memo is EMPTY is touched — a memo anyone wrote stays. A leg linked to
two instruments, or to both a lot and a sale, is ambiguous and left alone. Several fills of one
order sum their quantity and fees and average their price; a fill with no price drops the price.

Downgrade is a no-op — a backfilled memo is indistinguishable from one the choke wrote.

Revision ID: 202610171200_trade_cash_legs_carry_their_trade_memo
Revises: 202610151200_trade_cash_legs_store_their_trade
Create Date: 2026-10-17
"""

from __future__ import annotations

import json
import logging

import sqlalchemy as sa
from alembic import op

from backend.core.trade_text import trade_memo

revision = "202610171200_trade_cash_legs_carry_their_trade_memo"
down_revision = "202610151200_trade_cash_legs_store_their_trade"
branch_labels = None
depends_on = None

logger = logging.getLogger(__name__)

_SOURCES = (
    ("lots", "Bought", "original_qty", "unit_cost", "cost_currency", "fees"),
    (
        "realized_events",
        "Sold",
        "quantity",
        "proceeds_per_unit",
        "proceeds_currency",
        "disposal_fees",
    ),
)


def _rows(conn, table: str):
    for row_id, payload in conn.execute(sa.text(f"SELECT id, payload FROM {table}")).fetchall():  # noqa: S608
        try:
            data = json.loads(payload) if isinstance(payload, str) else (payload or {})
        except (TypeError, ValueError):
            continue
        if isinstance(data, dict):
            yield row_id, data


def _num(value) -> float | None:
    try:
        return float(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _fills(conn) -> dict[str, list[tuple]]:
    """``{txn_id -> [(verb, instrument_id, qty, price, currency, fees), …]}``."""
    out: dict[str, list[tuple]] = {}
    for table, verb, qty_key, px_key, ccy_key, fee_key in _SOURCES:
        for _, rec in _rows(conn, table):
            if rec.get("txn_id") and rec.get("instrument_id"):
                out.setdefault(rec["txn_id"], []).append(
                    (
                        verb,
                        rec["instrument_id"],
                        abs(_num(rec.get(qty_key)) or 0.0),
                        _num(rec.get(px_key)),
                        (rec.get(ccy_key) or "").upper(),
                        _num(rec.get(fee_key)) or 0.0,
                    )
                )
    return out


def _memo(fills: list[tuple], symbol: str) -> str | None:
    if len({(f[0], f[1]) for f in fills}) != 1 or len({f[4] for f in fills}) != 1:
        return None
    qty = sum(f[2] for f in fills)
    if not qty:
        return None
    priced = all(f[3] for f in fills)
    price = sum(f[2] * f[3] for f in fills) / qty if priced else None
    return trade_memo(fills[0][0], symbol, qty, price, fills[0][4], sum(f[5] for f in fills))


def upgrade() -> None:
    conn = op.get_bind()
    fills = _fills(conn)
    if not fills:
        return
    symbols = {rid: (d.get("symbol") or "").strip() for rid, d in _rows(conn, "instruments")}
    written = 0
    for txn_id, data in _rows(conn, "transactions"):
        legs = fills.get(txn_id)
        if not legs or data.get("kind") != "investment" or (data.get("memo") or "").strip():
            continue
        symbol = data.get("ticker") or symbols.get(legs[0][1])
        memo = _memo(legs, symbol) if symbol else None
        if not memo:
            continue
        data["memo"] = memo
        conn.execute(
            sa.text("UPDATE transactions SET payload = :p, memo = :m WHERE id = :i"),
            {"p": json.dumps(data), "m": memo, "i": txn_id},
        )
        written += 1
    logger.info("trade cash legs: wrote the trade memo on %d transaction(s)", written)


def downgrade() -> None:
    pass
