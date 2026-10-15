"""Revision 202609120900 removes the decorative account colour payload key."""

from __future__ import annotations

import json

from alembic import command
from sqlalchemy import create_engine, text

from backend.db.alembic_config import make_config

_FROM = "202609111200_monthly_retirement_spending"
_TARGET = "202609120900_remove_account_color"


def _payload(engine, account_id: str) -> dict:
    with engine.connect() as conn:
        raw = conn.execute(
            text("SELECT payload FROM accounts WHERE id = :id"), {"id": account_id}
        ).scalar_one()
    return json.loads(raw) if isinstance(raw, str) else raw


def test_upgrade_removes_account_color_and_keeps_account_facts(tmp_path):
    db_path = tmp_path / "onw.db"
    config = make_config(f"sqlite:///{db_path}")
    command.upgrade(config, _FROM)
    engine = create_engine(f"sqlite:///{db_path}")
    try:
        payload = {
            "id": "acc-1",
            "name": "Everyday account",
            "kind": "asset",
            "type": "cash",
            "currency": "SGD",
            "color": "#3b86dd",
            "note": "Bills",
        }
        with engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT OR IGNORE INTO books (id, name, base_currency, created_at) "
                    "VALUES ('default', '', 'SGD', '')"
                )
            )
            conn.execute(
                text(
                    "INSERT INTO accounts "
                    "(id, book_id, seq, name, kind, type, currency, archived, opening_txn_id, payload) "
                    "VALUES (:id, 'default', 1, :name, 'asset', 'cash', 'SGD', 0, NULL, :payload)"
                ),
                {"id": payload["id"], "name": payload["name"], "payload": json.dumps(payload)},
            )

        command.upgrade(config, _TARGET)

        assert _payload(engine, "acc-1") == {
            "id": "acc-1",
            "name": "Everyday account",
            "kind": "asset",
            "type": "cash",
            "currency": "SGD",
            "note": "Bills",
        }
    finally:
        engine.dispose()
