"""Revision 202609231130 stamps the vest-date FMV basis on pending-release positions."""

from __future__ import annotations

import json

import pytest
from alembic import command
from sqlalchemy import create_engine, text

from backend.db.alembic_config import make_config

_FROM = "202609211200_remove_category_taxable_default"
_TARGET = "202609231130_pending_position_fmv_basis"


def _insert(conn, table: str, seq: int, payload: dict) -> None:
    conn.execute(
        text(f"INSERT INTO {table} (id, book_id, seq, payload) VALUES (:id, 'default', :seq, :p)"),
        {"id": payload["id"], "seq": seq, "p": json.dumps(payload)},
    )


def _read(conn, table: str, id_val: str) -> dict:
    raw = conn.execute(
        text(f"SELECT payload FROM {table} WHERE id = :id"), {"id": id_val}
    ).scalar_one()
    return json.loads(raw) if isinstance(raw, str) else raw


def test_upgrade_backfills_the_parked_basis_and_leaves_everything_else_alone(tmp_path):
    """Scar (2026-09-23): KSTL's 15 Sep tranche parked 488.75 units at cost_basis=None."""
    db_path = tmp_path / "onw.db"
    config = make_config(f"sqlite:///{db_path}")
    command.upgrade(config, _FROM)
    engine = create_engine(f"sqlite:///{db_path}")
    try:
        with engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT OR IGNORE INTO books (id, name, base_currency, created_at) "
                    "VALUES ('default', '', 'SGD', '')"
                )
            )
            _insert(conn, "grants", 1, {"id": "g1", "pending_position_id": "pos_pending"})
            _insert(conn, "grants", 2, {"id": "g2", "pending_position_id": "pos_mixed"})
            _insert(conn, "grants", 3, {"id": "g3", "pending_position_id": None})
            _insert(
                conn,
                "positions",
                1,
                {"id": "pos_pending", "quantity": 977.5, "cost_basis": None, "bucket": "liquid"},
            )
            _insert(conn, "positions", 2, {"id": "pos_mixed", "quantity": 20.0, "cost_basis": 1.0})
            _insert(
                conn, "positions", 3, {"id": "pos_liquid", "quantity": 1230.0, "cost_basis": 32.35}
            )
            # Two parked vests on pos_pending, one already released (must not count), one
            # parked on a position whose vests disagree on currency.
            _insert(
                conn,
                "vest_events",
                1,
                {
                    "id": "v1",
                    "pending_release": True,
                    "pending_position_id": "pos_pending",
                    "units": 488.75,
                    "fmv_per_unit": 36.75,
                    "fmv_currency": "EUR",
                },
            )
            _insert(
                conn,
                "vest_events",
                2,
                {
                    "id": "v2",
                    "pending_release": True,
                    "pending_position_id": "pos_pending",
                    "units": 488.75,
                    "fmv_per_unit": 40.25,
                    "fmv_currency": "eur",
                },
            )
            _insert(
                conn,
                "vest_events",
                3,
                {
                    "id": "v3",
                    "pending_release": False,
                    "released_on": "2026-08-12",
                    "pending_position_id": "pos_pending",
                    "units": 488.75,
                    "fmv_per_unit": 999.0,
                    "fmv_currency": "EUR",
                },
            )
            for i, ccy in ((4, "USD"), (5, "SGD")):
                _insert(
                    conn,
                    "vest_events",
                    i,
                    {
                        "id": f"v{i}",
                        "pending_release": True,
                        "pending_position_id": "pos_mixed",
                        "units": 10.0,
                        "fmv_per_unit": 5.0,
                        "fmv_currency": ccy,
                    },
                )

        command.upgrade(config, _TARGET)

        with engine.connect() as conn:
            pending = _read(conn, "positions", "pos_pending")
            mixed = _read(conn, "positions", "pos_mixed")
            liquid = _read(conn, "positions", "pos_liquid")
        assert pending["cost_basis"] == pytest.approx(38.5), "weighted over PARKED vests only"
        assert pending["cost_currency"] == "EUR"
        assert pending["quantity"] == 977.5 and pending["bucket"] == "liquid"
        assert mixed["cost_basis"] is None, "currencies disagree — unknown, never averaged"
        assert liquid == {"id": "pos_liquid", "quantity": 1230.0, "cost_basis": 32.35}
    finally:
        engine.dispose()
