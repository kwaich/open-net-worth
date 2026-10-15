"""wave5b logs schema: tool_discovery_log, cost_log, cost_log_daily, agent_findings — 4
more Tier-2 logs/telemetry stores (Wave 5B). Schema only, no data absorption (0023).

Same (seq, id, payload) shape as the nine Tier-2 logs tables from revision 0013 — see
that revision's own docstring for the full reasoning. NO book_id, NO FK: operational/
runtime logs, not book-scoped user data, exactly like their nine siblings.
``agent_findings`` is vestigial (the Agent product that wrote it was retired) but keeps
the identical shape regardless — see ``db/agent_finding_store.py``.

Revision ID: 0022
Revises: 0021
Create Date: 2026-08-10
"""

import sqlalchemy as sa
from alembic import op

revision = "202608110402_wave5b_logs_schema"
down_revision = "202608110401_profile_cutover"
branch_labels = None
depends_on = None

_TABLES = (
    "tool_discovery_log",
    "cost_log",
    "cost_log_daily",
    "agent_findings",
)


def upgrade() -> None:
    for name in _TABLES:
        op.create_table(
            name,
            sa.Column("seq", sa.Integer, nullable=False),
            sa.Column("id", sa.Text, primary_key=True),
            sa.Column("payload", sa.JSON, nullable=False),
        )
        op.create_index(f"ix_{name}_seq", name, ["seq"], unique=True)


def downgrade() -> None:
    for name in reversed(_TABLES):
        op.drop_table(name)
