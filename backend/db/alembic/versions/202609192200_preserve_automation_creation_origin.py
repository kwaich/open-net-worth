"""Preserve proven automation creation origins before their audit events are pruned.

Only creation evidence qualifies; automation edits do not change a row's origin.
No financial fields or external identities change. Unprovable origins remain untouched.
"""

import sqlalchemy as sa
from alembic import op

revision = "202609192200_preserve_automation_creation_origin"
down_revision = "202609121100_rename_account_payload_to_data"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    transactions = sa.table(
        "transactions",
        sa.column("id", sa.Text),
        sa.column("book_id", sa.Text),
        sa.column("payload", sa.JSON),
    )
    audit = sa.table(
        "txn_audit",
        sa.column("txn_id", sa.Text),
        sa.column("book_id", sa.Text),
        sa.column("surface", sa.Text),
        sa.column("payload", sa.JSON),
    )
    proof = sa.exists(
        sa.select(audit.c.txn_id).where(
            audit.c.book_id == transactions.c.book_id,
            audit.c.txn_id == transactions.c.id,
            audit.c.surface == "automation",
            audit.c.payload["op"].as_string() == "create",
        )
    )
    for row in conn.execute(sa.select(transactions).where(proof)).all():
        if row.payload.get("source") == "automation":
            continue
        conn.execute(
            transactions.update()
            .where(
                transactions.c.id == row.id,
                transactions.c.book_id == row.book_id,
            )
            .values(payload={**row.payload, "source": "automation"})
        )


def downgrade() -> None:
    # Origin remains valid on older code; the overwritten writer cannot be reconstructed.
    pass
