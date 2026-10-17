"""Revision 202610171200 — a trade cash leg booked before the choke wrote a memo gets the same
readable text ("Sold 60 KSTL @ 150.00 USD") from the lot/realized event that links back to it.

Runs the revision for REAL on a tmp-path DB, synthetic rows only. Pins: a sale (with fees) and a
two-fill buy get their memo in payload AND column; a memo someone wrote, a non-investment row, an
unlinked leg and a mixed-instrument leg are untouched; a re-run is a no-op.
"""

from __future__ import annotations

import json

import pytest
from alembic import command
from sqlalchemy import create_engine, text

from backend.db.alembic_config import make_config

_BEFORE = "202610151200_trade_cash_legs_store_their_trade"
_AFTER = "202610171200_trade_cash_legs_carry_their_trade_memo"


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


def _txn(conn, seq: int, txn_id: str, kind: str = "investment", memo: str = "") -> None:
    row = {
        "id": txn_id,
        "date": "2026-10-16",
        "kind": kind,
        "source": "ai",
        "description": "Sale proceeds",
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
            "desc": row["description"],
            "m": memo,
            "p": json.dumps(row),
        },
    )


def _seed(engine) -> None:
    with engine.begin() as conn:
        conn.execute(text("INSERT OR IGNORE INTO books (id) VALUES ('default')"))
        for seq, (iid, sym) in enumerate([("i_kstl", "KSTL"), ("i_vxo", "VXO")]):
            _insert(conn, "instruments", seq, {"id": iid, "symbol": sym}, book=False)
        txns = [
            ("t_sell", "investment", ""),
            ("t_buy", "investment", ""),
            ("t_noted", "investment", "my own note"),
            ("t_exp", "expense", ""),
            ("t_free", "investment", ""),
            ("t_mixed", "investment", ""),
        ]
        for seq, (tid, kind, memo) in enumerate(txns):
            _txn(conn, seq, tid, kind, memo)
        events = [
            ("e1", "t_sell", 60, 150.0, 1.25),
            ("e2", "t_noted", 5, 10.0, 0),
            ("e3", "t_exp", 1, 10.0, 0),
        ]
        for seq, (eid, tid, q, px, fee) in enumerate(events):
            _insert(
                conn,
                "realized_events",
                seq,
                {
                    "id": eid,
                    "txn_id": tid,
                    "instrument_id": "i_kstl",
                    "quantity": q,
                    "proceeds_per_unit": px,
                    "proceeds_currency": "USD",
                    "disposal_fees": fee,
                },
            )
        lots = [
            ("l1", "t_buy", "i_vxo", 1, 500.0),
            ("l2", "t_buy", "i_vxo", 3, 520.0),
            ("l3", "t_mixed", "i_vxo", 1, 5.0),
            ("l4", "t_mixed", "i_kstl", 1, 5.0),
        ]
        for seq, (lid, tid, iid, q, px) in enumerate(lots):
            _insert(
                conn,
                "lots",
                seq,
                {
                    "id": lid,
                    "txn_id": tid,
                    "instrument_id": iid,
                    "original_qty": q,
                    "unit_cost": px,
                    "cost_currency": "USD",
                    "fees": 0,
                },
            )


def _memos(engine) -> dict[str, tuple]:
    with engine.connect() as conn:
        rows = conn.execute(text("SELECT id, memo, payload FROM transactions"))
        return {r[0]: (r[1], json.loads(r[2]).get("memo")) for r in rows}


def test_trade_legs_get_their_trade_memo(db):
    url, engine = db
    _seed(engine)
    command.upgrade(make_config(url), _AFTER)
    got = _memos(engine)
    sale = "Sold 60 KSTL @ 150.00 USD · fees 1.25 USD"
    assert got["t_sell"] == (sale, sale)
    assert got["t_buy"] == ("Bought 4 VXO @ 515.00 USD",) * 2
    assert got["t_noted"] == ("my own note",) * 2, "a memo someone wrote is kept"
    for untouched in ("t_exp", "t_free", "t_mixed"):
        assert got[untouched] == ("", ""), untouched


def test_rerun_is_a_no_op(db):
    url, engine = db
    _seed(engine)
    command.upgrade(make_config(url), _AFTER)
    first = _memos(engine)
    command.downgrade(make_config(url), _BEFORE)
    command.upgrade(make_config(url), _AFTER)
    assert _memos(engine) == first
