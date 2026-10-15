"""connector_snapshots: bank/broker provenance snapshots (RFC connectors-as-data-sources §C).

Schema-only: brand new table, no prior JSON file to cut over (like revision
``202608161811_receipt_store``'s own ``receipts``/``transaction_receipts``, not the
JSON->DB cutover shape of ``email_snapshots``' earlier revision). Bare ``(seq, id,
payload)`` ``payload_table()`` shape, ``id_field="key"`` at the store layer
(``db/connector_snapshot_store.py``) — the physical primary key column stays ``id``, it
just carries ``f"{source}:{ref}"`` values instead of a bare id.

Checkpoint-INCLUDED (default — not added to ``ai.checkpoint._policy._EXCLUDED_TABLES``)
and portable (``db/portable_bridge.py``), exactly like ``email_snapshots``.

Revision ID: 202609051200_connector_snapshots
Revises: 202609032128_flip_plan_event_amount_sign
Create Date: 2026-09-05
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "202609051200_connector_snapshots"
down_revision = "202609032128_flip_plan_event_amount_sign"
branch_labels = None
depends_on = None

_TABLE = "connector_snapshots"


def upgrade() -> None:
    op.create_table(
        _TABLE,
        sa.Column("seq", sa.Integer, nullable=False),
        sa.Column("id", sa.Text, primary_key=True),
        sa.Column("payload", sa.JSON, nullable=False),
    )
    op.create_index(f"ix_{_TABLE}_seq", _TABLE, ["seq"], unique=True)


def downgrade() -> None:
    op.drop_table(_TABLE)
