"""The one-off backfill that gives already-failed runs their error text.

`last_turn_error` only started being stamped on 2026-08-16, so every run that failed before
then has the text on the run row and nothing on the session — the conversation still opens
with no explanation in it. This copies it across, but only where the SESSION's own status
already proves the turn failed.

The interesting half is what it REFUSES to touch: 18 of this instance's 41 failed runs have
`last_turn_status == "ok"`, because the turn succeeded and the RUN failed its goal check
("ended with 5 of 6 tasks unfinished"). Those conversations are not empty and their turn did
not error, so stamping one would be inventing a fact.
"""

from __future__ import annotations

import pytest

from backend.ai.assistant import upsert_session_meta
from backend.db.automation_run_store import AutomationRunStore
from backend.db.session_store import SessionStore
from backend.scripts.backfill_last_turn_error import backfill, plan

pytestmark = pytest.mark.usefixtures("db_isolated")


def _run(run_id: str, status: str, error: str | None) -> None:
    """Runs are born ``running`` at the store's own choke, so a terminal one is reached the
    way the app reaches it — through ``transition`` — never by writing a status directly."""
    store = AutomationRunStore()
    store.start(
        {
            "id": run_id,
            "automation_id": "task-1",
            "session_id": f"automation-run-{run_id}",
        }
    )
    store.transition(run_id, {"running"}, status, {"error": error})


def _session(run_id: str, **meta) -> str:
    sid = f"automation-run-{run_id}"
    SessionStore().upsert_meta(sid, {"id": sid, "source": "automation", **meta})
    return sid


def _error_of(sid: str) -> str | None:
    return {row["id"]: row for row in SessionStore().all_meta()}[sid].get("last_turn_error")


@pytest.mark.parametrize("status", ["error", "timeout", "interrupted"])
def test_a_turn_that_really_failed_gets_its_text(status):
    _run("r1", "failed", "Claude Code CLI reported an error: 400")
    sid = _session("r1", last_turn_status=status)

    backfill(commit=True)
    assert _error_of(sid) == "Claude Code CLI reported an error: 400"


def test_a_successful_turn_whose_RUN_failed_is_left_alone():
    """The 18-run case. `ended with N of M tasks unfinished` is a run-level verdict — the
    turn itself ended cleanly and its transcript is intact. Marking it as a turn error would
    put a failure on a conversation that never had one."""
    _run("r2", "failed", "ended with 5 of 6 tasks unfinished")
    sid = _session("r2", last_turn_status="ok")

    backfill(commit=True)
    assert _error_of(sid) is None


def test_a_session_predating_the_field_is_left_alone():
    """No stored status means no evidence the session's last turn was this run's at all."""
    _run("r3", "failed", "boom")
    sid = _session("r3")

    backfill(commit=True)
    assert _error_of(sid) is None


def test_a_newer_message_is_never_overwritten():
    """Idempotency that matters: re-running after a LATER failure must not replace the
    current message with an older run's."""
    _run("r4", "failed", "the old failure")
    sid = _session("r4", last_turn_status="error")
    upsert_session_meta(sid, last_turn_error="the current failure")

    backfill(commit=True)
    assert _error_of(sid) == "the current failure"


def test_a_dry_run_writes_nothing():
    _run("r5", "failed", "boom")
    sid = _session("r5", last_turn_status="error")

    assert len(backfill(commit=False)) == 1
    assert _error_of(sid) is None


def test_a_completed_run_is_not_a_candidate():
    _run("r6", "completed", None)
    _session("r6", last_turn_status="ok")
    assert plan()[0] == []
