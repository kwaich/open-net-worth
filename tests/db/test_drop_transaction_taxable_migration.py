"""Revision 202609291600 drops the transaction ``taxable`` flag — column, CHECK and payload
key — without the SQLite table rebuild cascading away the transaction's child rows."""

from __future__ import annotations

import json

from alembic import command
from sqlalchemy import create_engine, text

from backend.db.alembic_config import make_config

_FROM = "202609291200_automation_completed_status"
_TARGET = "202609291600_drop_transaction_taxable"

_PAYLOAD = {
    "id": "txn_gift",
    "date": "2026-09-01",
    "kind": "income",
    "description": "Birthday gift",
    "to_amount": "100.0",
    "splits": [{"category": "cat_a", "amount": 100.0}],
    "external_refs": [{"source": "email", "ref": "msg-1"}],
    "taxable": False,
}


def _seed(engine) -> None:
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT OR IGNORE INTO books (id, name, base_currency, created_at) "
                "VALUES ('default', '', 'SGD', '')"
            )
        )
        conn.execute(
            text(
                "INSERT INTO transactions (id, book_id, seq, date, kind, to_amount, source, "
                "description, taxable, payload) VALUES ('txn_gift', 'default', 0, "
                "'2026-09-01', 'income', '100.0', 'manual', 'Birthday gift', 0, :payload)"
            ),
            {"payload": json.dumps(_PAYLOAD)},
        )
        conn.execute(
            text(
                "INSERT INTO transaction_splits (split_id, book_id, txn_id, idx, amount, "
                "category, memo, payload) VALUES ('spl_1', 'default', 'txn_gift', 0, '100.0', "
                "'cat_a', '', :payload)"
            ),
            {"payload": json.dumps(_PAYLOAD["splits"][0])},
        )
        conn.execute(
            text(
                "INSERT INTO transaction_external_refs (ref_id, book_id, txn_id, idx, source, "
                "ref, payload) VALUES ('ref_1', 'default', 'txn_gift', 0, 'email', 'msg-1', "
                ":payload)"
            ),
            {"payload": json.dumps(_PAYLOAD["external_refs"][0])},
        )


def test_upgrade_drops_taxable_and_keeps_child_rows(tmp_path):
    db_path = tmp_path / "onw.db"
    config = make_config(f"sqlite:///{db_path}")
    command.upgrade(config, _FROM)
    engine = create_engine(f"sqlite:///{db_path}")
    try:
        _seed(engine)
        command.upgrade(config, _TARGET)

        with engine.connect() as conn:
            columns = {r[1] for r in conn.execute(text("PRAGMA table_info(transactions)"))}
            ddl = conn.execute(
                text("SELECT sql FROM sqlite_master WHERE name = 'transactions'")
            ).scalar_one()
            raw = conn.execute(
                text("SELECT payload FROM transactions WHERE id = 'txn_gift'")
            ).scalar_one()
            splits = conn.execute(text("SELECT split_id FROM transaction_splits")).fetchall()
            refs = conn.execute(text("SELECT ref_id FROM transaction_external_refs")).fetchall()

        assert "taxable" not in columns
        assert "taxable" not in ddl
        stored = json.loads(raw) if isinstance(raw, str) else raw
        assert stored == {k: v for k, v in _PAYLOAD.items() if k != "taxable"}
        assert [s[0] for s in splits] == ["spl_1"]
        assert [r[0] for r in refs] == ["ref_1"]
    finally:
        engine.dispose()


def test_downgrade_restores_a_null_column_and_keeps_child_rows(tmp_path):
    db_path = tmp_path / "onw.db"
    config = make_config(f"sqlite:///{db_path}")
    command.upgrade(config, _FROM)
    engine = create_engine(f"sqlite:///{db_path}")
    try:
        _seed(engine)
        command.upgrade(config, _TARGET)
        command.downgrade(config, _FROM)

        with engine.connect() as conn:
            taxable = conn.execute(
                text("SELECT taxable FROM transactions WHERE id = 'txn_gift'")
            ).scalar_one()
            n_splits = conn.execute(text("SELECT COUNT(*) FROM transaction_splits")).scalar_one()
            n_refs = conn.execute(
                text("SELECT COUNT(*) FROM transaction_external_refs")
            ).scalar_one()

        assert taxable is None
        assert (n_splits, n_refs) == (1, 1)
    finally:
        engine.dispose()
