"""wave5d2b schema: pending_plans, mcp_pending_calls, chat_pairing — the 3 Wave 5D2b
(JSON->DB, permission/nonce machinery) tables. Schema only, no data absorption (0031).

All three ride the generic (seq, id, payload) keyed-dict shape — the same shape
``skills_state`` (revision 0024) uses: ``id`` is a TOKEN, a tool_use_id, a NONCE, or
(``chat_pairing``) a "<connector>:<sender_id>" composite key, never a field inside the
payload dict itself. NO book_id — instance-local runtime queues, not book-scoped user
data.

The other three stores named in this wave's original scope — ``mcp_grant``,
``code_grants``, ``mcp_proposals`` — are NOT part of this schema, deliberately: all three
were already deleted from the codebase (store, API, service, AND json file) well before
this migration started. ``mcp_grant``/``mcp_proposals`` (the inbound-MCP T0/T1/T2
grant/proposal tier) were removed in 50626ce2 ("refactor(mcp): remove inbound
authorization tiering", 2026-07-18) — that commit's own message confirms
``mcp_grant.json`` "held no secret" even when it existed. ``code_grants`` (run_code's
per-conversation "Allow for this conversation" grant) was removed in f581b86d ("feat!:
delete CodeGrantStore with the conversation grant tier", 2026-08-02), superseded by the
durable ``code_execution_auto_run`` flag. There is nothing left to migrate for any of the
three — see ``db/tables.py``'s own Wave 5D2b comment and the wave-5 plan doc's progress
ledger for the full investigation trail.

Revision ID: 0030
Revises: 0029
Create Date: 2026-08-10
"""

import sqlalchemy as sa
from alembic import op

revision = "202608110410_wave5d2b_schema"
down_revision = "202608110409_wave5d2a_cutover"
branch_labels = None
depends_on = None

_PAYLOAD_TABLES = ("pending_plans", "mcp_pending_calls", "chat_pairing")


def upgrade() -> None:
    for name in _PAYLOAD_TABLES:
        op.create_table(
            name,
            sa.Column("seq", sa.Integer, nullable=False),
            sa.Column("id", sa.Text, primary_key=True),
            sa.Column("payload", sa.JSON, nullable=False),
        )
        op.create_index(f"ix_{name}_seq", name, ["seq"], unique=True)


def downgrade() -> None:
    for name in reversed(_PAYLOAD_TABLES):
        op.drop_table(name)
