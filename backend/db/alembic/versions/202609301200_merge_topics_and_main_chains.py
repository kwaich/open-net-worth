"""Merge point: two independent revision chains branched off
``202609231130_pending_position_fmv_basis`` concurrently. One is on the
``feat/intel-improvements`` branch: ``202609271200_rename_intelligence_watchlist`` (now a no-op),
then ``202609271300_topics_leave_watchlists`` (ADR ``2026-09-27-topics-are-followed-not-listed``).
The other is on ``main``: ``202609261500_owned_asset_purchase_point_derived``, then the
statement title/note/tags revisions, ``202609281300_data_hygiene_cleanup``,
``202609291200_automation_completed_status`` and ``202609291600_drop_transaction_taxable``.

Verified data-safe in either order before merging. The intel chain reads and writes only the
``watchlists`` payload (topics detach from lists, duplicates merge, the emptied topics-only list
is dropped). None of main's revisions touches ``watchlists``: they touch owned assets,
statements metadata, automations, transactions and the budget/category hygiene tables. Neither
chain reads a key the other writes. A database already stamped at either head therefore upgrades
correctly through this merge. Re-parenting the intel chain onto main's head instead would have
left a book already at ``202609271300`` believing it had applied main's revisions. So this
revision carries no operations of its own: it is a pure coalescing point, the same pattern as
``202609071500_merge_external_ids_and_seven_account_types``.

Revision ID: 202609301200_merge_topics_and_main_chains
Revises: 202609271300_topics_leave_watchlists, 202609291600_drop_transaction_taxable  # pii-lint: ok (alembic revision timestamp)
Create Date: 2026-09-30
"""

from __future__ import annotations

revision = "202609301200_merge_topics_and_main_chains"
down_revision = (
    "202609271300_topics_leave_watchlists",
    "202609291600_drop_transaction_taxable",
)
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
