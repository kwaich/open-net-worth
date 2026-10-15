from datetime import datetime, timedelta, timezone

import pytest

from backend.db.automation_runtime_store import AutomationRuntimeStore
from backend.db.automation_store import AutomationStore


@pytest.fixture
def automation_store(db_isolated):
    return AutomationStore()


def test_manual_automation_stays_active_without_a_next_run(automation_store):
    row = automation_store.create(
        {
            "name": "On demand review",
            "instructions": "Review my month when I ask.",
            "status": "active",
            "trigger": {"kind": "manual"},
            "execution": {"kind": "lucius"},
        }
    )

    assert row["id"].startswith("automation_")
    assert row["next_run_at"] is None
    assert automation_store.active() == [row]


def test_automation_store_defaults_to_lucius_and_rejects_legacy_execution(automation_store):
    row = automation_store.create(
        {
            "name": "On demand review",
            "instructions": "Review my month when I ask.",
            "trigger": {"kind": "manual"},
        }
    )

    assert row["execution"] == {"kind": "lucius"}
    with pytest.raises(ValueError, match="Lucius"):
        automation_store.create(
            {
                "name": "Old agent wrapper",
                "instructions": "Run agent A.",
                "trigger": {"kind": "manual"},
                "execution": {"kind": "agent", "agent_id": "a"},
            }
        )


def test_automation_store_normalizes_known_fields_and_queries_due_rows(automation_store):
    due = automation_store.create(
        {
            "name": "  Morning brief  ",
            "instructions": "  Summarize changes.  ",
            "trigger": {"kind": "schedule", "type": "daily", "time": "08:00"},
            "execution": {"kind": "lucius"},
            "next_run_at": "2026-08-02T08:00:00+00:00",
            "unknown": "discard me",
        }
    )
    paused = automation_store.create(
        {
            "name": "Paused",
            "instructions": "Wait.",
            "status": "paused",
            "trigger": {"kind": "manual"},
            "execution": {"kind": "lucius"},
            "next_run_at": "2026-08-01T08:00:00+00:00",
        }
    )

    assert due["name"] == "Morning brief"
    assert due["instructions"] == "Summarize changes."
    assert "unknown" not in due
    assert automation_store.due("2026-08-02T08:00:00+00:00") == [due]
    assert automation_store.active() == [due, paused]
    assert datetime.fromisoformat(due["created_at"]).tzinfo is timezone.utc


def test_due_compares_instants_when_schedule_and_scheduler_use_different_offsets(automation_store):
    row = automation_store.create(
        {
            "name": "Singapore morning",
            "instructions": "Run.",
            "trigger": {"kind": "schedule", "type": "daily", "time": "08:00"},
            "execution": {"kind": "lucius"},
            "next_run_at": "2026-08-02T08:00:00+08:00",
        }
    )

    assert automation_store.due("2026-08-02T01:00:00+00:00") == [row]


def test_due_treats_legacy_naive_timestamp_as_server_local_wall_time(automation_store, monkeypatch):
    import backend.db.automation_store as mod

    monkeypatch.setattr(mod, "_local_tz", lambda: timezone(timedelta(hours=8)), raising=False)
    row = automation_store.create(
        {
            "name": "Legacy Singapore morning",
            "instructions": "Run.",
            "trigger": {"kind": "schedule", "type": "daily", "time": "08:00"},
            "execution": {"kind": "lucius"},
            "next_run_at": "2026-08-02T08:00:00",
        }
    )

    assert automation_store.due("2026-08-02T00:00:00+00:00") == [row]


def test_due_rejects_a_malformed_timestamp_instead_of_falling_back_to_text_order(automation_store):
    automation_store.create(
        {
            "name": "Broken schedule",
            "instructions": "Run.",
            "trigger": {"kind": "schedule", "type": "daily", "time": "08:00"},
            "execution": {"kind": "lucius"},
            "next_run_at": "not-an-iso-timestamp",
        }
    )

    with pytest.raises(ValueError):
        automation_store.due("2026-08-02T01:00:00+00:00")


# test_configuration_store_fails_closed_on_corrupt_file — RETIRED (plan 4d task 2):
# AutomationStore is DB-backed now (BookPayloadTableStore), so there is no `_path`/file to
# corrupt, no `_fail_closed_on_corrupt` flag, and no quarantine mechanism — the premise (a
# corrupt JSON file) no longer applies to a DB row. Mirrors the retirement note already
# recorded for AutomationRunStore in tests/test_automation_run_store.py and for
# TransactionAuditStore in tests/test_json_store_durability.py.


def test_runtime_store_replaces_and_deletes_state_for_one_automation(db_isolated):
    store = AutomationRuntimeStore()

    assert store.get_for_automation("automation_a") is None
    assert store.put_for_automation(
        "automation_a", {"last_fired_at": "2026-08-02T08:00:00+00:00"}
    ) == {"last_fired_at": "2026-08-02T08:00:00+00:00"}
    assert store.put_for_automation("automation_a", {"queue": [{"key": "txn_1"}]}) == {
        "queue": [{"key": "txn_1"}]
    }
    assert store.get_for_automation("automation_a") == {"queue": [{"key": "txn_1"}]}
    assert store.delete_for_automation("automation_a") is True
    assert store.get_for_automation("automation_a") is None


# test_runtime_store_fails_closed_and_quarantines_a_corrupt_event_queue — RETIRED (plan 4c
# task 1): AutomationRuntimeStore is DB-backed now (PayloadTableStore), so there is no
# `_path`/file to corrupt, no `_fail_closed_on_corrupt` flag, and no quarantine mechanism —
# the premise (a corrupt JSON file) no longer applies to a DB row. Mirrors the retirement
# note already recorded for AutomationRunStore in tests/test_automation_run_store.py and for
# TransactionAuditStore in tests/test_json_store_durability.py.
