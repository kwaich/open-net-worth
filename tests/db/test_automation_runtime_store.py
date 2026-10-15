"""AutomationRuntimeStore — DB-CRUD round-trip + the _mutate_state CAS-conflict and
empty-state-deletes-the-row contracts (plan 4c task 1, Bucket C). Broader
service/scheduler-level behavior is already covered by tests/test_automation_runner.py,
tests/test_automation_events.py, etc.; this is the store's OWN persistence-contract
coverage."""

from __future__ import annotations

from backend.db.automation_runtime_store import AutomationRuntimeStore


def test_crud_round_trip(db_isolated):
    store = AutomationRuntimeStore()
    assert store.get_for_automation("automation_a") is None

    store.put_for_automation("automation_a", {"foo": "bar"})
    assert store.get_for_automation("automation_a") == {"foo": "bar"}

    store.put_for_automation("automation_a", {"foo": "baz"})
    assert store.get_for_automation("automation_a") == {"foo": "baz"}


def test_claim_run_cas_conflict_rejects_a_reclaim_from_the_wrong_owner(db_isolated):
    """The compare-and-set gate: a caller can only reclaim FROM the owner it names."""
    store = AutomationRuntimeStore()
    assert store.claim_run("automation_a", "run_1")

    # A reclaim naming the WRONG current owner is rejected — the slot stays with run_1.
    assert store.claim_run("automation_a", "run_2", reclaim_from="run_other") is False
    assert store.get_for_automation("automation_a") == {"running_run_id": "run_1"}

    # Naming the RIGHT current owner succeeds.
    assert store.claim_run("automation_a", "run_2", reclaim_from="run_1") is True
    assert store.get_for_automation("automation_a") == {"running_run_id": "run_2"}


def test_release_run_on_the_sole_state_key_deletes_the_row_not_just_empties_it(db_isolated):
    """_mutate_state's empty-state branch: when a mutation leaves `state` empty, the ROW
    itself is deleted (not left behind as an empty-dict row) — proven at the table level via
    find_by_id, not just via get_for_automation()'s already-equivalent None-for-both-cases
    read."""
    store = AutomationRuntimeStore()
    store.claim_run("automation_a", "run_1")  # state == {"running_run_id": "run_1"}, one row
    assert store.find_by_id("automation_a") is not None

    assert store.release_run("automation_a", "run_1") is True
    # The row is GONE, not present-with-empty-state.
    assert store.find_by_id("automation_a") is None
    assert store.all() == []


def test_release_run_wrong_owner_is_a_no_op(db_isolated):
    store = AutomationRuntimeStore()
    store.claim_run("automation_a", "run_1")

    assert store.release_run("automation_a", "run_other") is False
    assert store.get_for_automation("automation_a") == {"running_run_id": "run_1"}


def test_clear_fields_preserves_state_that_still_has_other_keys(db_isolated):
    """The empty-state-deletes-row rule only fires when NOTHING is left — a partial clear
    that leaves other keys behind must keep the row (UPDATE, not DELETE)."""
    store = AutomationRuntimeStore()
    store.put_for_automation("automation_a", {"running_run_id": "run_1", "queue": [1, 2]})

    assert store.clear_fields("automation_a", ["queue"]) is True
    assert store.find_by_id("automation_a") is not None
    assert store.get_for_automation("automation_a") == {"running_run_id": "run_1"}
