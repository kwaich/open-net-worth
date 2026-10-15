"""Drop `mcp_pending_calls` — external-MCP confirm→apply migrates onto `pending_plans`.

Not a Wave 5 sub-wave (Wave 5 was already COMPLETE at revision 0038) — a targeted
security-review follow-up (ADR
`2026-08-22-external-mcp-confirm-migrates-onto-generic-resolve.md`).

External-MCP's confirm→apply used to stage `{server_id, tool, args}` under a freshly minted
NONCE in this table (`mcp_pending_calls`, `db/mcp_pending_call_store.py`'s `McpPendingCallStore`)
and redeem it through a dedicated model-callable tool, `apply_external_mcp_call(nonce)`. Both
are DELETED in the same change: `backend/mcp/client_manager.py::call_external_tool` now stages
into `pending_plans` — the SAME `tool_use_id`-keyed table every other gated tool in the app
already used (RFC 2026-07-22) — and resolution happens through the existing
`POST /api/chat/{id}/resolve` → `ai.assistant.resolve_pending_tool_use` path, never a second
tool call. `apply_external_mcp_call` was also independently permission-gated on top of the
proposal card it existed to redeem (a real double-confirm bug); removing the tool removes that
gate too, since there is no longer a second tool call to gate.

NO DATA MIGRATION: every row in this table is a single-use, in-flight confirm-card stage —
by definition either already popped (applied or expired) or a card genuinely still awaiting a
human tap. There is no `pending_plans` row shape a stale `mcp_pending_calls` entry could
correctly become (the two tables never shared a key space — one is nonce-keyed, the other
tool_use_id-keyed — and a `tool_use_id` is meaningless without the paused chat turn it belongs
to, which lives in `sessions`, not here). Any card genuinely mid-flight at deploy time is lost —
the confirm card the user was looking at, at worst, produces a stale one that resolve refuses
as "already answered, or superseded" (indistinguishable from any other stale pending entry).
Accepted: this table is checkpoint-EXCLUDED and NON_RESTORABLE precisely because its rows are
never meant to survive a boundary like this one — it is a runtime queue, not user data (mirrors
revision 0040's `tool_grants` drop, which also carried no forward data migration for its
non-standing rows).

`core.sensitive.NON_RESTORABLE_TABLES` and `ai.checkpoint._policy._EXCLUDED_TABLES` both drop
their `"mcp_pending_calls"` membership in this same change (Python source, not this migration —
see `backend/core/sensitive.py` / `backend/ai/checkpoint/_policy.py`) so
`services.backup`'s `_non_restorable_tables()` never does `metadata.tables["mcp_pending_calls"]`
against a table that no longer exists.

Revision ID: 0047
Revises: 0046
Create Date: 2026-08-22
"""

import sqlalchemy as sa
from alembic import op

revision = "202608221703_drop_mcp_pending_calls"
down_revision = "202608220859_derived_data_colocation"
branch_labels = None
depends_on = None

_TABLE = "mcp_pending_calls"


def upgrade() -> None:
    op.drop_index(f"ix_{_TABLE}_seq", table_name=_TABLE)
    op.drop_table(_TABLE)


def downgrade() -> None:
    """Structural only — recreates the empty table/index. The rows it held were single-use
    runtime queue entries (see the module docstring); there is nothing to restore into it."""
    op.create_table(
        _TABLE,
        sa.Column("seq", sa.Integer, nullable=False),
        sa.Column("id", sa.Text, primary_key=True),
        sa.Column("payload", sa.JSON, nullable=False),
    )
    op.create_index(f"ix_{_TABLE}_seq", _TABLE, ["seq"], unique=True)
