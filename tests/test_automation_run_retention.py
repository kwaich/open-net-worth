"""services/automations/runs.py — `_trim_runs_to_cap`, unit-level.

Data-hygiene fix: AutomationRunStore had no retention of its own (unlike RunHistoryStore's
per-entity cap) — a long-lived automation's runs, and each run's OWN dedicated transcript
session, accumulated without bound. See tests/test_automation_service.py for the end-to-end
regression through `run_automation` itself; these are the isolated behavior checks.
"""

from __future__ import annotations

from backend.db.automation_run_store import AutomationRunStore
from backend.db.session_store import SessionStore
from backend.services.automations import runs as runs_mod


def _seed_completed_run(run_store: AutomationRunStore, automation_id: str, index: int) -> dict:
    session_id = f"automation-run-{automation_id}-{index}"
    run = run_store.start({"automation_id": automation_id, "session_id": session_id})
    run_store.transition(run["id"], {"running"}, "completed", {})
    return run_store.find_by_id(run["id"])


def test_trim_keeps_the_newest_cap_and_drops_the_rest_with_their_sessions(db_isolated):
    run_store = AutomationRunStore()
    session_store = SessionStore()
    total = runs_mod._MAX_RUNS_PER_AUTOMATION + 3
    seeded = [_seed_completed_run(run_store, "auto-1", i) for i in range(total)]
    for run in seeded:
        session_store.upsert_meta(run["session_id"], {"source": "automation"})
        session_store.append_messages(run["session_id"], [{"role": "user", "content": "hi"}])

    runs_mod._trim_runs_to_cap("auto-1")

    remaining = run_store.for_automation("auto-1", limit=1000, offset=0)
    assert len(remaining) == runs_mod._MAX_RUNS_PER_AUTOMATION
    # Seeded first == lowest seq == oldest — for_automation is newest-first, so these are the
    # ones past the cap.
    for run in seeded[:3]:
        assert run_store.find_by_id(run["id"]) is None
        assert session_store.get_meta(run["session_id"]) is None
        assert session_store.message_count(run["session_id"]) == 0
    for run in seeded[3:]:
        assert run_store.find_by_id(run["id"]) is not None
        assert session_store.get_meta(run["session_id"]) is not None
        assert session_store.message_count(run["session_id"]) == 1


def test_trim_is_a_noop_under_the_cap(db_isolated):
    run_store = AutomationRunStore()
    for i in range(5):
        _seed_completed_run(run_store, "auto-2", i)

    runs_mod._trim_runs_to_cap("auto-2")

    assert len(run_store.for_automation("auto-2", limit=1000, offset=0)) == 5


def test_trim_never_deletes_a_non_terminal_run(db_isolated):
    """Defensive: for_automation is newest-first and an automation has at most one live run
    at a time in practice (the runtime claim serializes it), but the trim must never delete a
    running/waiting row even if it somehow ended up past the cap boundary."""
    run_store = AutomationRunStore()
    stuck = run_store.start({"automation_id": "auto-3", "session_id": "automation-run-stuck"})
    for i in range(runs_mod._MAX_RUNS_PER_AUTOMATION + 5):
        _seed_completed_run(run_store, "auto-3", i)

    runs_mod._trim_runs_to_cap("auto-3")

    assert run_store.find_by_id(stuck["id"]) is not None


def test_trim_only_affects_the_named_automation(db_isolated):
    run_store = AutomationRunStore()
    for i in range(runs_mod._MAX_RUNS_PER_AUTOMATION + 2):
        _seed_completed_run(run_store, "auto-4", i)
    other = _seed_completed_run(run_store, "auto-5", 0)

    runs_mod._trim_runs_to_cap("auto-4")

    assert run_store.find_by_id(other["id"]) is not None


def test_trim_run_with_no_session_id_is_deleted_without_touching_sessions(db_isolated):
    """A run started with no session (defensive — every real run always has one) must not
    make the trim raise when it tries to drop the session."""
    run_store = AutomationRunStore()
    seeded = []
    for i in range(runs_mod._MAX_RUNS_PER_AUTOMATION + 1):
        run = run_store.start({"automation_id": "auto-6", "session_id": None})
        run_store.transition(run["id"], {"running"}, "completed", {})
        seeded.append(run_store.find_by_id(run["id"]))

    runs_mod._trim_runs_to_cap("auto-6")

    remaining = run_store.for_automation("auto-6", limit=1000, offset=0)
    assert len(remaining) == runs_mod._MAX_RUNS_PER_AUTOMATION
    assert run_store.find_by_id(seeded[0]["id"]) is None
