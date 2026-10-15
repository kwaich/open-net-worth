"""Unit tests for backend/services/automations/activity.py — the Lucius landing screen's
Activity panel source. Covers the running/waiting + recently-finished filter + join with
automation name (Active) and the status=active + future-next_run_at filter/sort (Scheduled)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from backend.db.automation_run_store import AutomationRunStore
from backend.db.automation_store import AutomationStore
from backend.services.automations.activity import automation_activity

FUTURE = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
FURTHER_FUTURE = (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat()
PAST = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
JUST_NOW = datetime.now(timezone.utc).isoformat()
LONG_AGO = (datetime.now(timezone.utc) - timedelta(hours=5)).isoformat()


@pytest.fixture
def stores(db_isolated):
    return AutomationStore(), AutomationRunStore()


def test_active_includes_running_waiting_and_recently_finished(stores):
    automations, runs = stores
    automation = automations.create({"name": "Daily Digest"})

    running = runs.start(
        {
            "automation_id": automation["id"],
            "started_at": "2026-01-01T00:00:00+00:00",
            "session_id": "session_running",
        }
    )
    waiting_run = runs.start(
        {"automation_id": automation["id"], "started_at": "2026-01-02T00:00:00+00:00"}
    )
    runs.transition(waiting_run["id"], {"running"}, "waiting", {})
    # Finishes just now — inside the recency window, so it stays in Active a while
    # ("did that just run OK?") instead of vanishing the instant it lands.
    completed_run = runs.start(
        {"automation_id": automation["id"], "started_at": "2026-01-03T00:00:00+00:00"}
    )
    runs.transition(completed_run["id"], {"running"}, "completed", {"finished_at": JUST_NOW})
    failed_run = runs.start(
        {"automation_id": automation["id"], "started_at": "2026-01-04T00:00:00+00:00"}
    )
    runs.transition(failed_run["id"], {"running"}, "failed", {"finished_at": JUST_NOW})
    # Also finished, but long past the recency window — excluded (see the next test for the
    # exact cutoff).
    stale_completed = runs.start(
        {"automation_id": automation["id"], "started_at": "2026-01-05T00:00:00+00:00"}
    )
    runs.transition(stale_completed["id"], {"running"}, "completed", {"finished_at": LONG_AGO})

    result = automation_activity()

    active_ids = [row["run_id"] for row in result["active"]]
    assert running["id"] in active_ids
    assert waiting_run["id"] in active_ids
    assert completed_run["id"] in active_ids
    assert failed_run["id"] in active_ids
    assert stale_completed["id"] not in active_ids
    for row in result["active"]:
        assert row["automation_id"] == automation["id"]
        assert row["automation_name"] == "Daily Digest"

    completed_row = next(r for r in result["active"] if r["run_id"] == completed_run["id"])
    assert completed_row["status"] == "completed"
    assert completed_row["finished_at"] == JUST_NOW

    running_row = next(r for r in result["active"] if r["run_id"] == running["id"])
    assert running_row["session_id"] == "session_running"


def test_active_excludes_a_completed_run_just_past_the_recency_window(stores):
    automations, runs = stores
    automation = automations.create({"name": "Daily Digest"})
    just_outside = (datetime.now(timezone.utc) - timedelta(hours=4, minutes=1)).isoformat()
    run = runs.start({"automation_id": automation["id"], "started_at": "2026-01-01T00:00:00+00:00"})
    runs.transition(run["id"], {"running"}, "completed", {"finished_at": just_outside})

    result = automation_activity()

    assert run["id"] not in [row["run_id"] for row in result["active"]]


# Regression (owner report 2026-08-22): a run that "stopped because the server restarted" —
# a graceful shutdown/reload journals every in-flight turn `cancelled`
# (ai/turns.py::shutdown(), backend/main.py's lifespan teardown), not `failed` — was invisible
# in Active despite finishing 30 minutes earlier. `cancelled` ALSO covers a deliberate user
# Stop; this codebase doesn't distinguish the two at the status level, but both answer the same
# "did that run actually finish OK?" question this panel exists for, so a recent one now shows
# either way (an earlier version of this test asserted the opposite — excluding it was the bug).
def test_active_includes_a_recently_cancelled_run(stores):
    automations, runs = stores
    automation = automations.create({"name": "Daily Digest"})
    run = runs.start({"automation_id": automation["id"], "started_at": "2026-01-01T00:00:00+00:00"})
    runs.transition(run["id"], {"running"}, "cancelled", {"finished_at": JUST_NOW})

    result = automation_activity()

    active_row = next(r for r in result["active"] if r["run_id"] == run["id"])
    assert active_row["status"] == "cancelled"


def test_active_excludes_a_cancelled_run_past_the_recency_window(stores):
    automations, runs = stores
    automation = automations.create({"name": "Daily Digest"})
    run = runs.start({"automation_id": automation["id"], "started_at": "2026-01-01T00:00:00+00:00"})
    runs.transition(run["id"], {"running"}, "cancelled", {"finished_at": LONG_AGO})

    result = automation_activity()

    assert run["id"] not in [row["run_id"] for row in result["active"]]


def test_active_sorts_most_recently_started_first_across_automations(stores):
    automations, runs = stores
    a = automations.create({"name": "A"})
    b = automations.create({"name": "B"})
    older = runs.start({"automation_id": a["id"], "started_at": "2026-01-01T00:00:00+00:00"})
    newer = runs.start({"automation_id": b["id"], "started_at": "2026-01-05T00:00:00+00:00"})

    result = automation_activity()

    assert [row["run_id"] for row in result["active"]] == [newer["id"], older["id"]]


# Regression (owner report 2026-08-22): a run genuinely `running` right then was missing from
# Active — a noisy, high-frequency automation's completed runs, all started more recently, had
# already filled the shared _FETCH_LIMIT cap by the time the query reached the older,
# still-in-flight one.
def test_active_never_lets_terminal_noise_crowd_out_an_in_flight_run(stores):
    from backend.services.automations.activity import _FETCH_LIMIT

    automations, runs = stores
    automation = automations.create({"name": "Noisy"})
    running = runs.start(
        {"automation_id": automation["id"], "started_at": "2020-01-01T00:00:00+00:00"}
    )
    # More completed runs than _FETCH_LIMIT, every one started (and finished) more recently
    # than the still-running one above.
    for i in range(_FETCH_LIMIT + 5):
        started = (datetime.now(timezone.utc) - timedelta(minutes=i + 1)).isoformat()
        noisy_run = runs.start({"automation_id": automation["id"], "started_at": started})
        runs.transition(noisy_run["id"], {"running"}, "completed", {"finished_at": JUST_NOW})

    result = automation_activity()

    assert running["id"] in [row["run_id"] for row in result["active"]]


# Matches the reference mockup: an hours-old `waiting` row still led a minutes-old `running`
# one — needing a human decision outranks merely being newer.
def test_active_lists_waiting_before_running_regardless_of_recency(stores):
    automations, runs = stores
    automation = automations.create({"name": "Mixed"})
    running = runs.start(
        {"automation_id": automation["id"], "started_at": "2026-01-05T00:00:00+00:00"}
    )
    waiting_run = runs.start(
        {"automation_id": automation["id"], "started_at": "2026-01-01T00:00:00+00:00"}
    )
    runs.transition(waiting_run["id"], {"running"}, "waiting", {})

    result = automation_activity()

    assert [row["run_id"] for row in result["active"]] == [waiting_run["id"], running["id"]]


def test_scheduled_excludes_paused_and_past_or_missing_next_run_at(stores):
    automations, _runs = stores
    due_soon = automations.create({"name": "Due Soon"})
    automations.update_by_id(due_soon["id"], {"next_run_at": FUTURE})
    paused = automations.create({"name": "Paused", "status": "paused"})
    automations.update_by_id(paused["id"], {"next_run_at": FUTURE})
    already_past = automations.create({"name": "Overdue"})
    automations.update_by_id(already_past["id"], {"next_run_at": PAST})
    no_schedule = automations.create({"name": "No Schedule"})

    result = automation_activity()

    scheduled_ids = {row["automation_id"] for row in result["scheduled"]}
    assert scheduled_ids == {due_soon["id"]}
    assert paused["id"] not in scheduled_ids
    assert already_past["id"] not in scheduled_ids
    assert no_schedule["id"] not in scheduled_ids


def test_scheduled_sorts_soonest_next_run_at_first(stores):
    automations, _runs = stores
    later = automations.create({"name": "Later"})
    automations.update_by_id(later["id"], {"next_run_at": FURTHER_FUTURE})
    sooner = automations.create({"name": "Sooner"})
    automations.update_by_id(sooner["id"], {"next_run_at": FUTURE})

    result = automation_activity()

    assert [row["automation_id"] for row in result["scheduled"]] == [sooner["id"], later["id"]]


def test_activity_is_empty_when_nothing_is_active_or_scheduled(stores):
    result = automation_activity()

    assert result == {"active": [], "scheduled": [], "failed": []}
