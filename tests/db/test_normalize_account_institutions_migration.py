"""Regression coverage for the one-way platform -> institution migration."""

from __future__ import annotations

import json

from alembic import command
from sqlalchemy import create_engine, text

from backend.db.alembic_config import make_config
from backend.db.tables import DEFAULT_BOOK_ID

_FROM = "202609120900_remove_account_color"
_TARGET = "202609121000_normalize_account_institutions"


def test_upgrade_creates_shared_institution_and_removes_platform(tmp_path):
    config = make_config(f"sqlite:///{tmp_path / 'onw.db'}")
    command.upgrade(config, _FROM)
    engine = create_engine(config.get_main_option("sqlalchemy.url"))
    try:
        with engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO accounts "
                    "(id, book_id, seq, name, kind, type, currency, archived, opening_txn_id, payload) "
                    "VALUES (:id, :book_id, :seq, :name, 'asset', 'cash', 'SGD', 0, NULL, :payload)"
                ),
                [
                    {
                        "id": "acc_sgd",
                        "book_id": DEFAULT_BOOK_ID,
                        "seq": 0,
                        "name": "DBS SGD",
                        "payload": json.dumps(
                            {"id": "acc_sgd", "name": "DBS SGD", "platform": "DBS"}
                        ),
                    },
                    {
                        "id": "acc_usd",
                        "book_id": DEFAULT_BOOK_ID,
                        "seq": 1,
                        "name": "DBS USD",
                        "payload": json.dumps(
                            {"id": "acc_usd", "name": "DBS USD", "platform": " dbs "}
                        ),
                    },
                ],
            )

        command.upgrade(config, _TARGET)

        with engine.connect() as conn:
            institutions = conn.execute(
                text("SELECT id, normalized_name, payload FROM institutions")
            ).fetchall()
            accounts = conn.execute(text("SELECT id, payload FROM accounts ORDER BY id")).fetchall()
        assert len(institutions) == 1
        assert institutions[0].normalized_name == "dbs"
        assert json.loads(institutions[0].payload)["name"] == "DBS"
        payloads = [json.loads(row.payload) for row in accounts]
        assert {payload["institution_id"] for payload in payloads} == {institutions[0].id}
        assert all("platform" not in payload for payload in payloads)
    finally:
        engine.dispose()
