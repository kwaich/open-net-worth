"""What a run is told about its own Automation beyond the instructions: the rule that woke it
and when it last ran to completion.

The instructions say WHAT to do; the trigger alone says WHEN (ADR 2026-10-11, instructions
carry no timing). So a run must see the configured trigger — with the names of the accounts
and payees it points at — to know why it fired, and the start of the last run that COMPLETED,
to scope work "since the last run" whatever cadence the user picks later. A failed or stopped
run may have done nothing, so it never moves that mark.
"""

from __future__ import annotations

from backend.db.automation_run_store import AutomationRunStore

from .options import condition_references
from .references import referenced_ids


def configured_trigger(automation: dict) -> dict:
    """The stored trigger, plus ``names`` for every account and payee id it mentions."""
    trigger = dict(automation.get("trigger") or {})
    ids = referenced_ids(trigger)
    if not ids:
        return trigger
    references = condition_references()
    names = {
        row["id"]: row["label"] for rows in references.values() for row in rows if row["id"] in ids
    }
    return {**trigger, "names": names} if names else trigger


def last_completed_run(automation_id: str, run: dict) -> dict | None:
    """The latest run before ``run`` that completed — ``{"started_at"}`` — or None."""
    row = AutomationRunStore().latest_completed(
        automation_id, before=str(run.get("started_at") or ""), exclude_id=str(run.get("id") or "")
    )
    return {"started_at": row.get("started_at")} if row else None
