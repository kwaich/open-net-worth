"""Drop stale `tool_permissions` rows for `send_email`/`search_messages`/`get_messages`
under BOTH the `core` and `email` owners — a follow-on data cleanup for the owner-tag fix
in `backend/ai/tools/email_ingest.py` (commit `81cbd7b8`, `docs/adr/
2026-08-17-read-shaped-exemption-survives-connector-tagging.md`).

## Why this is needed at all

Before that fix, `@tool(connector="email")` was missing from all three tools, so
`ai/tools/_connector_gate.py::permission_owner` resolved their owner as the reserved
`core` bucket while `PUT /api/connectors/email/permissions/{tool}` (Email's own permission
ladder in the connector detail UI) already wrote decisions keyed `connector_id="email"` —
taken straight from the URL, with no idea the dispatch-time read was looking at a
different owner entirely. Two rows could therefore exist for the SAME tool, and only one
of them was ever actually consulted:

  * an `email`-owned row — WRITTEN (an operator really clicked something in Connectors ->
    Email's ladder) but never READ at dispatch (silently ignored);
  * a `core`-owned row — READ at dispatch, but not specific to email at all: `core` is a
    shared bucket for every untagged tool, so a `(core, send_email, X)` row could have been
    set for a reason that has nothing to do with the Email connector, or — more likely,
    given YOLO is also a `(core, "*")` row — could reflect the blanket "stop asking me"
    setting rather than a decision about this tool specifically.

The owner-tag fix makes both of these rows REAL for the first time. Left in place, either
one silently changes what happens the moment this migration's own PR ships, with zero new
action from the operator:

  * an `email`-owned `always_allow` (a click that, at the time, visibly did nothing —
    every real call still showed a confirm card — so an operator might reasonably have
    forgotten it, or concluded the control was broken) now activates for real: `send_email`,
    an explicitly irreversible action by this app's own conventions, could fire card-free
    on an Automation/headless run;
  * a `core`-owned `blocked` degrades to `needs_approval`, or — worse — under YOLO
    (`(core, "*", always_allow)`) a `core`-owned row with no explicit decision at all
    degrades all the way to `always_allow`, a blocked-to-allow flip nobody asked for.

## Why deleting is safe (not a permission taken away)

Neither key was ever simultaneously WRITTEN-TO and ENFORCED before the owner-tag fix:

  * the `email` rows were written but ignored — nothing an operator actually EXPERIENCED as
    "this tool now always/never runs" is being taken away, because dispatch never looked at
    that row;
  * the `core` rows were enforced but never SPECIFIC to email — `core` is deliberately a
    shared bucket (`services.tool_permissions.CORE_CONNECTOR_ID`), so a stored decision
    there was never "the operator's decision about the Email connector" in the first place,
    even though it happened to also govern these three tool names until the tag fix moved
    them onto their own owner.

So this migration deletes exactly six possible keys — `core:send_email`,
`core:search_messages`, `core:get_messages`, `email:send_email`, `email:search_messages`,
`email:get_messages` — whichever of them actually exist, and leaves every other row (any
OTHER tool name under `core`, and the reserved `core:*` YOLO row) completely untouched.
Re-granting any of the three is one click in the UI post-migration — the ladder starts
cautious (`needs_approval`), exactly like a freshly discovered connector tool always does.

Forward-only: there is no meaningful "undo" for a data cleanup whose whole point is that
the deleted rows were never a real, consulted decision in the first place.

Revision ID: 0043
Revises: 0042
Create Date: 2026-08-17
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "202608172253_email_tool_permissions_owner_fix_cleanup"
down_revision = "202608161811_receipt_store"
branch_labels = None
depends_on = None

_TABLE = "tool_permissions"

# The exact (owner, tool) pairs that were never simultaneously written-to AND enforced
# before the `connector="email"` tag fix — see the module docstring.
_STALE_OWNERS = ("core", "email")
_STALE_TOOLS = ("send_email", "search_messages", "get_messages")

_perms = sa.table(_TABLE, sa.column("id", sa.Text))


def upgrade() -> None:
    conn = op.get_bind()
    stale_ids = [f"{owner}:{tool}" for owner in _STALE_OWNERS for tool in _STALE_TOOLS]
    conn.execute(sa.delete(_perms).where(_perms.c.id.in_(stale_ids)))


def downgrade() -> None:
    """No-op. The deleted rows were never a real, consulted operator decision (see the
    module docstring) — there is nothing to reconstruct, and re-inventing one on a
    downgrade would be minting a permission nobody granted, the exact mistake revision
    0040's own docstring already refuses to make on the permissive side."""
