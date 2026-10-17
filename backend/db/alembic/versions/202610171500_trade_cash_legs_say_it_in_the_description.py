"""A trade cash leg says what it traded in its DESCRIPTION, not its memo.

The owner reads the description ("I don't buy 'Share purchase' or 'Sale proceeds' — we need to
give the details there", 2026-10-17), so the trade choke now writes "Sold 60 KSTL @ 150.00 USD"
as the description and leaves the memo free. This moves every earlier leg to the same shape:

- a linked investment leg whose description is still the bare placeholder ("Sale proceeds" /
  "Share purchase") gets the trade text as its description — a description anyone wrote stays;
- its memo is cleared ONLY when it is exactly that generated text (what revision 202610171200
  wrote); a memo with any other words in it is someone's note and stays.

The trade text is computed from the lot / realized event linked by ``txn_id`` — the helpers are a
frozen copy of 202610171200's, as a revision must not change when app code does.

Downgrade is a no-op.

Revision ID: 202610171500_trade_cash_legs_say_it_in_the_description
Revises: 202610171200_trade_cash_legs_carry_their_trade_memo
Create Date: 2026-10-17
"""

from __future__ import annotations

import json
import logging
from decimal import Decimal

import sqlalchemy as sa
from alembic import op

revision = "202610171500_trade_cash_legs_say_it_in_the_description"
down_revision = "202610171200_trade_cash_legs_carry_their_trade_memo"
branch_labels = None
depends_on = None

logger = logging.getLogger(__name__)

_PLACEHOLDERS = {"sale proceeds", "share purchase"}
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


def _price(value: float) -> str:
    whole, _, frac = f"{float(value):,.8f}".rstrip("0").partition(".")
    return f"{whole}.{frac.ljust(2, '0')}"


def _text(verb, symbol, qty, price, ccy, fees) -> str:
    parts = [verb, format(Decimal(repr(abs(qty))).normalize(), "f"), symbol]
    if price:
        parts.append(f"@ {_price(price)} {ccy}".rstrip())
    text = " ".join(parts)
    return text + (f" · fees {_price(fees)} {ccy}".rstrip() if fees and fees > 0 else "")


def _fills(conn) -> dict[str, list[tuple]]:
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


def _trade_text(fills: list[tuple], symbol: str) -> str | None:
    if len({(f[0], f[1]) for f in fills}) != 1 or len({f[4] for f in fills}) != 1:
        return None
    qty = sum(f[2] for f in fills)
    if not qty:
        return None
    price = sum(f[2] * f[3] for f in fills) / qty if all(f[3] for f in fills) else None
    return _text(fills[0][0], symbol, qty, price, fills[0][4], sum(f[5] for f in fills))


def upgrade() -> None:
    conn = op.get_bind()
    fills = _fills(conn)
    if not fills:
        return
    symbols = {rid: (d.get("symbol") or "").strip() for rid, d in _rows(conn, "instruments")}
    moved = 0
    for txn_id, data in _rows(conn, "transactions"):
        legs = fills.get(txn_id)
        desc = (data.get("description") or "").strip().lower()
        if not legs or data.get("kind") != "investment" or desc not in _PLACEHOLDERS:
            continue
        symbol = data.get("ticker") or symbols.get(legs[0][1])
        text = _trade_text(legs, symbol) if symbol else None
        if not text:
            continue
        data["description"] = text
        if (data.get("memo") or "").strip() == text:
            data["memo"] = ""
        conn.execute(
            sa.text(
                "UPDATE transactions SET payload = :p, description = :d, memo = :m WHERE id = :i"
            ),
            {"p": json.dumps(data), "d": text, "m": data.get("memo") or "", "i": txn_id},
        )
        moved += 1
    logger.info("trade cash legs: wrote the trade into %d description(s)", moved)


def downgrade() -> None:
    pass
