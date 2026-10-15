"""logs schema: automation_runs, automation_runtime, batch_ledger, harness_suggestions,
mcp_write_log, model_telemetry, pending_analysis, run_history, tool_call_log — the 9
Tier-2 logs/telemetry stores (plan 4c, task 1). Schema only, no data absorption (0014).

Every table is the plain (seq, id, payload) shape — mirrors backend.db.payload_store.
payload_table() / 0011's asset_brief block exactly. NO book_id, NO FK: these are
operational logs, not book-scoped user data (spec:196-197).

Revision ID: 0013
Revises: 0012
Create Date: 2026-08-09
"""

import sqlalchemy as sa
from alembic import op

revision = "202608090721_logs_schema"
down_revision = "202608090350_investments_cutover"
branch_labels = None
depends_on = None

_TABLES = (
    "automation_runs",
    "automation_runtime",
    "batch_ledger",
    "harness_suggestions",
    "mcp_write_log",
    "model_telemetry",
    "pending_analysis",
    "run_history",
    "tool_call_log",
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
