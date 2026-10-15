"""Backfill ``completed`` onto Automations whose schedule already ran out.

Until this revision a schedule that ran out (a one-shot that ran, a ``max_runs`` cap reached)
was parked as ``paused`` — the same status as a user's deliberate pause, so the list could
not tell a finished reminder from something switched off on purpose. The runner now writes
``completed``; this rewrites the rows it would have written before.

Deliberately conservative — only rows provably finished move: ``paused`` with no
``next_run_at`` AND either a one-shot whose date has passed and that ran at least once, or a
run count at its ``max_runs`` cap. A recurring schedule the user paused, a one-shot paused
before its date, or one that never ran stays ``paused``. An rrule past its UNTIL is left
alone (proving it ended needs the recurrence engine) — the runner marks it on its next run.

Same payload-rewrite shape as ``202608271619_rename_automation_approval_to_permission_mode``:
the status lives in the JSON ``payload``; an unreadable payload or date is skipped rather than
failing the boot. ``downgrade()`` returns every ``completed`` row to ``paused`` — exactly the
state the older code would have left them in.

Revision ID: 202609291200_automation_completed_status
Revises: 202609281435_statement_tags
Create Date: 2026-09-29
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

import sqlalchemy as sa
from alembic import op

log = logging.getLogger("alembic.automation_completed_status")

revision = "202609291200_automation_completed_status"
down_revision = "202609281435_statement_tags"
branch_labels = None
depends_on = None

_SELECT = sa.text("SELECT id, payload FROM automations")
_UPDATE = sa.text("UPDATE automations SET payload = :payload WHERE id = :id")


def _count(value: object) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _one_shot_passed(trigger: dict, now: datetime) -> bool:
    if trigger.get("type") != "once":
        return False
    try:
        at = datetime.fromisoformat(str(trigger.get("at")))
    except ValueError:
        return False
    if at.tzinfo is None:
        at = at.astimezone()
    return at <= now


def _ran_out(payload: dict, now: datetime) -> bool:
    trigger = payload.get("trigger")
    if payload.get("status") != "paused" or payload.get("next_run_at"):
        return False
    if not isinstance(trigger, dict) or trigger.get("kind") != "schedule":
        return False
    runs = _count(payload.get("run_count"))
    cap = _count(payload.get("max_runs"))
    return (runs >= 1 and _one_shot_passed(trigger, now)) or (cap >= 1 and runs >= cap)


def _rewrite(conn, should_change, new_status: str) -> int:
    touched = 0
    for row_id, raw in conn.execute(_SELECT).fetchall():
        try:
            payload = json.loads(raw) if isinstance(raw, str) else raw
        except Exception:  # noqa: BLE001 — one unreadable row must not stop the rest
            log.warning("automation payload %s is unreadable; skipped", row_id)
            continue
        if not isinstance(payload, dict) or not should_change(payload):
            continue
        payload["status"] = new_status
        conn.execute(_UPDATE, {"payload": json.dumps(payload), "id": row_id})
        touched += 1
    return touched


def _upgrade(conn, *, now: datetime) -> None:
    touched = _rewrite(conn, lambda payload: _ran_out(payload, now), "completed")
    if touched:
        log.info("marked %d finished automation(s) completed", touched)


def _downgrade(conn) -> None:
    _rewrite(conn, lambda payload: payload.get("status") == "completed", "paused")


def upgrade() -> None:
    _upgrade(op.get_bind(), now=datetime.now(timezone.utc))


def downgrade() -> None:
    _downgrade(op.get_bind())
