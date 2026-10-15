"""connector_tool_permissions: the per-(connector, tool) permission ladder — allow / ask
/ block — for EVERY connector lane (Channels, Data sources, Web, MCP). Connectors
redesign §4; design handoff §10 ("the storage shape ... is the thing to get right").

Schema PLUS one in-database backfill. Not a JSON cutover: there is no source file, so
nothing is stamped into ``json_cutovers`` and nothing gets renamed — the only prior
storage for any part of this decision is the MCP-only ``mcp_clients.payload
-> tool_permissions`` map, which lives in a table already (revision 0026).

## The backfill: an MCP ``deny`` becomes a ``block`` row, and NOTHING else moves

``McpServerConfig.tool_permissions`` was a two-value, MCP-only, opt-in-DENY map: a tool
absent from it defaulted to ``allow``, and a ``trusted`` server auto-fired everything not
denied. The redesign replaces that with three levels for every connector and a uniform
``ask`` default. Carrying the data across therefore splits cleanly in two:

  * a stored ``"deny"`` IS an explicit user decision — it becomes ``level="block"`` under
    connector id ``mcp:<server_id>`` (``services.connectors``' own namespacing), so a tool
    the user refused stays refused across the change;
  * EVERYTHING ELSE — an absent entry, and a stored ``"allow"`` — is deliberately NOT
    backfilled. Absent now means ``ask``, and so does an unmigrated ``allow``.

**Why a legacy ``"allow"`` does NOT become the new ``allow``.** The two words do not mean
the same thing, and reading them as if they did converts "Needs approval" into unattended
execution. Under the removed model (``git show 8d4afa9d^:backend/mcp/client_manager.py``)
``tool_permissions[t] == "allow"`` never auto-fired anything by itself: ``_needs_confirm``
ALSO required ``server.trusted`` AND a positively-read-only tool AND not-money-movement.
``"allow"`` only ever meant "not filtered out of the offer" — the tool still raised a
confirm card. The new ``allow`` means "fire directly, no card". So a converted row is a
permission the user never granted.

That is not hypothetical on this branch. The intermediate build's UI
(``frontend/src/components/tabs/connectors/permissions.ts``, landed in ``c8929da6``) wrote
``'allow'`` for BOTH "Allow" and "Needs approval" — the backend of the day could express
only two of the three levels. Anyone who exercised it and chose *Needs approval* has a
stored ``"allow"``, on tools including ``place_order``. Not migrating it costs nothing (it
lands at the ``ask`` default, which is what the user picked) and is the only reading that
cannot invent consent.

A deliberate ``allow`` set through the NEW control is untouched by any of this — it is
written directly to ``connector_tool_permissions`` and means exactly what it says,
everywhere, unattended included (``CLAUDE.md``, Security-critical areas). What this
migration refuses to do is manufacture one out of a value that meant something else.

The old field is left in place on ``mcp_clients`` — retiring it belongs to the dispatch
lane that rewires ``mcp/client_manager.py`` onto ``services.tool_permissions`` (ADR
``2026-08-13-mcp-permission-config-is-the-single-authority``); this revision only makes
sure the decisions survive that rewiring. Re-running the backfill is safe: it INSERTs only
pairs that have no row yet.

Revision ID: 0039
Revises: 0038
Create Date: 2026-08-13
"""

import json
from datetime import datetime, timezone

import sqlalchemy as sa
from alembic import op

revision = "202608132057_connector_tool_permissions"
down_revision = "202608110418_wave5f_dead_file_cleanup"
branch_labels = None
depends_on = None

_TABLE = "connector_tool_permissions"

# The old MCP-only vocabulary -> the new ladder. ONE entry, on purpose: a refusal is the
# only legacy value whose meaning survives the change intact. "allow" is deliberately
# ABSENT (see the module docstring — it did not mean auto-fire, and the intermediate UI
# wrote it for "Needs approval"), and any other value is ignored rather than guessed at.
# Nothing may be invented on the permissive side.
_LEVEL_FROM_MCP = {"deny": "block"}

_mcp_clients = sa.table("mcp_clients", sa.column("id", sa.Text), sa.column("payload", sa.JSON))

# Typed lightweight handle on the table this revision just created — the insert below goes
# through SQLAlchemy's own JSON bind processing rather than a hand-serialized string, so it
# is correct on SQLite (TEXT) and Postgres (json) alike.
_perms = sa.table(
    _TABLE,
    sa.column("seq", sa.Integer),
    sa.column("id", sa.Text),
    sa.column("payload", sa.JSON),
)


def _payload(raw) -> dict:
    """``payload`` comes back a dict through SQLAlchemy's JSON type on both dialects, but
    a str is tolerated so this cannot depend on how a given driver decodes the column."""
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError:
            return {}
    return raw if isinstance(raw, dict) else {}


def _backfill_mcp_denies(conn) -> None:
    now = datetime.now(timezone.utc).isoformat()
    rows = []
    for server in conn.execute(sa.select(_mcp_clients.c.id, _mcp_clients.c.payload)):
        payload = _payload(server.payload)
        server_id = payload.get("id") or server.id
        perms = payload.get("tool_permissions")
        if not server_id or not isinstance(perms, dict):
            continue
        connector_id = f"mcp:{server_id}"
        for tool, value in perms.items():
            # Every read below is defensive by design: this runs inside the fail-closed boot
            # migration, so a single malformed legacy row must be SKIPPED, never allowed to
            # abort the upgrade (which would refuse the whole boot and restore a backup).
            level = _LEVEL_FROM_MCP.get(value) if isinstance(value, str) else None
            if not level or not isinstance(tool, str) or not tool:
                continue
            key = f"{connector_id}:{tool}"
            rows.append(
                {
                    "id": key,
                    "payload": {
                        "id": key,
                        "connector_id": connector_id,
                        "tool": tool,
                        "level": level,
                        "updated_at": now,
                    },
                }
            )
    if not rows:
        return
    # Deterministic seq, and a re-run inserts only what is missing (the table is empty at
    # this revision, but the guard keeps the backfill idempotent if it is ever re-applied).
    # De-duplicating by key on the way in matters for the same reason: two legacy rows that
    # somehow resolve to one pair must collapse, not raise a PK violation mid-migration.
    existing = {r[0] for r in conn.execute(sa.select(_perms.c.id))}
    seen: set[str] = set()
    fresh = []
    for row in rows:
        if row["id"] in existing or row["id"] in seen:
            continue
        seen.add(row["id"])
        fresh.append(row)
    if not fresh:
        return
    conn.execute(
        sa.insert(_perms),
        [{"seq": seq, "id": r["id"], "payload": r["payload"]} for seq, r in enumerate(fresh)],
    )


def upgrade() -> None:
    op.create_table(
        _TABLE,
        sa.Column("seq", sa.Integer, nullable=False),
        sa.Column("id", sa.Text, primary_key=True),
        sa.Column("payload", sa.JSON, nullable=False),
    )
    op.create_index(f"ix_{_TABLE}_seq", _TABLE, ["seq"], unique=True)
    _backfill_mcp_denies(op.get_bind())


def downgrade() -> None:
    op.drop_table(_TABLE)
