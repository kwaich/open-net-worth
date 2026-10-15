"""claims: cross-subject push-dedup registry.

## Why

RFC ``docs/rfc/intel-signals-data-model.md`` §4 recommended NO-GO on a ``claims``/`group_key`
table, citing ADR ``2026-08-28-cross-subject-fan-out-and-asset-brief-consolidation`` twice
deliberately rejecting a wider cross-subject dedup mechanism. The owner reviewed that RFC's
summary and OVERRODE it — see ``docs/adr/2026-08-30-claims-registry-cross-subject-push-dedup.md``
for the full reasoning, including the proven incident this table exists for (a linked topic-side
and asset-side ``intel_stories`` row each independently pushed the same real corporate
development, ~10 hours apart — the ADR's own same-tick-only pairing (``_find_linked_pairs``)
cannot catch a cross-TICK duplicate by construction).

A ``claims`` row is one real-world fact a story's push decision judged worth communicating.
``group_key`` (``services.claims.group_key_for``) is the subject-agnostic identity that lets a
DIFFERENT story row about the SAME underlying fact resolve to the SAME claim — deliberately
narrow: only the news/development-tracking story types (``asset``/``topic``/``risk``/
``opportunity``/``macro``) ever get a group_key, and only when they resolve to EXACTLY ONE
instrument id. A ``price_alert``/``daily_digest`` row, or a multi-instrument risk/macro finding,
never gets one — the RFC's own scoping caution (a price-alert-type story is never a duplicate of
an M&A-topic story on the same ticker) holds structurally, not by convention.

Schema-only, mirroring revision ``202608230747_sql_query_log``'s own shape: the DDL is written
directly in this file (not imported from ``backend.db.tables``, per revision ``0048``'s standing
rule that a migration must stay correct even after a later ORM change). There is no prior JSON
file and no existing row to absorb — create-only, no cutover, nothing stamped in
``json_cutovers``.

NO FK to ``intel_stories`` — ``story_ids`` is an opaque id list, the same loose-coupling
convention ``intel_stories.instrument_id`` already uses (no FK there either): a claim outlives
any single story and must never cascade-delete when one of its stories is deleted.

Revision ID: 202608301800
Revises: 202608301500
Create Date: 2026-08-30
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "202608301800_claims_registry"
down_revision = "202608301500_intel_stories_split"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "claims",
        sa.Column("id", sa.Text, primary_key=True),
        sa.Column("seq", sa.Integer, nullable=False),
        sa.Column("group_key", sa.Text, nullable=False),
        sa.Column("statement", sa.Text, nullable=False, server_default=""),
        sa.Column("status", sa.Text, nullable=False, server_default="active"),
        sa.Column("duplicate_of_claim_id", sa.Text, nullable=True),
        sa.Column("contradicts_claim_id", sa.Text, nullable=True),
        sa.Column("sources", sa.JSON, nullable=False, server_default="[]"),
        sa.Column("story_ids", sa.JSON, nullable=False, server_default="[]"),
        sa.Column("materiality", sa.Text, nullable=True),
        sa.Column("decision_relevant", sa.Integer, nullable=False, server_default="1"),
        sa.Column("created_at", sa.Text, nullable=False),
        sa.Column("confirmed_at", sa.Text, nullable=True),
        sa.Column("pushed_at", sa.Text, nullable=True),
        sa.Column("payload", sa.JSON, nullable=False),
        sa.CheckConstraint(
            "status IN ('active','superseded','flagged')",
            name=sa.schema.conv("ck_claims_status"),
        ),
        sa.CheckConstraint(
            "decision_relevant IN (0,1)", name=sa.schema.conv("ck_claims_decision_relevant_bool")
        ),
    )
    op.create_index("ix_claims_seq", "claims", ["seq"], unique=True)
    op.create_index("ix_claims_group_key", "claims", ["group_key"])


def downgrade() -> None:
    op.drop_index("ix_claims_group_key", table_name="claims")
    op.drop_index("ix_claims_seq", table_name="claims")
    op.drop_table("claims")
