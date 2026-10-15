"""session_messages.extra: a message is not just role/content/ts.

Revision 0051 promoted three fields to columns and dropped everything else on the message
dict. That was wrong: the skill host rides ``_onw_skill`` / ``_onw_skill_activity_id`` /
``_onw_skill_deactivation`` on transcript entries, and the backfill silently discarded them —
19 real fields across 19 messages on the owner's instance, caught by
``tests/test_skill_host_runtime.py`` rather than by anything in the migration itself.

``extra`` carries every key that is not ``role``/``content``/``ts``, merged back on read so
the dict a caller gets is byte-identical to the one it stored. The three promoted columns stay
promoted — they are what queries and ordering actually use.

Schema-only. The 19 lost fields are restored separately by
``backend/scripts/repair_session_message_extras.py``, which reads them from a pre-0051
snapshot: a migration cannot recover data that a previous migration already dropped.

Revision ID: 0052
Revises: 0051
Create Date: 2026-08-24
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "202608241938_session_message_extra"
down_revision = "202608241937_session_messages"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("session_messages", sa.Column("extra", sa.JSON, nullable=True))


def downgrade() -> None:
    op.drop_column("session_messages", "extra")
