"""sql_query_log: the audit ring for Lucius's read-only SQL access.

## Why

``docs/superpowers/specs/2026-08-23-lucius-sql-access-design.md`` gives Lucius two read-only
tools (``run_sql_query`` / ``describe_db_schema``) so he can answer data questions the ~200
fixed-projection tools cannot reach. A capability that lets a model compose arbitrary reads
over the whole database needs an owner-readable record of what it actually asked — this table
is that record, surfaced in Settings > Developer beside the kill switch.

Schema-only, mirroring revision 0049's own ``budget_engine_state`` shape: the DDL is written
directly in this file rather than ``from backend.db.tables import sql_query_log`` — a migration
must stay correct even after some future change to the live ORM table (revision 0048's own
docstring names this rule; every prior ``create_table`` revision in this history already
follows it). There is no prior JSON file and no existing row to absorb, so this is create-only
— no cutover step, nothing stamped in ``json_cutovers``.

The plain flat-list ``(seq, id, payload)`` shape and the Tier-2 classification match the
thirteen logs/telemetry tables from plans 4c and wave 5B: NO ``book_id`` (an operational record
of what was asked of the database, not book-scoped user data), checkpoint-EXCLUDED
(``ai.checkpoint._policy._EXCLUDED_TABLES``), and — mirroring ``tool_call_log`` exactly —
NEITHER a ``db/portable_bridge.py`` BRIDGED entry NOR a ``_WIPE_FNS`` one, because a runtime
log has no export or reset role to preserve.

One deliberate departure from ``tool_call_log``, whose store docstring pins "SIZES AND SHAPES
ONLY — never raw arguments": this ring stores the SQL TEXT VERBATIM (capped at
``sql_query_log_store.MAX_SQL_CHARS``). For a telemetry ring the arguments are incidental, but
here the statement IS the auditable action — the same reason ``mcp_write_log`` keeps the real
call rather than its length. It is reachable only through an auth-gated endpoint that sets
``Cache-Control: no-store``.

Revision ID: 0050
Revises: 0049
Create Date: 2026-08-23
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "202608230747_sql_query_log"
down_revision = "202608230353_budget_engine_state"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "sql_query_log",
        sa.Column("seq", sa.Integer, nullable=False),
        sa.Column("id", sa.Text, primary_key=True),
        sa.Column("payload", sa.JSON, nullable=False),
    )
    op.create_index("ix_sql_query_log_seq", "sql_query_log", ["seq"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_sql_query_log_seq", table_name="sql_query_log")
    op.drop_table("sql_query_log")
