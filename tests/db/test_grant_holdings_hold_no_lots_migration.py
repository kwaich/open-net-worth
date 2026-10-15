"""Revision 202610111300 — a grant's unvested and awaiting-release holdings hold no lots.

Found 2026-10-11 on the owner's book: viewing a grant's pending-release holding's lots under
the old read route (``ensure_opening_lot``) seeded a sellable ``opening`` lot of its 488.75
parked units. Runs the revision for REAL on a tmp-path DB, synthetic rows only. Pins: exactly
that shape is removed from a locked or pending position; a lot sold from, linked to cash, of
another kind, or on an owned position stays; no position row is written; a re-run is a no-op.
"""

from __future__ import annotations

import json

import pytest
from alembic import command
from sqlalchemy import create_engine, text

from backend.db.alembic_config import make_config

_BEFORE = "202610111200_every_holding_has_a_record"
_AFTER = "202610111300_grant_holdings_hold_no_lots"


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


def _lot(lot_id: str, pid: str, qty: float = 10, **extra) -> dict:
    return {
        "id": lot_id,
        "position_id": pid,
        "account_id": "acc_custodian",
        "original_qty": qty,
        "remaining_qty": qty,
        "kind": "opening",
        "unit_cost": 20.0,
        "cost_currency": "EUR",
        "txn_id": None,
        **extra,
    }


def _seed(engine) -> None:
    with engine.begin() as conn:
        conn.execute(text("INSERT OR IGNORE INTO books (id) VALUES ('default')"))
        for seq, (pid, qty, state) in enumerate(
            [
                ("pos_locked", 300, "unvested"),
                ("pos_pending", 50, "vested"),
                ("pos_owned", 5, "vested"),
            ]
        ):
            _insert(
                conn,
                "positions",
                seq,
                {
                    "id": pid,
                    "instrument_id": "inst_x",
                    "account_id": "acc_custodian",
                    "quantity": qty,
                    "cost_basis": 20.0,
                    "vesting_state": state,
                },
            )
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
        rows = [
            _lot("lot_pending_seeded", "pos_pending", 50),
            _lot("lot_locked_seeded", "pos_locked", 300),
            _lot("lot_pending_sold_from", "pos_pending", 50, remaining_qty=30),
            _lot("lot_pending_with_cash", "pos_pending", 50, txn_id="txn_1"),
            _lot("lot_pending_buy", "pos_pending", 50, kind="buy"),
            _lot("lot_owned_opening", "pos_owned", 5),
        ]
        for seq, row in enumerate(rows):
            _insert(conn, "lots", seq, row)


def _ids(engine, table: str) -> dict[str, dict]:
    with engine.begin() as conn:
        rows = conn.execute(text(f"SELECT id, payload FROM {table}")).fetchall()  # noqa: S608
    return {r[0]: json.loads(r[1]) if isinstance(r[1], str) else r[1] for r in rows}


def test_only_the_seeded_opening_lots_on_grant_holdings_are_removed(db):
    url, engine = db
    _seed(engine)
    positions_before = _ids(engine, "positions")

    command.upgrade(make_config(url), _AFTER)

    assert set(_ids(engine, "lots")) == {
        "lot_pending_sold_from",
        "lot_pending_with_cash",
        "lot_pending_buy",
        "lot_owned_opening",
    }
    assert _ids(engine, "positions") == positions_before


def test_a_rerun_changes_nothing(db):
    url, engine = db
    _seed(engine)
    command.upgrade(make_config(url), _AFTER)
    after = _ids(engine, "lots")

    command.downgrade(make_config(url), _BEFORE)
    command.upgrade(make_config(url), _AFTER)

    assert _ids(engine, "lots") == after


def test_a_book_without_grants_is_untouched(db):
    url, engine = db
    with engine.begin() as conn:
        conn.execute(text("INSERT OR IGNORE INTO books (id) VALUES ('default')"))
        _insert(conn, "lots", 0, _lot("lot_owned", "pos_any"))

    command.upgrade(make_config(url), _AFTER)

    assert set(_ids(engine, "lots")) == {"lot_owned"}
