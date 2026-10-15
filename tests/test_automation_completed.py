"""A schedule with no runs left is ``completed`` — never ``paused``.

``paused`` is the USER's choice and ``completed`` is the schedule running out (a one-shot
that ran, an rrule past its UNTIL, a ``max_runs`` cap reached). One status for both left the
Automations list unable to tell a finished one-shot from something deliberately parked.
"""

from __future__ import annotations

import importlib.util
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import sqlalchemy as sa

from backend.core.errors import ValidationError
from backend.db.automation_store import AutomationStore
from backend.services.automations.runs import _finish_automation_once
from backend.services.automations.service import apply_automation_change

_PAST = "2026-08-01T09:00:00+00:00"


def _once(at: str = _PAST, **extra) -> dict:
    return {
        "name": "Remind me once",
        "instructions": "Remind me.",
        "trigger": {"kind": "schedule", "type": "once", "at": at},
        "execution": {"kind": "lucius"},
        **extra,
    }


def _completed() -> dict:
    created = AutomationStore().create({**_once(), "status": "completed"})
    return created


def _finish(automation: dict, run_id: str = "run_1") -> dict:
    run = {"id": run_id, "finished_at": datetime.now(timezone.utc).isoformat(), "stats": {}}
    _finish_automation_once(automation, run, "completed", {"scheduled_for": _PAST})
    return AutomationStore().find_by_id(automation["id"])


def test_a_one_shot_that_ran_is_completed_not_paused(db_isolated):
    automation = AutomationStore().create({**_once(), "status": "active"})
    finished = _finish(automation)
    assert finished["status"] == "completed"
    assert finished["next_run_at"] is None


def test_reaching_max_runs_completes_a_recurring_schedule(db_isolated):
    automation = AutomationStore().create(
        {
            **_once(),
            "trigger": {"kind": "schedule", "type": "daily", "time": "09:00", "tzid": "UTC"},
            "max_runs": 1,
            "status": "active",
        }
    )
    assert _finish(automation)["status"] == "completed"


def test_a_recurring_schedule_with_runs_left_stays_active(db_isolated):
    automation = AutomationStore().create(
        {
            **_once(),
            "trigger": {"kind": "schedule", "type": "daily", "time": "09:00", "tzid": "UTC"},
            "status": "active",
        }
    )
    finished = _finish(automation)
    assert finished["status"] == "active"
    assert finished["next_run_at"] is not None


def test_completed_automations_are_listed(db_isolated):
    completed = _completed()
    assert [row["id"] for row in AutomationStore().all()] == [completed["id"]]


def test_a_completed_automation_can_still_be_renamed(db_isolated):
    completed = _completed()
    renamed = apply_automation_change(
        {"op": "update", "id": completed["id"], "changes": {"name": "Done reminder"}}
    )
    assert renamed["name"] == "Done reminder"
    assert renamed["status"] == "completed"


def test_completed_is_never_set_by_a_caller(db_isolated):
    created = apply_automation_change({"op": "create", "automation": _once(_future())})
    with pytest.raises(ValidationError, match="completed"):
        apply_automation_change(
            {"op": "update", "id": created["id"], "changes": {"status": "completed"}}
        )
    with pytest.raises(ValidationError, match="completed"):
        apply_automation_change({"op": "create", "automation": {**_once(), "status": "completed"}})


@pytest.mark.parametrize("op", ["resume", "update"])
def test_resuming_a_completed_one_shot_with_no_future_run_is_refused(db_isolated, op):
    completed = _completed()
    change = (
        {"op": "resume", "id": completed["id"]}
        if op == "resume"
        else {"op": "update", "id": completed["id"], "changes": {"status": "active"}}
    )
    with pytest.raises(ValidationError, match="no future run"):
        apply_automation_change(change)
    assert AutomationStore().find_by_id(completed["id"])["status"] == "completed"


def test_an_editor_save_that_echoes_the_row_keeps_it_completed(db_isolated):
    """The editor re-sends status and trigger on every save — a rename of a finished
    one-shot must neither be refused as caller-set `completed` nor try to re-arm it."""
    completed = _completed()
    saved = apply_automation_change(
        {
            "op": "update",
            "id": completed["id"],
            "changes": {
                "name": "Renamed",
                "status": "completed",
                "trigger": dict(completed["trigger"]),
            },
        }
    )
    assert saved["name"] == "Renamed"
    assert saved["status"] == "completed"


def test_giving_a_completed_one_shot_a_new_date_rearms_it(db_isolated):
    completed = _completed()
    future = _future()
    rearmed = apply_automation_change(
        {
            "op": "update",
            "id": completed["id"],
            "changes": {"trigger": {"kind": "schedule", "type": "once", "at": future}},
        }
    )
    assert rearmed["status"] == "active"
    assert rearmed["next_run_at"] is not None


def test_switching_a_completed_one_to_manual_leaves_it_active_and_unscheduled(
    db_isolated,
):
    completed = _completed()
    rearmed = apply_automation_change(
        {"op": "update", "id": completed["id"], "changes": {"trigger": {"kind": "manual"}}}
    )
    assert rearmed["status"] == "active"
    assert rearmed["next_run_at"] is None


def _future() -> str:
    return (datetime.now(timezone.utc) + timedelta(days=30)).replace(microsecond=0).isoformat()


# ── backfill of rows finished before `completed` existed ─────────────────────────────


def _migration():
    path = next(
        Path("backend/db/alembic/versions").glob("*_automation_completed_status.py"),
    )
    spec = importlib.util.spec_from_file_location("automation_completed_migration", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _backfill(rows: dict[str, dict], *, downgrade: bool = False) -> dict[str, dict]:
    engine = sa.create_engine("sqlite://")
    with engine.begin() as conn:
        conn.execute(sa.text("CREATE TABLE automations (id TEXT PRIMARY KEY, payload TEXT)"))
        for row_id, payload in rows.items():
            conn.execute(
                sa.text("INSERT INTO automations VALUES (:id, :payload)"),
                {"id": row_id, "payload": json.dumps(payload)},
            )
        module = _migration()
        if downgrade:
            module._downgrade(conn)
        else:
            module._upgrade(conn, now=datetime(2026, 9, 29, tzinfo=timezone.utc))
        result = conn.execute(sa.text("SELECT id, payload FROM automations")).fetchall()
    return {row_id: json.loads(payload) for row_id, payload in result}


def _row(status: str, trigger: dict, **extra) -> dict:
    return {"status": status, "trigger": trigger, "next_run_at": None, **extra}


_ONCE = {"kind": "schedule", "type": "once", "at": _PAST}
_DAILY = {"kind": "schedule", "type": "daily", "time": "09:00"}


def test_backfill_completes_only_schedules_that_ran_out():
    out = _backfill(
        {
            "ran_once": _row("paused", _ONCE, run_count=1),
            "capped": _row("paused", _DAILY, run_count=3, max_runs=3),
            "user_paused_daily": _row("paused", _DAILY, run_count=5),
            "paused_before_its_date": _row(
                "paused", {**_ONCE, "at": "2026-12-01T09:00:00+00:00"}, run_count=1
            ),
            "paused_never_ran": _row("paused", _ONCE, run_count=0),
            "active_once": _row("active", _ONCE, run_count=1),
            "unreadable_date": _row("paused", {**_ONCE, "at": "soon"}, run_count=1),
        }
    )
    assert {row_id for row_id, row in out.items() if row["status"] == "completed"} == {
        "ran_once",
        "capped",
    }


def test_backfill_downgrade_returns_completed_rows_to_paused():
    out = _backfill({"done": _row("completed", _ONCE, run_count=1)}, downgrade=True)
    assert out["done"]["status"] == "paused"
