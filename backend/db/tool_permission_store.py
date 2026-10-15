"""ToolPermissionStore — THE permission table: one level per tool, for every tool in
the app (Connectors redesign §4, design handoff §10; approval model RFC 2026-07-20 §16).

One row per tool the user has actually decided about:

    {"id": "plaid:plaid_sync_now", "connector_id": "plaid", "tool": "plaid_sync_now",
     "level": "always_allow", "updated_at": iso}

## One store, because two answers to one question is the bug

Until 2026-08-14 there were TWO: this table (three levels, per connector, written by the
Connectors ladder, consulted ONLY by external-MCP dispatch) and `tool_grants` (binary
grant / no grant, per tool name, written by the confirm card + Settings › Permissions,
consulted for everything else). Both answered "may this tool run"; they could disagree,
and nothing told the user which had won. `tool_grants` is now ABSORBED here and deleted
(revision 0040) — a stored `scope: "always"` grant became a `level: "always_allow"` row,
and an absent grant was left absent because absence is not a decision. See ADR
`2026-08-14-one-permission-store-for-every-tool`.

So `connector_id` is the tool's OWNER, not necessarily a connector:

  * a tool carrying `@tool(connector="<id>")` -> that id (`plaid`, `web_search:tavily`);
  * an external-MCP tool -> `mcp:<server_id>` (`services.connectors`' own namespacing);
  * everything else -> the reserved bucket `services.tool_permissions.CORE_CONNECTOR_ID`
    (`"core"`) — `add_transactions`, the ~160 unscoped write tools (`email_send_message` is
    NOT one of these — it is `connector="email"`-owned, see `services.tool_permissions`).

The composite key is what keeps a decision from leaking sideways. Two MCP servers may
each advertise a tool called `search`; `plaid_sync_now` and `ibkr_sync_now` are distinct
rows under distinct owners. `tool_grants`' tool-name-only key could express neither —
which is why THIS table survived the merge and that one did not (and why
`ai/tools/connectors.py` split the shared `connector_sync` name apart in the first
place: one name meant one grant covering every provider).

## The shape, and why it is the whole design

Section 10 of the handoff names the storage shape as the thing to get right: *per
connector, per tool, with the group aggregate DERIVED and never written*. This table
holds exactly one level per (owner, tool) pair and has nowhere to put a group
level — "Custom" is computed where it is displayed (a group whose tools disagree) and
can therefore never drift from the tools underneath it, because there is no second
record to drift.

A group-level pick is `set_levels()`: a deliberately LOSSY stamp of one level onto
every named tool, in ONE transaction. Lossy is the specified behaviour ("this is the
'make this whole group behave' control"), and one transaction is what keeps it honest —
a half-applied stamp would leave the group non-uniform, i.e. reading *Custom*, which is
precisely the state the user just asked to leave.

## Absent means default

A tool with no row is UNDECIDED. For a connector-owned tool that reads
`services.tool_permissions.DEFAULT_LEVEL` (`"needs_approval"`) uniformly, read or write
alike; for a `core`-owned tool the gate applies the approval model's own answer instead
(`ai.tools._confirm_gate.effective_level` — a write asks, a read runs), which is what
keeps every untagged tool behaving exactly as it did before this table existed. A fresh
install stores NOTHING either way: there is no seeding pass, no per-connector row set
minted on connect, and no tiering by tool type inside a connector.

`always_allow` is a decision; `blocked` is a decision; `needs_approval` written
explicitly is also a decision (round-trip fidelity: what you set is what
`get_connector_tools` reports) — but the *absence* of a row is what every
unconfigured tool starts from. Deleting a row (`clear_level`/`clear_connector`) is
therefore a real operation: it returns the tool to its default rather than pinning it
at today's value.

## Ids

`id` is the deterministic surrogate `f"{connector_id}:{tool}"` — `budget_assignments`'
precedent (`f"{book_id}:{month}:{category}"`), for the same reason: a keyed relation's
pk composed of the things that identify it makes "set this pair's level" a plain upsert
and makes two rows for one pair structurally impossible. Both components are kept in
the payload too, so nothing has to parse the key; and a tool name is a Python
identifier while a connector id never contains "/", so the pair is recoverable from the
id alone via one `rsplit(":", 1)` if it ever needs to be. The reserved YOLO row
(`services.tool_permissions.YOLO_TOOL`, `"core:*"`) rides the same shape — a real tool
can never collide with it, because tool names are identifiers.

## The two flags every store must answer

**Checkpoint-EXCLUDED** (`ai.checkpoint._policy._EXCLUDED_TABLES`) — the absorbed
`tool_grants`' reasoning, load-bearing rather than tidy: `restore_checkpoint` is a
MODEL-CALLABLE tool carrying `mutates=("*",)`, so a checkpoint-INCLUDED permission table
would mean *user blocks a tool -> the model restores any earlier checkpoint -> the block
is gone*. That is privilege escalation through an unrelated tool, no malice required.
A checkpoint undoes what Lucius did to your DATA; a permission is not something Lucius
did, it is something the owner decided. Accepted cost, stated plainly: a level SURVIVES
a `/rewind`, so undoing a rash session leaves the permission changed in it standing.

**NON_RESTORABLE** (`core.sensitive.NON_RESTORABLE_TABLES`) — the same argument against
a disaster-recovery restore: revoke `always_allow` -> restore a backup taken before the
revocation -> the tool is auto-firing again. `services.backup` captures this table's
live rows across the `onw.db` swap instead.

**NOT portable, NOT secret.** Absent from `core.portable.PORTABLE_FILES` and from
`db.portable_bridge.BRIDGED`: "I trust THIS install to let Lucius place orders
unattended" is instance-local trust and must not travel to another machine inside a
backup. It holds no credential, so it is not a `SECRET_TABLES` member and needs no
chmod-600-equivalent; security-relevant but not confidential. `/api/reset` DOES wipe it
(`portable_bridge._wipe_tool_permissions`) — "reset means reset", and the direction of
that wipe is back to the cautious default.

All three classifications are inherited UNCHANGED from both merged predecessors: they
were identical (that is stated verbatim in `tool_grants`' own retired docstring), which
is one more sign these were always one store wearing two names.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from backend.db.payload_store import PayloadTableStore
from backend.db.tables import tool_permissions

logger = logging.getLogger(__name__)

# The three stored levels. There is deliberately no fourth: "Custom" is a DERIVED group
# label (a group whose tools disagree), computed for display and never written — see the
# module docstring. Defined here, beside the table, exactly as `tool_grant_store`'s
# VALID_SCOPES is; `services.tool_permissions` re-exports it as the caller-facing name.
LEVELS: tuple[str, ...] = ("always_allow", "needs_approval", "blocked")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def permission_key(connector_id: str, tool: str) -> str:
    """This pair's row id — see the module docstring's Ids section."""
    return f"{connector_id}:{tool}"


class ToolPermissionStore(PayloadTableStore):
    """Flat list of permission rows, one per (connector, tool) pair."""

    def __init__(self, engine=None):
        super().__init__(tool_permissions, engine=engine)

    # ── Reads ─────────────────────────────────────────────────────────────────

    def get_level(self, connector_id: str, tool: str) -> Optional[str]:
        """This pair's STORED level, or ``None`` when the user has never decided about it.

        ``None`` is not "denied" and not "allowed" — it is *unset*, and the caller
        (`services.tool_permissions.get_tool_permission`) is what turns it into
        ``DEFAULT_LEVEL``. Keeping the store honest about "no row" rather than
        manufacturing a default here is what lets `clear_connector` mean something.
        """
        row = self.find_by_id(permission_key(connector_id, tool))
        return row.get("level") if row else None

    def for_connector(self, connector_id: str) -> list[dict]:
        """Every stored row for one connector, in insertion order.

        A Python-side equality filter over `all()` (`PayloadTableStore.query`'s
        documented shape — no JSON-path SQL). This table is bounded by
        connectors x tools and only ever holds rows for pairs a human decided about,
        so it stays small by construction; the hot path (one tool's level, on
        dispatch) is a primary-key lookup, not this scan.
        """
        return self.query(connector_id=connector_id)

    # ── Writes ────────────────────────────────────────────────────────────────

    def set_level(self, connector_id: str, tool: str, level: str) -> dict:
        """Record ONE tool's level. Re-setting the same pair REPLACES its row."""
        with self._use_conn(None) as conn:
            return self._stamp(conn, connector_id, tool, level)

    def set_levels(self, connector_id: str, tools: list[str], level: str) -> list[dict]:
        """Stamp ``level`` onto every named tool in ONE transaction — the group control.

        Deliberately lossy: an existing per-tool override is overwritten, not merged.
        Atomic because a half-applied stamp is a non-uniform group, i.e. the *Custom*
        state the user just acted to leave.

        Duplicate names in ``tools`` collapse onto one row (same key, upserted twice) —
        harmless, and cheaper than making every caller de-duplicate a list it assembled
        from a UI group.
        """
        if not tools:
            return []
        with self._use_conn(None) as conn:
            return [self._stamp(conn, connector_id, tool, level) for tool in tools]

    def clear_level(self, connector_id: str, tool: str) -> bool:
        """Forget ONE pair's decision; True when a row was actually removed.

        The single-pair sibling of `clear_connector`, and the absorbed `tool_grants`'
        `revoke()` in its new spelling: Settings › Permissions calls it to take a decision
        back, and the tool returns to its DEFAULT rather than to a stored
        `needs_approval` — same distinction `clear_connector` draws, and the reason a
        revoke is a DELETE here rather than a downgrade.
        """
        removed = self.delete(permission_key(connector_id, tool))
        if removed:
            logger.info("tool permission cleared: %s/%s", connector_id, tool)
        return removed

    def clear_connector(self, connector_id: str) -> int:
        """Drop every stored row for one connector; returns how many were removed.

        Returns its tools to the DEFAULT (unset), which is NOT the same as stamping them
        all to ``needs_approval``: a future default change would move them, and nothing is left
        claiming the user made a decision they didn't. The intended caller is a connector
        being removed — a re-added connector of the same id must not silently inherit the
        levels of the one it replaced.

        Reads INSIDE its own transaction (``all(conn=...)``) rather than through
        ``for_connector``'s lock-free read: a row written for this connector between a
        read-then-delete pair would otherwise survive the clear and be the one thing left
        claiming a decision the user just erased.
        """
        with self._use_conn(None) as conn:
            rows = [r for r in self.all(conn=conn) if r.get("connector_id") == connector_id]
            for row in rows:
                self.delete(row["id"], conn=conn)
        if rows:
            logger.info("connector permissions cleared: %s (%d rows)", connector_id, len(rows))
        return len(rows)

    def clear_connector_level(self, connector_id: str, level: str) -> int:
        """Drop every stored row for one connector that sits AT ``level``; returns the count.

        `clear_connector`'s selective sibling, for the one caller that must withdraw grants
        without touching refusals (`services.tool_permissions.revoke_allowed_tools` — an MCP
        server whose endpoint was edited). Same in-transaction read-then-delete as
        `clear_connector`, for the same reason: a row written between a lock-free read and
        the delete would survive as the one decision the caller just erased.
        """
        with self._use_conn(None) as conn:
            rows = [
                r
                for r in self.all(conn=conn)
                if r.get("connector_id") == connector_id and r.get("level") == level
            ]
            for row in rows:
                self.delete(row["id"], conn=conn)
        if rows:
            logger.info(
                "connector permissions cleared at level %s: %s (%d rows)",
                level,
                connector_id,
                len(rows),
            )
        return len(rows)

    # ── Internals ─────────────────────────────────────────────────────────────

    def _stamp(self, conn, connector_id: str, tool: str, level: str) -> dict:
        """Upsert one pair's row THROUGH the caller's open transaction.

        Validates the level here as well as in `services.tool_permissions` — belt over
        the service's own check (`ToolGrantStore.grant`'s identical precedent for an
        unknown scope), because this is the only write path into the table and an
        unrecognised level stored here would be read back as a level nothing knows how
        to enforce.
        """
        if level not in LEVELS:
            raise ValueError(f"unknown permission level {level!r} — expected one of {LEVELS}")
        row = {
            "id": permission_key(connector_id, tool),
            "connector_id": connector_id,
            "tool": tool,
            "level": level,
            "updated_at": _now(),
        }
        self.upsert(row, conn=conn)
        return row
