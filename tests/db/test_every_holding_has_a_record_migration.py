"""Revision 202610111200 — every holding has a record (ADR
2026-10-07-a-position-quantity-is-derived-from-its-records).

Runs the revision for REAL on an isolated tmp-path DB against hand-seeded rows. Synthetic data
only. Pins: a lot-less owned position gets ONE opening lot of exactly its quantity and basis; a
grant's locked/pending positions and an unvested row never get one; a lot filed under another
account than its position's is re-filed; a re-run changes nothing; no position row is written.
"""

from __future__ import annotations

import hashlib
import json

import pytest
from alembic import command
from sqlalchemy import create_engine, text

from backend.db.alembic_config import make_config

_BEFORE = "202610101200_connectors_table"
_AFTER = "202610111200_every_holding_has_a_record"


@pytest.fixture
def db(tmp_path):
    url = f"sqlite:///{tmp_path / 'onw.db'}"
    command.upgrade(make_config(url), _BEFORE)
    engine = create_engine(url)
    yield url, engine
    engine.dispose()


def _insert(conn, table: str, seq: int, row: dict) -> None:
    conn.execute(
        text(f"INSERT INTO {table} (book_id, seq, id, payload) VALUES ('default', :s, :i, :p)"),  # noqa: S608
        {"s": seq, "i": row["id"], "p": json.dumps(row)},
    )


def _pos(pid: str, qty: float, *, account: str = "acc_broker", **extra) -> dict:
    return {
        "id": pid,
        "instrument_id": "inst_x",
        "account_id": account,
        "quantity": qty,
        "cost_basis": 12.5,
        "cost_currency": "USD",
        "vesting_state": "vested",
        "acquired_at": None,
        **extra,
    }


def _seed(engine) -> None:
    with engine.begin() as conn:
        conn.execute(text("INSERT OR IGNORE INTO books (id) VALUES ('default')"))
        conn.execute(
            text("INSERT INTO instruments (seq, id, payload) VALUES (0, 'inst_x', :p)"),
            {"p": json.dumps({"id": "inst_x", "symbol": "XYZ", "quote_currency": "USD"})},
        )
        _insert(conn, "positions", 0, _pos("pos_legacy", 40))
        _insert(conn, "positions", 1, _pos("pos_empty", 0))
        _insert(conn, "positions", 2, _pos("pos_locked", 300, vesting_state="unvested"))
        _insert(conn, "positions", 3, _pos("pos_pending", 50))
        _insert(conn, "positions", 4, _pos("pos_moved", 7))
        _insert(conn, "positions", 5, _pos("pos_hand_unvested", 9, vesting_state="unvested"))
        _insert(
            conn,
            "grants",
            0,
            {
                "id": "grant_1",
                "locked_position_id": "pos_locked",
                "pending_position_id": "pos_pending",
                "tranches": [],
            },
        )
        _insert(
            conn,
            "lots",
            0,
            {
                "id": "lot_moved",
                "position_id": "pos_moved",
                "account_id": "acc_wallet_usd",
                "original_qty": 7,
                "remaining_qty": 7,
                "kind": "buy",
            },
        )


def _lots(engine) -> dict[str, dict]:
    with engine.begin() as conn:
        rows = conn.execute(text("SELECT id, payload FROM lots")).fetchall()
    return {r[0]: json.loads(r[1]) if isinstance(r[1], str) else r[1] for r in rows}


def _positions(engine) -> dict[str, dict]:
    with engine.begin() as conn:
        rows = conn.execute(text("SELECT id, payload FROM positions")).fetchall()
    return {r[0]: json.loads(r[1]) if isinstance(r[1], str) else r[1] for r in rows}


def _opening_id(pid: str) -> str:
    return "lot_" + hashlib.sha256(f"opening:{pid}".encode()).hexdigest()[:12]


def test_a_lot_less_owned_position_gets_one_opening_lot_of_its_own_figures(db):
    url, engine = db
    _seed(engine)
    command.upgrade(make_config(url), _AFTER)

    lot = _lots(engine)[_opening_id("pos_legacy")]
    assert lot["kind"] == "opening"
    assert lot["original_qty"] == lot["remaining_qty"] == 40
    assert lot["unit_cost"] == 12.5 and lot["cost_currency"] == "USD"
    assert lot["account_id"] == "acc_broker"
    assert lot["acquired_at"] == ""  # a legacy holding's date is unknown, never invented


def test_grant_rows_unvested_rows_and_empty_rows_get_no_lot(db):
    url, engine = db
    _seed(engine)
    command.upgrade(make_config(url), _AFTER)

    held = {lot["position_id"] for lot in _lots(engine).values()}
    assert held == {"pos_legacy", "pos_moved"}


def test_a_lot_filed_under_another_account_follows_its_position(db):
    url, engine = db
    _seed(engine)
    command.upgrade(make_config(url), _AFTER)

    assert _lots(engine)["lot_moved"]["account_id"] == "acc_broker"


def test_no_position_figure_is_written(db):
    url, engine = db
    _seed(engine)
    before = _positions(engine)
    command.upgrade(make_config(url), _AFTER)

    assert _positions(engine) == before


def test_downgrade_then_upgrade_is_stable(db):
    url, engine = db
    _seed(engine)
    command.upgrade(make_config(url), _AFTER)
    first = _lots(engine)
    command.downgrade(make_config(url), _BEFORE)
    assert _opening_id("pos_legacy") not in _lots(engine)
    command.upgrade(make_config(url), _AFTER)

    again = _lots(engine)
    assert set(again) == set(first)
    assert again[_opening_id("pos_legacy")]["original_qty"] == 40
