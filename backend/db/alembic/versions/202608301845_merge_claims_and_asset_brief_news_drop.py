"""Merge point: two independent revisions branched off ``202608301500_intel_stories_split``
concurrently in this worktree — ``202608301645_drop_asset_brief_news_fields`` (this task,
RFC ``intel-signals-data-model`` §5) and ``202608301800_claims_registry`` (a separate,
concurrently-dispatched task building the ``claims`` table, ADR
``2026-08-30-claims-registry-cross-subject-push-dedup``). Neither revision's own DDL/data
work depends on the other's, so this merge carries no operations of its own — a pure
coalescing point, same pattern Alembic's own docs recommend for two independently-developed
branches off one parent.

Revision ID: 202608301845_merge_claims_and_asset_brief_news_drop
Revises: 202608301645_drop_asset_brief_news_fields, 202608301800_claims_registry
Create Date: 2026-08-30
"""

from __future__ import annotations

revision = "202608301845_merge_claims_and_asset_brief_news_drop"
down_revision = ("202608301645_drop_asset_brief_news_fields", "202608301800_claims_registry")
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
