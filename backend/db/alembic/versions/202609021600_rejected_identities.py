"""rejected_identities: a deleted transaction's external-ref identity, remembered.

## Why

``TransactionStore.by_external_ref`` reads live rows only, so deleting a transaction FREES its
``(source, ref)`` — the "unblock after an accidental delete" guarantee. It is also why a voided
hotel charge the owner deleted on 10 Aug was booked again by the next automation run on 11 Aug:
the email was re-delivered, the ref was free, the row came back. The loop stays armed for every
deleted row whose source can re-present it.

This table is the memory ``docs/adr/2026-08-04-a-rejected-import-identity-is-remembered.md``
accepted and never built, placed per ``docs/adr/2026-09-02-a-deleted-identity-is-remembered.md``:
the ledger's delete choke writes one record per (ref, movement) of the row it removes, and both
create chokes consult it OUTSIDE the ``force``/``allow_duplicate`` gate.

Schema-only, ``book_payload_table`` shape (revision 202608081826's own planning-store DDL) —
written directly here rather than imported from ``backend.db.tables`` so the migration stays
correct after any later change to the live ORM table. No prior JSON file, nothing to absorb.

Checkpoint-INCLUDED (not in ``ai.checkpoint._policy._EXCLUDED_TABLES``): a delete turn that
wrote a record must revert the record on ``/rewind`` along with the row. Portable and wiped
by ``/api/reset`` like every other planning-shaped user decision.

Revision ID: 202609021600_rejected_identities
Revises: 202609021430_backfill_external_ref_emitter
Create Date: 2026-09-02
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "202609021600_rejected_identities"
down_revision = "202609021430_backfill_external_ref_emitter"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "rejected_identities",
        sa.Column("book_id", sa.Text, sa.ForeignKey("books.id"), nullable=False),
        sa.Column("seq", sa.Integer, nullable=False),
        sa.Column("id", sa.Text, primary_key=True),
        sa.Column("payload", sa.JSON, nullable=False),
    )
    op.create_index(
        "ix_rejected_identities_book_seq", "rejected_identities", ["book_id", "seq"], unique=True
    )


def downgrade() -> None:
    op.drop_table("rejected_identities")
