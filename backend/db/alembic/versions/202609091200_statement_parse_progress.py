"""statement_parse_progress — the async-preview progress seam (B20), + statements_meta.status
now populated.

``status`` already exists on ``statements_meta`` (revision 0044's brief), reserved verbatim in
``tables.py``'s own comment: "kept as a column because the brief asks for it, reserved for a
derived/summary value a future store rewrite computes — nullable, unpopulated by this
revision." This IS that future rewrite: ``status`` now carries the parse lifecycle —
``parsing`` | ``ready`` | ``failed`` | ``interrupted`` — written by
``services.statements.parse_progress``'s persistence hook at each stage boundary and on
done/error (``StatementStore.set_parse_status``). No CheckConstraint, matching
``import_batches.status``'s own precedent (a comment-documented enum, no CHECK) — a NULL value
is a legacy/pre-B20 record, not a violation, and stays meaningful (§ADR
2026-09-09-statement-parse-status-lives-on-the-record).

DESIGN CHANGE from this revision's first cut (still unmerged — rewritten in place rather than
superseded by a second revision): the registry's own snapshot dict — ``{stages, started_at,
elapsed_s, done, error}`` — does NOT live inline on ``statements_meta`` any more. A multi-page
scan calls the persistence hook roughly once per stage/page (~10x for a typical statement),
and ``StatementStore.update_by_id`` is a full read/reinline/re-extract-parsed-rows/write cycle
— fine for the rare, real edits it was built for, expensive to pay ten times per parse for a
JSON blob nobody else on the row cares about. ``statement_parse_progress`` is a narrow side
table instead: one row per statement, upserted in place (no history, no growth), FK'd
``ON DELETE CASCADE`` so deleting a statement drops its progress row for free.
``statements_meta.status`` changes only on an actual TRANSITION (parsing → ready/failed/
interrupted), via a narrow single-column UPDATE — never through ``update_by_id``.

Checkpoint-EXCLUDED (``ai.checkpoint._EXCLUDED_TABLES``) — a rewind must not resurrect stale
read-progress for a statement whose parse has since finished, same reasoning as `price_points`/
`evidence`/`asset_brief`. Not portable (absent from ``db.portable_bridge.BRIDGED``) — a
derived, ephemeral cache with no export/import role, same precedent.

Revision ID: 202609091200_statement_parse_progress
Revises: 202609071500_merge_external_ids_and_seven_account_types
Create Date: 2026-09-09
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "202609091200_statement_parse_progress"
down_revision = "202609071500_merge_external_ids_and_seven_account_types"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "statement_parse_progress",
        sa.Column(
            "statement_id",
            sa.Text,
            sa.ForeignKey("statements_meta.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("progress", sa.JSON, nullable=False),
        sa.Column("updated_at", sa.Text, nullable=False),
    )


def downgrade() -> None:
    op.drop_table("statement_parse_progress")
