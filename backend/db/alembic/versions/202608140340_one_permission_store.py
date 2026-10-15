"""ONE permission store: `connector_tool_permissions` -> `tool_permissions`, absorbing
`tool_grants`, with the three levels renamed to say what they mean.

Three changes, ONE revision, because they are one change: the app had TWO stores that both
answered "may this tool run", and it now has one.

  * `tool_grants` (binary grant / no grant, keyed by tool NAME) — written by the confirm
    card's "Always allow" and Settings > Permissions, consulted at dispatch for every
    native tool.
  * `connector_tool_permissions` (three levels, keyed by (connector, tool)) — written by
    the Connectors tab's ladder, consulted ONLY by external-MCP dispatch.

They could disagree, and nothing told the user which had won. ADR
`2026-08-14-one-permission-store-for-every-tool` records the merge; this revision is its
data half.

## 1. The table is renamed

`connector_tool_permissions` -> `tool_permissions`. The `connector_` prefix stops being
true the moment unscoped tools live here: `connector_id` becomes the tool's OWNER — its
`@tool(connector=...)` id, `mcp:<server_id>`, or the reserved bucket `core`.

## 2. The levels are renamed to their UI labels

`allow` -> `always_allow`, `ask` -> `needs_approval`, `block` -> `blocked`. Bare `allow`
was ambiguous about whether it meant "permitted once" or "permitted always"; the stored
value now reads the same as the control that sets it.

**Every existing row is rewritten here.** Revision 0039 shipped, so real instances hold
`block` rows (its MCP backfill) and `allow`/`ask` rows (anything set through the ladder
since). An unrewritten row would not match any known level, and `stored_tool_level` fails
CLOSED on an unrecognised value — so a `block` the owner set would silently degrade to the
default and a `always_allow` would silently stop firing. That is a permission quietly
discarded, which is the one outcome a permission migration must never produce.

## 3. `tool_grants` is absorbed, then dropped

  * `scope: "always"` — a real, explicit "run this without asking me" -> a row at
    `always_allow`, under the tool's owner from `_TOOL_OWNERS` below.
  * the reserved `*` YOLO row -> `(core, "*", always_allow)`, same meaning, now listed and
    revoked through the same surface as every other decision.
  * **ANYTHING ELSE IS NOT MIGRATED.** A legacy `session` row (the tier removed on
    2026-08-02) already failed closed in `ToolGrantStore._match` and is not a standing
    grant; `once` was never persisted; an absent grant is an ABSENCE, not a decision, and
    absence already reads as `needs_approval`.

**Nothing is backfilled on the permissive side.** This is the mistake revision 0039 caught
in review and refused to make (`_LEVEL_FROM_MCP` carries exactly one entry, `deny ->
block`, because a legacy MCP `"allow"` had never meant auto-fire), and it is refused again
here for the same reason: a level nobody set is consent nobody gave. The only rows written
are the ones a human explicitly created.

## Why the owner map is a literal snapshot

Placing an absorbed grant needs each tool's `connector=` tag, which lives in the `ai/`
registry. A migration must not import live application code — the tags can change, and a
migration that reads them would produce a different result on every replay. `_TOOL_OWNERS`
is therefore this revision's frozen snapshot of the 20 tagged tools, generated from the
registry at authoring time. A name absent from it lands in `core`, which is exactly right:
an untagged tool IS core-owned, and so is a stale grant for a tool that no longer exists.

Getting this right is not cosmetic. `ai/tools/connectors.py` records the incident behind
the per-provider split: a single shared `connector_sync` name meant one name-keyed grant,
so "always allow Plaid syncs" would silently have allowed IBKR too. Placing each absorbed
grant under its own owner is what carries that property across the merge.

Re-running is safe: the absorb INSERTs only pairs that have no row yet, and the level
rewrite is idempotent (the new names are not themselves old names).

Revision ID: 0040
Revises: 0039
Create Date: 2026-08-14
"""

import json
from datetime import datetime, timezone

import sqlalchemy as sa
from alembic import op

revision = "202608140340_one_permission_store"
down_revision = "202608132057_connector_tool_permissions"
branch_labels = None
depends_on = None

_OLD_TABLE = "connector_tool_permissions"
_TABLE = "tool_permissions"
_GRANTS = "tool_grants"

# The reserved owner bucket for a tool that belongs to no connector, and the reserved
# tool name for the blanket "stop asking me" row. Duplicated as literals rather than
# imported from `services.tool_permissions` for the reason in the docstring: a migration
# is frozen, and must not move when the application does.
_CORE = "core"
_YOLO = "*"

# old level -> new level. Every value revision 0039 could have written is covered; a value
# outside this map is left untouched (it was already unrecognised, and rewriting it would
# be inventing a decision).
_LEVEL_RENAMES = {"allow": "always_allow", "ask": "needs_approval", "block": "blocked"}

# The `connector=` tag of every tagged tool, snapshotted at revision time — see the
# docstring. Absent -> `_CORE`.
_TOOL_OWNERS = {
    "fetch_page_via_duckduckgo": "web_search:duckduckgo",
    "fetch_page_via_google_pse": "web_search:google_pse",
    "fetch_page_via_perplexity": "web_search:perplexity",
    "fetch_page_via_serper": "web_search:serper",
    "fetch_page_via_tavily": "web_search:tavily",
    "get_alpaca_status": "alpaca",
    "get_enable_banking_status": "enable_banking",
    "get_ibkr_status": "ibkr",
    "get_plaid_status": "plaid",
    "get_yodlee_status": "yodlee",
    "search_via_duckduckgo": "web_search:duckduckgo",
    "search_via_google_pse": "web_search:google_pse",
    "search_via_perplexity": "web_search:perplexity",
    "search_via_serper": "web_search:serper",
    "search_via_tavily": "web_search:tavily",
    "sync_alpaca_now": "alpaca",
    "sync_enable_banking_now": "enable_banking",
    "sync_ibkr_now": "ibkr",
    "sync_plaid_now": "plaid",
    "sync_yodlee_now": "yodlee",
}

_perms = sa.table(
    _TABLE,
    sa.column("seq", sa.Integer),
    sa.column("id", sa.Text),
    sa.column("payload", sa.JSON),
)
_grants = sa.table(_GRANTS, sa.column("id", sa.Text), sa.column("payload", sa.JSON))


def _payload(raw) -> dict:
    """``payload`` comes back a dict through SQLAlchemy's JSON type on both dialects, but
    a str is tolerated so this cannot depend on how a given driver decodes the column."""
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError:
            return {}
    return raw if isinstance(raw, dict) else {}


def _rename_levels(conn) -> None:
    """Rewrite every stored level onto its new name, in place.

    Row-by-row rather than one UPDATE ... SET json_set(...): the payload column is plain
    JSON on SQLite and json on Postgres, and the JSON-manipulation SQL differs between
    them. This table holds one row per decision a human actually made — tens, not
    thousands — so a Python round-trip costs nothing and works identically on both.
    """
    for row in conn.execute(sa.select(_perms.c.id, _perms.c.payload)).fetchall():
        payload = _payload(row.payload)
        new_level = _LEVEL_RENAMES.get(payload.get("level"))
        if not new_level:
            continue
        conn.execute(
            sa.update(_perms)
            .where(_perms.c.id == row.id)
            .values(payload={**payload, "level": new_level})
        )


def _absorb_grants(conn) -> None:
    now = datetime.now(timezone.utc).isoformat()
    existing = {r[0] for r in conn.execute(sa.select(_perms.c.id))}
    next_seq = conn.execute(sa.select(sa.func.coalesce(sa.func.max(_perms.c.seq), -1))).scalar()
    next_seq = int(next_seq) + 1

    fresh = []
    seen: set[str] = set()
    for row in conn.execute(sa.select(_grants.c.id, _grants.c.payload)):
        payload = _payload(row.payload)
        # Every read below is defensive by design: this runs inside the fail-closed boot
        # migration, so a single malformed legacy row must be SKIPPED, never allowed to
        # abort the upgrade (which would refuse the whole boot and restore a backup).
        tool = payload.get("tool") or row.id
        if not isinstance(tool, str) or not tool:
            continue
        # ONLY a durable grant crosses. See the docstring: `session` (retired tier, already
        # failing closed), `once` (never persisted) and anything unrecognised are not
        # standing permissions and must not become one.
        if payload.get("scope") != "always":
            continue
        owner = _CORE if tool == _YOLO else _TOOL_OWNERS.get(tool, _CORE)
        key = f"{owner}:{tool}"
        if key in existing or key in seen:
            continue
        seen.add(key)
        fresh.append(
            {
                "seq": next_seq + len(fresh),
                "id": key,
                "payload": {
                    "id": key,
                    "connector_id": owner,
                    "tool": tool,
                    "level": "always_allow",
                    "updated_at": payload.get("granted_at") or now,
                },
            }
        )
    if fresh:
        conn.execute(sa.insert(_perms), fresh)


def upgrade() -> None:
    conn = op.get_bind()
    op.drop_index(f"ix_{_OLD_TABLE}_seq", table_name=_OLD_TABLE)
    op.rename_table(_OLD_TABLE, _TABLE)
    op.create_index(f"ix_{_TABLE}_seq", _TABLE, ["seq"], unique=True)
    _rename_levels(conn)
    _absorb_grants(conn)
    op.drop_index(f"ix_{_GRANTS}_seq", table_name=_GRANTS)
    op.drop_table(_GRANTS)


def downgrade() -> None:
    """Structural only — the absorbed grants are NOT split back out.

    Reversing the merge would have to guess which `always_allow` rows were once grants and
    which the user set afterwards through the ladder, and guessing wrong writes a
    permission nobody granted. The table and its old level names come back; the rows stay
    where they are.
    """
    conn = op.get_bind()
    op.create_table(
        _GRANTS,
        sa.Column("seq", sa.Integer, nullable=False),
        sa.Column("id", sa.Text, primary_key=True),
        sa.Column("payload", sa.JSON, nullable=False),
    )
    op.create_index(f"ix_{_GRANTS}_seq", _GRANTS, ["seq"], unique=True)
    reverse = {new: old for old, new in _LEVEL_RENAMES.items()}
    for row in conn.execute(sa.select(_perms.c.id, _perms.c.payload)).fetchall():
        payload = _payload(row.payload)
        old_level = reverse.get(payload.get("level"))
        if not old_level:
            continue
        conn.execute(
            sa.update(_perms)
            .where(_perms.c.id == row.id)
            .values(payload={**payload, "level": old_level})
        )
    op.drop_index(f"ix_{_TABLE}_seq", table_name=_TABLE)
    op.rename_table(_TABLE, _OLD_TABLE)
    op.create_index(f"ix_{_OLD_TABLE}_seq", _OLD_TABLE, ["seq"], unique=True)
