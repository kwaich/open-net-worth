"""Revision 202609211200 removes category-level tax metadata from stored payloads."""

from __future__ import annotations

import json

from alembic import command
from sqlalchemy import create_engine, text

from backend.db.alembic_config import make_config

_FROM = "202609201200_price_movement_notes"
_TARGET = "202609211200_remove_category_taxable_default"


def test_upgrade_removes_taxable_default_and_keeps_category_facts(tmp_path):
    db_path = tmp_path / "onw.db"
    config = make_config(f"sqlite:///{db_path}")
    command.upgrade(config, _FROM)
    engine = create_engine(f"sqlite:///{db_path}")
    try:
        payload = {
            "id": "cat_salary",
            "label": "Salary",
            "kind": "income",
            "color": "#34b08a",
            "taxable_default": False,
            "hidden": True,
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
                    "INSERT INTO categories (id, book_id, seq, payload) "
                    "VALUES (:id, 'default', 1, :payload)"
                ),
                {"id": payload["id"], "payload": json.dumps(payload)},
            )

        command.upgrade(config, _TARGET)

        with engine.connect() as conn:
            raw = conn.execute(
                text("SELECT payload FROM categories WHERE id = :id"),
                {"id": payload["id"]},
            ).scalar_one()
        stored = json.loads(raw) if isinstance(raw, str) else raw
        assert stored == {
            "id": "cat_salary",
            "label": "Salary",
            "kind": "income",
            "color": "#34b08a",
            "hidden": True,
        }
    finally:
        engine.dispose()
