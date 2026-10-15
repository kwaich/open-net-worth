"""Rename the Automation payload key ``approval`` to ``permission_mode``.

The field is a permission MODE, not an approval record: ``manual`` asks on every
approval-tier call, ``auto`` proceeds except on hardline operations whose target set cannot
be verified after execution, ``skip`` proceeds regardless. ``approval`` named the thing being
decided rather than the setting, which reads as a per-run outcome ("was this approved?")
rather than a standing policy.

It is the blanket half of the Automation's permission decision; ``always_allowed`` (revision
0061) is the per-tool exception list carved out of it by real approval taps. Naming one
``approval`` and the other ``always_allowed`` obscured that they are two halves of one thing.

Same payload-rewrite shape as 0061: ``automations`` is a ``book_payload_table``, so the value
lives inside the JSON ``payload`` column. A payload that cannot be parsed is skipped rather
than failing the boot.

The key is renamed even when it holds the default ``manual``: leaving it behind would strand a
key nothing reads, and ``AutomationStore._canonical`` writes ``permission_mode`` on the row's
next save regardless — so a skipped row would silently carry both spellings.

``downgrade()`` is exact and lossless: the same rename in reverse.

Revision ID: 0062
Revises: 0061
Create Date: 2026-08-27
"""

from __future__ import annotations

import json
import logging

import sqlalchemy as sa
from alembic import op

log = logging.getLogger("alembic.rename_automation_approval")

revision = "202608271619_rename_automation_approval_to_permission_mode"
down_revision = "202608271618_rename_automation_allowed_to_always_allowed"
branch_labels = None
depends_on = None

_OLD = "approval"
_NEW = "permission_mode"

_SELECT = sa.text("SELECT id, payload FROM automations")
_UPDATE = sa.text("UPDATE automations SET payload = :payload WHERE id = :id")


def _migrate(conn, *, src: str, dst: str) -> None:
    touched = 0
    for row_id, raw in conn.execute(_SELECT).fetchall():
        try:
            payload = json.loads(raw) if isinstance(raw, str) else raw
        except Exception:  # noqa: BLE001 — one unreadable row must not stop the rest
            log.warning("automation payload %s is unreadable; skipped", row_id)
            continue
        if not isinstance(payload, dict) or src not in payload:
            continue
        payload[dst] = payload.pop(src)
        conn.execute(_UPDATE, {"payload": json.dumps(payload), "id": row_id})
        touched += 1
    if touched:
        log.info("renamed %s -> %s on %d automation(s)", src, dst, touched)


def upgrade() -> None:
    _migrate(op.get_bind(), src=_OLD, dst=_NEW)


def downgrade() -> None:
    _migrate(op.get_bind(), src=_NEW, dst=_OLD)
