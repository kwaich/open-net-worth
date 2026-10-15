"""A failed Automation stays visible until a later run succeeds.

Owner report 2026-10-14: a run that failed showed "Failed" only until it was read, four failed
runs never showed it at all (their turn ended ``timeout``/``interrupted``), and two said "New
reply" (the run failed after a turn that said ``ok``). The run row's own status is the truth:
the Lucius home lists every automation whose LATEST run failed under Needs attention, and the
conversation list carries each run's status beside its conversation.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from backend.ai import assistant
from backend.db.automation_run_store import AutomationRunStore
from backend.db.automation_store import AutomationStore
from backend.services.automations.activity import automation_activity, run_status_by_session


def ago(hours: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()


TIMEOUT = "Claude Code CLI timed out after 300s (raise ONW_CLAUDE_CLI_TIMEOUT if this is expected)."


@pytest.fixture
def stores(db_isolated):
    return AutomationStore(), AutomationRunStore()


def _run(runs, automation_id, started_at, status, **patch):
    run = runs.start(
        {
            "automation_id": automation_id,
            "started_at": started_at,
            "session_id": f"automation-run-{started_at}",
        }
    )
    if status != "running":
        runs.transition(run["id"], {"running"}, status, patch)
    return run


def test_an_automation_whose_latest_run_failed_needs_attention(stores):
    automations, runs = stores
    digest = automations.create({"name": "Email digest"})
    _run(runs, digest["id"], ago(14), "completed")
    finished = ago(1)
    failed = _run(runs, digest["id"], ago(2), "failed", error=TIMEOUT, finished_at=finished)

    [item] = automation_activity()["failed"]

    assert item["automation_name"] == "Email digest"
    assert item["run_id"] == failed["id"]
    assert item["session_id"] == failed["session_id"]
    assert item["finished_at"] == finished
    assert item["error"] == TIMEOUT


def test_a_later_successful_run_clears_it(stores):
    automations, runs = stores
    digest = automations.create({"name": "Email digest"})
    _run(runs, digest["id"], "2026-10-12T00:00:00+00:00", "failed", error="boom")
    _run(runs, digest["id"], "2026-10-13T00:00:00+00:00", "completed")

    assert automation_activity()["failed"] == []


def test_a_retry_that_succeeds_clears_it(stores):
    automations, runs = stores
    digest = automations.create({"name": "Email digest"})
    failed = _run(runs, digest["id"], "2026-10-13T00:00:00+00:00", "failed", error="boom")
    # A follow-up in the run's own conversation re-enters running on the SAME row.
    runs.transition(failed["id"], {"failed"}, "running", {"error": None, "finished_at": None})
    runs.transition(failed["id"], {"running"}, "completed", {})

    assert automation_activity()["failed"] == []


def test_a_new_run_in_flight_replaces_the_failure(stores):
    automations, runs = stores
    digest = automations.create({"name": "Email digest"})
    _run(runs, digest["id"], "2026-10-12T00:00:00+00:00", "failed", error="boom")
    _run(runs, digest["id"], "2026-10-13T00:00:00+00:00", "running")

    assert automation_activity()["failed"] == []


def test_each_automation_is_judged_by_its_own_latest_run(stores):
    automations, runs = stores
    a = automations.create({"name": "A"})
    b = automations.create({"name": "B"})
    _run(runs, a["id"], ago(2), "failed", error="boom")
    _run(runs, b["id"], ago(1), "completed")

    assert [i["automation_name"] for i in automation_activity()["failed"]] == ["A"]


def test_a_failure_waits_at_most_a_day(stores):
    # A paused or finished automation never runs again — without a cap its failure would sit
    # in Needs attention forever (owner, 2026-10-14). The history list keeps saying Failed.
    automations, runs = stores
    recent = automations.create({"name": "Recent"})
    stale = automations.create({"name": "Stale"})
    _run(runs, recent["id"], ago(24), "failed", error="boom", finished_at=ago(23))
    old = _run(runs, stale["id"], ago(26), "failed", error="boom", finished_at=ago(25))
    automations.update_by_id(stale["id"], {"status": "paused"})

    assert [i["automation_name"] for i in automation_activity()["failed"]] == ["Recent"]
    assert run_status_by_session()[old["session_id"]] == "failed"


def test_an_automation_being_deleted_is_left_out(stores):
    automations, runs = stores
    digest = automations.create({"name": "Email digest"})
    _run(runs, digest["id"], "2026-10-12T00:00:00+00:00", "failed", error="boom")
    automations.update_by_id(digest["id"], {"status": "deleting"})

    assert automation_activity()["failed"] == []


def test_outcomes_reads_only_the_outcome_fields_oldest_first(stores):
    automations, runs = stores
    digest = automations.create({"name": "Email digest"})
    first = _run(runs, digest["id"], "2026-10-12T00:00:00+00:00", "completed")
    second = _run(runs, digest["id"], "2026-10-13T00:00:00+00:00", "failed", error="boom")

    rows = runs.outcomes()

    assert [r["id"] for r in rows] == [first["id"], second["id"]]
    assert set(rows[0]) == {
        "id",
        "automation_id",
        "session_id",
        "status",
        "started_at",
        "finished_at",
        "error",
    }
    assert rows[1]["error"] == "boom"


def test_the_conversation_list_carries_each_runs_status(stores, monkeypatch):
    automations, runs = stores
    digest = automations.create({"name": "Email digest"})
    failed = _run(runs, digest["id"], "2026-10-13T00:00:00+00:00", "failed", error="boom")
    rows = [
        {"id": failed["session_id"], "updated_at": "2026-10-13T01:00:00+00:00"},
        {"id": "ordinary-chat", "updated_at": "2026-10-13T00:00:00+00:00"},
    ]
    monkeypatch.setattr(assistant, "_read_index", lambda: rows)

    listed = {row["id"]: row for row in assistant.list_sessions(with_run_status=True)}

    assert run_status_by_session() == {failed["session_id"]: "failed"}
    assert listed[failed["session_id"]]["run_status"] == "failed"
    assert "run_status" not in listed["ordinary-chat"]
    assert "run_status" not in assistant.list_sessions()[0]
