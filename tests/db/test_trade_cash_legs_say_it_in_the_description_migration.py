"""Revision 202610171500 — a trade cash leg's description becomes the trade itself.

Owner, 2026-10-17: "I don't buy 'Share purchase' or 'Sale proceeds' — we need to give the details
there." Runs the revision for REAL on a tmp-path DB, synthetic rows only. Pins: a placeholder
description is replaced and the generated memo cleared; a memo with someone's own words stays; a
description someone wrote stays; a non-investment or unlinked row is untouched; a re-run is a no-op.
"""

from __future__ import annotations

import json

import pytest
from alembic import command
from sqlalchemy import create_engine, text

from backend.db.alembic_config import make_config

_BEFORE = "202610171200_trade_cash_legs_carry_their_trade_memo"
_AFTER = "202610171500_trade_cash_legs_say_it_in_the_description"
_SALE = "Sold 60 KSTL @ 150.00 USD · fees 1.25 USD"


@pytest.fixture
def db(tmp_path):
    url = f"sqlite:///{tmp_path / 'onw.db'}"
    command.upgrade(make_config(url), _BEFORE)
    engine = create_engine(url)
    yield url, engine
    engine.dispose()


def _insert(conn, table: str, seq: int, row: dict, book: bool = True) -> None:
    cols = "book_id, seq, id, payload" if book else "seq, id, payload"
    vals = "'default', :s, :i, :p" if book else ":s, :i, :p"
    conn.execute(
        text(f"INSERT INTO {table} ({cols}) VALUES ({vals})"),  # noqa: S608
        {"s": seq, "i": row["id"], "p": json.dumps(row)},
    )


def _txn(conn, seq, txn_id, desc, memo="", kind="investment") -> None:
    row = {
        "id": txn_id,
        "date": "2026-10-16",
        "kind": kind,
        "source": "ai",
        "description": desc,
        "memo": memo,
    }
    conn.execute(
        text(
            "INSERT INTO transactions (id, book_id, seq, date, kind, source, description, memo, "
            "payload) VALUES (:i, 'default', :s, :d, :k, 'ai', :desc, :m, :p)"
        ),
        {
            "i": txn_id,
            "s": seq,
            "d": row["date"],
            "k": kind,
            "desc": desc,
            "m": memo,
            "p": json.dumps(row),
        },
    )


def _seed(engine) -> None:
    with engine.begin() as conn:
        conn.execute(text("INSERT OR IGNORE INTO books (id) VALUES ('default')"))
        _insert(conn, "instruments", 0, {"id": "i_kstl", "symbol": "KSTL"}, book=False)
        rows = [
            ("t_sell", "Sale proceeds", _SALE, "investment"),
            ("t_buy", "Share purchase", "", "investment"),
            ("t_note", "Sale proceeds", "sold to fund the roof", "investment"),
            ("t_own", "16 KSTL sold by hand", _SALE, "investment"),
            ("t_exp", "Sale proceeds", "", "expense"),
            ("t_free", "Sale proceeds", "", "investment"),
        ]
        for seq, (tid, desc, memo, kind) in enumerate(rows):
            _txn(conn, seq, tid, desc, memo, kind)
        for seq, tid in enumerate(["t_sell", "t_note", "t_own", "t_exp"]):
            _insert(
                conn,
                "realized_events",
                seq,
                {
                    "id": f"e{seq}",
                    "txn_id": tid,
                    "instrument_id": "i_kstl",
                    "quantity": 60,
                    "proceeds_per_unit": 150.0,
                    "proceeds_currency": "USD",
                    "disposal_fees": 1.25,
                },
            )
        _insert(
            conn,
            "lots",
            0,
            {
                "id": "l1",
                "txn_id": "t_buy",
                "instrument_id": "i_kstl",
                "original_qty": 10,
                "unit_cost": 30.0,
                "cost_currency": "USD",
                "fees": 0,
            },
        )


def _read(engine) -> dict[str, tuple]:
    with engine.connect() as conn:
        rows = conn.execute(text("SELECT id, description, memo, payload FROM transactions"))
        out = {}
        for r in rows:
            p = json.loads(r[3])
            assert (p.get("description"), p.get("memo") or "") == (r[1], r[2] or ""), r[0]
            out[r[0]] = (r[1], r[2] or "")
        return out


def test_the_description_becomes_the_trade(db):
    url, engine = db
    _seed(engine)
    command.upgrade(make_config(url), _AFTER)
    got = _read(engine)
    assert got["t_sell"] == (_SALE, ""), "the generated memo moves into the description"
    assert got["t_buy"] == ("Bought 10 KSTL @ 30.00 USD", "")
    assert got["t_note"] == (_SALE, "sold to fund the roof"), "an owner's memo stays"
    assert got["t_own"] == ("16 KSTL sold by hand", _SALE), "an owner's description stays"
    assert got["t_exp"] == ("Sale proceeds", "")
    assert got["t_free"] == ("Sale proceeds", "")


def test_rerun_is_a_no_op(db):
    url, engine = db
    _seed(engine)
    command.upgrade(make_config(url), _AFTER)
    first = _read(engine)
    command.downgrade(make_config(url), _BEFORE)
    command.upgrade(make_config(url), _AFTER)
    assert _read(engine) == first
