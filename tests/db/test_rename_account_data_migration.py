"""The account document column is renamed without changing its JSON content."""

import json

from alembic import command
from sqlalchemy import create_engine, text

from backend.db.alembic_config import make_config
from backend.db.tables import DEFAULT_BOOK_ID


def test_upgrade_renames_account_payload_to_data(tmp_path):
    config = make_config(f"sqlite:///{tmp_path / 'onw.db'}")
    command.upgrade(config, "202609121000_normalize_account_institutions")
    engine = create_engine(config.get_main_option("sqlalchemy.url"))
    try:
        document = {"id": "acc_1", "name": "Checking", "type": "cash", "currency": "SGD"}
        with engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO accounts "
                    "(id, book_id, seq, name, kind, type, currency, archived, opening_txn_id, payload) "
                    "VALUES ('acc_1', :book_id, 0, 'Checking', 'asset', 'cash', 'SGD', 0, NULL, :payload)"
                ),
                {"book_id": DEFAULT_BOOK_ID, "payload": json.dumps(document)},
            )
        command.upgrade(config, "head")
        with engine.connect() as conn:
            columns = {row[1] for row in conn.execute(text("PRAGMA table_info(accounts)"))}
            stored = conn.execute(text("SELECT data FROM accounts WHERE id = 'acc_1'")).scalar_one()
        assert "data" in columns
        assert "payload" not in columns
        assert json.loads(stored) == document
    finally:
        engine.dispose()
