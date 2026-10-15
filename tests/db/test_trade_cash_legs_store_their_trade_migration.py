"""Revision 202610151200 — a trade cash leg booked before the choke stamped it gets its
``ticker`` + ``quantity`` stored from the lot/realized event that links back to it.

Found 2026-10-15: every IBKR sale read as a bare "Sale proceeds" in the register. Runs the
revision for REAL on a tmp-path DB, synthetic rows only. Pins: a sale and a two-fill buy are
stamped in payload AND columns; a leg with its own ticker, a non-investment row, an unlinked
leg and a mixed-instrument leg are untouched; a re-run is a no-op.
"""

from __future__ import annotations

import json

import pytest
from alembic import command
from sqlalchemy import create_engine, text

from backend.db.alembic_config import make_config

_BEFORE = "202610111300_grant_holdings_hold_no_lots"
_AFTER = "202610151200_trade_cash_legs_store_their_trade"


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


def _txn(conn, seq: int, txn_id: str, kind: str = "investment", **extra) -> None:
    row = {
        "id": txn_id,
        "date": "2026-10-14",
        "kind": kind,
        "source": "ai",
        "description": "Sale proceeds",
        **extra,
    }
    conn.execute(
        text(
            "INSERT INTO transactions (id, book_id, seq, date, kind, source, description, "
            "ticker, quantity, payload) VALUES (:i, 'default', :s, :d, :k, 'ai', :desc, :t, "
            ":q, :p)"
        ),
        {
            "i": txn_id,
            "s": seq,
            "d": row["date"],
            "k": kind,
            "desc": row["description"],
            "t": extra.get("ticker"),
            "q": None,
            "p": json.dumps(row),
        },
    )


def _seed(engine) -> None:
    with engine.begin() as conn:
        conn.execute(text("INSERT OR IGNORE INTO books (id) VALUES ('default')"))
        for seq, (iid, sym) in enumerate([("i_kstl", "KSTL"), ("i_voo", "VOO")]):
            _insert(conn, "instruments", seq, {"id": iid, "symbol": sym}, book=False)
        for seq, tid in enumerate(["t_sell", "t_buy", "t_own", "t_exp", "t_free", "t_mixed"]):
            kind = "expense" if tid == "t_exp" else "investment"
            extra = {"ticker": "AAPL", "quantity": 3} if tid == "t_own" else {}
            _txn(conn, seq, tid, kind, **extra)
        _insert(
            conn,
            "realized_events",
            0,
            {"id": "e1", "txn_id": "t_sell", "instrument_id": "i_kstl", "quantity": 100},
        )
        _insert(
            conn,
            "realized_events",
            1,
            {"id": "e2", "txn_id": "t_own", "instrument_id": "i_kstl", "quantity": 9},
        )
        _insert(
            conn,
            "realized_events",
            2,
            {"id": "e3", "txn_id": "t_exp", "instrument_id": "i_kstl", "quantity": 1},
        )
        for seq, (lid, tid, iid, q) in enumerate(
            [
                ("l1", "t_buy", "i_voo", 1.5),
                ("l2", "t_buy", "i_voo", 0.5),
                ("l3", "t_mixed", "i_voo", 1),
                ("l4", "t_mixed", "i_kstl", 1),
            ]
        ):
            _insert(
                conn,
                "lots",
                seq,
                {"id": lid, "txn_id": tid, "instrument_id": iid, "original_qty": q},
            )


def _read(engine) -> dict[str, tuple]:
    with engine.connect() as conn:
        rows = conn.execute(text("SELECT id, ticker, quantity, payload FROM transactions"))
        return {
            r[0]: (r[1], r[2], json.loads(r[3]).get("ticker"), json.loads(r[3]).get("quantity"))
            for r in rows
        }


def test_trade_legs_store_their_ticker_and_quantity(db):
    url, engine = db
    _seed(engine)
    command.upgrade(make_config(url), _AFTER)
    got = _read(engine)
    assert got["t_sell"] == ("KSTL", "100.0", "KSTL", 100.0)
    assert got["t_buy"] == ("VOO", "2.0", "VOO", 2.0)
    assert got["t_own"][2:] == ("AAPL", 3), "a leg's own ticker is never overwritten"
    for untouched in ("t_exp", "t_free", "t_mixed"):
        assert got[untouched] == (None, None, None, None), untouched


def test_rerun_is_a_no_op(db):
    url, engine = db
    _seed(engine)
    command.upgrade(make_config(url), _AFTER)
    first = _read(engine)
    command.downgrade(make_config(url), _BEFORE)
    command.upgrade(make_config(url), _AFTER)
    assert _read(engine) == first
