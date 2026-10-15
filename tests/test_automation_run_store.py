import threading

import pytest

from backend.db.automation_run_store import AutomationRunStore, TransitionConflict
from backend.models.automation import AutomationRun


@pytest.fixture
def run_store(db_isolated):
    return AutomationRunStore()


def test_run_store_refuses_a_late_terminal_overwrite(run_store):
    run = run_store.start({"automation_id": "automation_a", "session_id": "automation-s"})
    completed, pruned = run_store.transition(
        run["id"], {"running"}, "completed", {"summary": "done"}
    )

    assert completed["status"] == "completed"
    assert pruned == []
    with pytest.raises(TransitionConflict):
        run_store.transition(run["id"], {"running"}, "failed", {"error": "late"})


def test_run_store_patches_live_progress_and_finds_the_conversation_owner(run_store):
    run = run_store.start({"automation_id": "automation_a", "session_id": "automation-s"})

    updated = run_store.patch(
        run["id"], {"running"}, {"summary": "Halfway", "steps": [{"done": False}]}
    )

    assert updated["summary"] == "Halfway"
    assert run_store.find_by_session_id("automation-s")["id"] == run["id"]
    run_store.transition(run["id"], {"running"}, "completed", {})
    with pytest.raises(TransitionConflict):
        run_store.patch(run["id"], {"running"}, {"summary": "late"})


def test_run_store_paginates_newest_first_and_retains_every_conversation_owner(run_store):
    waiting = run_store.start({"automation_id": "automation_a", "session_id": "waiting"})
    run_store.transition(waiting["id"], {"running"}, "waiting", {})
    completed_ids = []
    oldest_completed_id = None
    for number in range(51):
        run = run_store.start({"automation_id": "automation_a", "session_id": f"s-{number}"})
        if number == 0:
            oldest_completed_id = run["id"]
        _, pruned = run_store.transition(run["id"], {"running"}, "completed", {})
        completed_ids.extend(pruned)

    assert completed_ids == []
    assert run_store.find_by_id(oldest_completed_id) is not None
    rows = run_store.for_automation("automation_a", limit=100, offset=0)
    assert len(rows) == 52
    assert any(row["id"] == waiting["id"] and row["status"] == "waiting" for row in rows)
    assert any(row["id"] == oldest_completed_id for row in rows)
    assert len(run_store.for_automation("automation_a", limit=1, offset=1)) == 1
    assert run_store.delete_for_automation("automation_a") == [row["id"] for row in rows]
    assert run_store.for_automation("automation_a", limit=100, offset=0) == []


def test_list_by_statuses_spans_automations_newest_started_first(run_store):
    completed = run_store.start({"automation_id": "automation_a", "session_id": "s-done"})
    run_store.transition(completed["id"], {"running"}, "completed", {})

    older_running = run_store.start(
        {
            "automation_id": "automation_a",
            "session_id": "s-1",
            "started_at": "2026-01-01T00:00:00+00:00",
        }
    )
    newer_waiting = run_store.start(
        {
            "automation_id": "automation_b",
            "session_id": "s-2",
            "started_at": "2026-01-02T00:00:00+00:00",
        }
    )
    run_store.transition(newer_waiting["id"], {"running"}, "waiting", {})

    rows = run_store.list_by_statuses(["running", "waiting"], limit=20)

    assert [row["id"] for row in rows] == [newer_waiting["id"], older_running["id"]]
    assert all(row["status"] in {"running", "waiting"} for row in rows)


def test_list_by_statuses_respects_the_limit(run_store):
    for number in range(5):
        run_store.start({"automation_id": "automation_a", "session_id": f"s-{number}"})

    rows = run_store.list_by_statuses(["running"], limit=2)

    assert len(rows) == 2


def test_automation_run_model_round_trips_every_persisted_store_field(run_store):
    row = run_store.start(
        {
            "automation_id": "automation_a",
            "session_id": "automation-session",
            "trigger": {"kind": "event"},
            "execution": {"kind": "lucius"},
            "model": "claude-sonnet",
            "effort": "high",
            "summary": "started",
            "error": "",
            "steps": [{"title": "step"}],
            "proposed_changes": [{"kind": "note"}],
            "pending_tool_ids": ["tool_1"],
            "context": {"source": "test"},
            "files": ["report.md"],
            "working_dir": "/tmp/automation",
            "input_tokens": 10,
            "output_tokens": 20,
            "cache_creation_tokens": 30,
            "cache_read_tokens": 40,
            "cost_usd": 0.01,
            "stats": {"runs": 1},
        }
    )

    assert AutomationRun.model_validate(row).model_dump() == row


# test_run_store_fails_closed_on_corrupt_file — RETIRED (plan 4c task 1): AutomationRunStore
# is DB-backed now (PayloadTableStore), so there is no `_path`/file to corrupt, no
# `_fail_closed_on_corrupt` flag, and no quarantine mechanism — the premise (a corrupt JSON
# file) no longer applies to a DB row. Mirrors the retirement note already recorded for
# TransactionAuditStore in tests/test_json_store_durability.py: a DB read either succeeds or
# the database itself is broken, which is db.migrate.ensure_migrated()'s fail-closed boot
# gate's job, orthogonal to any one store.


def test_runtime_run_claim_only_replaces_the_expected_owner(db_isolated):
    from backend.db.automation_runtime_store import AutomationRuntimeStore

    store = AutomationRuntimeStore()
    store.put_for_automation(
        "automation_a",
        {"running_run_id": "run_old", "queue": [{"event_id": "event_1"}]},
    )

    assert not store.claim_run("automation_a", "run_new", reclaim_from="run_other")
    assert store.get_for_automation("automation_a") == {
        "running_run_id": "run_old",
        "queue": [{"event_id": "event_1"}],
    }
    assert store.claim_run("automation_a", "run_new", reclaim_from="run_old")
    assert store.get_for_automation("automation_a") == {
        "running_run_id": "run_new",
        "queue": [{"event_id": "event_1"}],
    }


def test_runtime_run_release_cannot_clear_a_newer_owner(db_isolated):
    from backend.db.automation_runtime_store import AutomationRuntimeStore

    store = AutomationRuntimeStore()
    assert store.claim_run("automation_a", "run_old")
    assert store.claim_run("automation_a", "run_new", reclaim_from="run_old")

    assert not store.release_run("automation_a", "run_old")
    assert store.get_for_automation("automation_a") == {"running_run_id": "run_new"}


def test_runtime_run_claim_reports_whether_this_caller_acquired_the_slot(db_isolated):
    from backend.db.automation_runtime_store import AutomationRuntimeStore

    store = AutomationRuntimeStore()

    assert store.claim_run_with_result("automation_a", "run_a") == (True, True)
    assert store.claim_run_with_result("automation_a", "run_a") == (True, False)


def test_runtime_enqueue_during_run_claim_cannot_erase_the_owner(db_isolated, monkeypatch):
    from backend.db.automation_runtime_store import AutomationRuntimeStore

    claim_has_lock = threading.Event()
    release_claim_write = threading.Event()
    enqueue_started = threading.Event()
    errors: list[BaseException] = []
    # _save_items (JsonStore) is gone post-migration (plan 4c task 1) — _fire_hook is its
    # PayloadTableStore analogue: called exactly once per _mutate_state cycle, immediately
    # before the write statement, STILL inside the same _use_conn(None) table-lock/transaction
    # (see automation_runtime_store.py's _mutate_state). Pausing here reproduces the original
    # test's exact semantics: the claim thread holds the table lock and is about to write;
    # a concurrent enqueue_events call on the same automation_id must block on that same lock
    # rather than interleave and silently drop the claim.
    original_hook = AutomationRuntimeStore._fire_hook

    def pause_claim_hook(self, conn, rows):
        if threading.current_thread().name == "claim-runtime":
            claim_has_lock.set()
            assert release_claim_write.wait(timeout=2)
        return original_hook(self, conn, rows)

    monkeypatch.setattr(AutomationRuntimeStore, "_fire_hook", pause_claim_hook)
    store = AutomationRuntimeStore()
    store.ensure_trigger_revision("automation_a", "triggerrev_a", {"event_queue", "event_seen"})

    def claim() -> None:
        try:
            assert store.claim_run("automation_a", "run_first")
        except BaseException as exc:
            errors.append(exc)

    def enqueue() -> None:
        enqueue_started.set()
        try:
            assert store.enqueue_events(
                "automation_a",
                [
                    {
                        "event_key": "txn:event_1",
                        "event": "txn",
                        "payload": {"event_key": "txn:event_1"},
                        "queued_at": "2026-08-02T00:00:00+00:00",
                        "trigger_revision": "triggerrev_a",
                    }
                ],
                trigger_revision="triggerrev_a",
                max_queue=500,
                max_seen=2_000,
            ) == (1, 0)
        except BaseException as exc:
            errors.append(exc)

    claim_thread = threading.Thread(target=claim, name="claim-runtime")
    claim_thread.start()
    assert claim_has_lock.wait(timeout=2)
    enqueue_thread = threading.Thread(target=enqueue, name="enqueue-runtime")
    enqueue_thread.start()
    assert enqueue_started.wait(timeout=2)
    release_claim_write.set()
    claim_thread.join(timeout=2)
    enqueue_thread.join(timeout=2)

    assert not claim_thread.is_alive()
    assert not enqueue_thread.is_alive()
    assert errors == []
    assert store.get_for_automation("automation_a") == {
        "trigger_revision": "triggerrev_a",
        "running_run_id": "run_first",
        "event_queue": [
            {
                "event_key": "txn:event_1",
                "event": "txn",
                "payload": {"event_key": "txn:event_1"},
                "queued_at": "2026-08-02T00:00:00+00:00",
                "trigger_revision": "triggerrev_a",
            }
        ],
        "event_seen": ["txn:event_1"],
    }
    assert not store.claim_run("automation_a", "run_second")


def test_stale_publisher_cannot_append_old_revision_after_trigger_edit(db_isolated):
    from backend.db.automation_runtime_store import AutomationRuntimeStore

    store = AutomationRuntimeStore()
    store.ensure_trigger_revision(
        "automation_a",
        "triggerrev_new",
        {"event_queue", "event_seen", "event_inflight"},
    )

    result = store.enqueue_events(
        "automation_a",
        [
            {
                "event_key": "txn:stale",
                "event": "txn",
                "payload": {"event_key": "txn:stale"},
                "queued_at": "2026-08-02T00:00:00+00:00",
                "trigger_revision": "triggerrev_old",
            }
        ],
        trigger_revision="triggerrev_old",
        max_queue=500,
        max_seen=2_000,
        state_updates={"balance_observations": {"account_a": 10}},
    )

    assert result == (0, 0)
    assert store.get_for_automation("automation_a") == {"trigger_revision": "triggerrev_new"}


def test_current_revision_reconciliation_prunes_a_legacy_stale_queue_head(db_isolated):
    from backend.db.automation_runtime_store import AutomationRuntimeStore

    store = AutomationRuntimeStore()
    store.put_for_automation(
        "automation_a",
        {
            "trigger_revision": "triggerrev_new",
            "event_queue": [
                {
                    "event_key": "txn:stale",
                    "trigger_revision": "triggerrev_old",
                }
            ],
            "event_seen": ["txn:stale"],
        },
    )

    state = store.ensure_trigger_revision(
        "automation_a",
        "triggerrev_new",
        {"event_queue", "event_seen", "event_inflight"},
    )

    assert state == {
        "trigger_revision": "triggerrev_new",
        "event_queue": [],
        "event_seen": [],
    }


def test_count_for_automation_matches_a_materialised_count(db_isolated):
    """The SQL COUNT must agree with counting the rows by hand.

    /api/automations/{id}/runs used `sum(1 for row in store.all() if ...)`, which built every
    run payload in the table to produce one integer — on an endpoint the Automations tab polls
    once a second while a run is live. Equivalence is pinned so the cheap path can't drift
    from the honest one.
    """
    store = AutomationRunStore()
    for i in range(5):
        store.start({"automation_id": "auto-a", "status": "running", "title": f"a{i}"})
    for i in range(3):
        store.start({"automation_id": "auto-b", "status": "running", "title": f"b{i}"})

    by_hand_a = sum(1 for r in store.all() if r.get("automation_id") == "auto-a")
    by_hand_b = sum(1 for r in store.all() if r.get("automation_id") == "auto-b")

    assert store.count_for_automation("auto-a") == by_hand_a == 5
    assert store.count_for_automation("auto-b") == by_hand_b == 3
    assert store.count_for_automation("auto-missing") == 0
