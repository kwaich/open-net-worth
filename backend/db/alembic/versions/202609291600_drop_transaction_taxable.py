"""Drop the transaction-level ``taxable`` flag — the promoted column, its CHECK and the
payload key.

Nothing has read it since the tax feature was removed (ADR 2026-08-25) and category
taxability went with it (ADR 2026-09-21); the last consumers — Planning's unrendered
``taxable_received``/``taxable_ytd`` totals — go in the same change. A future tax engine
designs its own contract (ADR 2026-09-29-drop-transaction-taxable).

The SQLite cascade-on-recreate hazard applies: dropping a column referenced by a CHECK
means batch mode rebuilds ``transactions``, and with ``PRAGMA foreign_keys=ON`` the internal
``DROP TABLE`` cascades into ``transaction_splits``/``transaction_external_refs``. Both
children are dumped raw before the rebuild and restored verbatim after it, then checked
(same mechanism as ``202608080315_money_decimal``, whose docstring has the full finding).
Postgres alters in place and needs none of it.

``downgrade()`` re-adds the nullable column and its CHECK; the discarded per-transaction
values cannot be reconstructed, so every row reads ``NULL`` (= the old income default).

Revision ID: 202609291600_drop_transaction_taxable
Revises: 202609291200_automation_completed_status
Create Date: 2026-09-29
"""

from __future__ import annotations

import json

import sqlalchemy as sa
from alembic import op

revision = "202609291600_drop_transaction_taxable"
down_revision = "202609291200_automation_completed_status"
branch_labels = None
depends_on = None

_CHECK = "ck_transactions_taxable_bool"
_CHILDREN = (("transaction_splits", "split_id"), ("transaction_external_refs", "ref_id"))


def _dump(conn: sa.Connection, table_name: str) -> tuple[list[str], list[dict]]:
    res = conn.execute(sa.text(f"SELECT * FROM {table_name}"))  # noqa: S608 - fixed name
    keys = list(res.keys())
    return keys, [dict(zip(keys, row)) for row in res.fetchall()]


def _restore(conn: sa.Connection, table_name: str, keys: list[str], rows: list[dict]) -> None:
    if not rows:
        return
    cols = ", ".join(keys)
    params = ", ".join(f":{k}" for k in keys)
    conn.execute(sa.text(f"INSERT INTO {table_name} ({cols}) VALUES ({params})"), rows)  # noqa: S608


def _rebuild_transactions(conn: sa.Connection, alter) -> None:
    """Run ``alter(batch_op)`` on ``transactions`` with the SQLite cascade guard around it."""
    is_sqlite = conn.dialect.name == "sqlite"
    dumps = {name: _dump(conn, name) for name, _ in _CHILDREN} if is_sqlite else {}
    with op.batch_alter_table("transactions") as batch_op:
        alter(batch_op)
    for name, pk in _CHILDREN if is_sqlite else ():
        keys, before = dumps[name]
        _restore(conn, name, keys, before)
        _, after = _dump(conn, name)
        if {r[pk]: r for r in after} != {r[pk]: r for r in before}:
            raise RuntimeError(f"drop taxable: {name} did not survive the rebuild — aborting")


def _strip_payload_key(conn: sa.Connection) -> None:
    rows = conn.execute(sa.text("SELECT id, payload FROM transactions")).fetchall()
    for txn_id, raw in rows:
        payload = json.loads(raw) if isinstance(raw, str) else dict(raw or {})
        if "taxable" not in payload:
            continue
        payload.pop("taxable")
        conn.execute(
            sa.text("UPDATE transactions SET payload = :payload WHERE id = :id"),
            {"id": txn_id, "payload": json.dumps(payload)},
        )


def upgrade() -> None:
    conn = op.get_bind()
    _strip_payload_key(conn)

    def _drop(batch_op) -> None:
        batch_op.drop_constraint(op.f(_CHECK), type_="check")
        batch_op.drop_column("taxable")

    _rebuild_transactions(conn, _drop)


def downgrade() -> None:
    conn = op.get_bind()

    def _add(batch_op) -> None:
        batch_op.add_column(sa.Column("taxable", sa.Integer, nullable=True))
        batch_op.create_check_constraint(op.f(_CHECK), "taxable IS NULL OR taxable IN (0,1)")

    _rebuild_transactions(conn, _add)
