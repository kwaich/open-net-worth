"""Rename the Automation's ``allowed`` permission list to ``always_allowed``.

The field never governed reachability. Its ONLY consumer is
``automation_runner_adapter._canonical_always_allowed_tools``, which folds it into
``AutomationApprovalContext.always_allowed_tools``; the gate then returns ``"allow"`` for a
tool it names (``ai/tools/_confirm_gate.py``). An Automation with ``allowed: []`` can still
call every tool in the app — it is simply asked first. So the old name read as a capability
allowlist and actually meant "these tools skip the card", and the dangerous direction is the
one that looks safe: ``allowed: []`` reads as a lockdown and is not one.

The UI has labelled this control "Always allowed" since it was built
(``AutomationDetail.tsx``), and the in-memory field was renamed
``allowed_tools`` -> ``always_allowed_tools`` on 2026-08-27 for the same reason. This is the
persisted half of that rename.

``automations`` is a ``book_payload_table``, so the list lives inside the JSON ``payload``
column: a payload rewrite, not a schema change. A payload that cannot be parsed is skipped
rather than failing the boot (0059/0060's shape).

The key is renamed even when the list is EMPTY. Leaving an empty ``allowed`` behind would
strand a key nothing reads, and ``AutomationStore._canonical`` writes ``always_allowed`` on
the row's next save regardless — so a skipped row would silently carry both spellings.

``downgrade()`` is exact and lossless: the same rename in reverse.

Revision ID: 0061
Revises: 0060
Create Date: 2026-08-27
"""

from __future__ import annotations

import json
import logging

import sqlalchemy as sa
from alembic import op

log = logging.getLogger("alembic.rename_automation_allowed")

revision = "202608271618_rename_automation_allowed_to_always_allowed"
down_revision = "202608260009_listify_profile_identity_fields"
branch_labels = None
depends_on = None

_OLD = "allowed"
_NEW = "always_allowed"

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
