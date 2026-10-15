"""Remove self-harness: drop harness_suggestions, clear the harness_state watermark,
delete the seeded "Self-harness routine scan" Automation.

Owner decision 2026-08-28: the feature never worked as designed. A 2026-08-02
automations-unification migration (``migrate_automation_execution_to_lucius.py``)
converted the seeded automation's ``execution.kind`` from ``harness_scan`` to plain
``lucius`` — since then the detection pipeline this table/watermark/tool/API backed
(``services/self_harness.py``, ``HarnessSuggestionStore``, the Inbox
``harness_suggestion`` cards, ``get_harness_suggestions``) has been unreachable dead
code; nothing has written a row to ``harness_suggestions`` or read ``harness_state``
since. The code itself is removed in the same change (see docs/launch/BOARD.md) — this
revision is its data/schema half. The feature may be rebuilt later; the RFC
(docs/rfc/lucius-self-harness.md) and this migration's own history stay as reference.

Three independent cleanups, one revision because they are one decision:

1. **DELETE the seeded automation row** — matched by
   ``payload->>'provenance'->>'system_key' == 'harness_scan'`` (the seeding
   idempotence key, ``services.automations.service.seed_harness_scan``'s own
   ``system_key=`` argument) rather than by name, since a name is user-editable. This
   is the actual thing that ran weekly on a real instance.
2. **DELETE the ``harness_state`` namespace row from ``runtime_state``** — the shared
   table also holds ``app_config``/``dedup_config``/three cache-state rows, so only the
   one row is removed, not the table.
3. **DROP the ``harness_suggestions`` table** (created by revision 0013, absorbed-from
   by revision 0014 until this revision — see that file's own post-hoc edit note).

downgrade() recreates the empty ``harness_suggestions`` table (structural only, same
precedent as revision 0040's ``tool_grants`` downgrade) — the deleted automation row and
harness_state watermark are genuine data loss with nothing to reconstruct, so those two
downgrade steps are documented no-ops (same precedent as revision 0043).

Revision ID: 0063
Revises: 0062
Create Date: 2026-08-28
"""

from __future__ import annotations

import json
import logging

import sqlalchemy as sa
from alembic import op

log = logging.getLogger("alembic.remove_self_harness")

revision = "202608281125_remove_self_harness"
down_revision = "202608271619_rename_automation_approval_to_permission_mode"
branch_labels = None
depends_on = None

_TABLE = "harness_suggestions"

_SELECT_AUTOMATIONS = sa.text("SELECT id, payload FROM automations")
_DELETE_AUTOMATION = sa.text("DELETE FROM automations WHERE id = :id")
_DELETE_HARNESS_STATE = sa.text("DELETE FROM runtime_state WHERE namespace = 'harness_state'")


def _delete_seeded_automation(conn) -> None:
    removed = 0
    for row_id, raw in conn.execute(_SELECT_AUTOMATIONS).fetchall():
        try:
            payload = json.loads(raw) if isinstance(raw, str) else raw
        except Exception:  # noqa: BLE001 — one unreadable row must not stop the rest
            log.warning("automation payload %s is unreadable; skipped", row_id)
            continue
        if not isinstance(payload, dict):
            continue
        system_key = (payload.get("provenance") or {}).get("system_key")
        if system_key == "harness_scan":
            conn.execute(_DELETE_AUTOMATION, {"id": row_id})
            removed += 1
    if removed:
        log.info("deleted %d seeded self-harness automation row(s)", removed)


def upgrade() -> None:
    conn = op.get_bind()
    _delete_seeded_automation(conn)
    conn.execute(_DELETE_HARNESS_STATE)
    op.drop_index(f"ix_{_TABLE}_seq", table_name=_TABLE)
    op.drop_table(_TABLE)


def downgrade() -> None:
    """Structural only. The deleted automation row and the harness_state watermark are
    genuine data loss with nothing to reconstruct from — same documented-no-op shape as
    revision 0043's tool_permissions cleanup. Only the empty table structure comes back."""
    op.create_table(
        _TABLE,
        sa.Column("seq", sa.Integer, nullable=False),
        sa.Column("id", sa.Text, primary_key=True),
        sa.Column("payload", sa.JSON, nullable=False),
    )
    op.create_index(f"ix_{_TABLE}_seq", _TABLE, ["seq"], unique=True)
