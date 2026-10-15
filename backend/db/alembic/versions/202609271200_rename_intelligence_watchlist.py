"""Rename the "Intelligence" watchlist to "Macro & topics" — superseded, now a no-op.

This revision renamed the list behind Intel's topics tab. The next one
(``202609271300_topics_leave_watchlists``) takes topics out of watchlists altogether, deleting
that list once it holds no tickers — so renaming it first only mattered for a list that ALSO held
tickers, where it produced a ticker list called "Macro & topics" beside the "Topics & macro" tab.
The rename is therefore dropped; the revision stays in the chain because it may already have run
at boot (removing it would leave that database "ahead of the code"), and 1300 renames such a
kept list back to "Intelligence".

Revision ID: 202609271200_rename_intelligence_watchlist
Revises: 202609231130_pending_position_fmv_basis
Create Date: 2026-09-27
"""

from __future__ import annotations

revision = "202609271200_rename_intelligence_watchlist"
down_revision = "202609231130_pending_position_fmv_basis"
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
