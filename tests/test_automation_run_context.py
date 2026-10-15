"""A run is told the rule that woke it and when the Automation last ran — the instructions say
what to do, never when, so this is how a run scopes "since the last run" (ADR 2026-10-11)."""

from __future__ import annotations

import pytest

from backend.ai.automation_runner_adapter import _automation_message
from backend.core.automation_runner import AutomationRunnerRequest
from backend.db.automation_run_store import AutomationRunStore
from backend.services.automations import run_context

TRIGGER = {
    "kind": "event",
    "event": "txn",
    "when": {
        "conditions": [
            {"field": "payee", "op": "is", "value": ["pay_grab", "pay_uber"]},
            {"field": "account", "op": "is", "value": "acc_card"},
            {"field": "amount", "op": "over", "value": 20},
        ]
    },
    "limit": "each",
}


@pytest.fixture(autouse=True)
def references(monkeypatch):
    monkeypatch.setattr(
        run_context,
        "condition_references",
        lambda: {
            "accounts": [{"id": "acc_card", "label": "Visa"}, {"id": "acc_x", "label": "X"}],
            "payees": [{"id": "pay_grab", "label": "Grab"}, {"id": "pay_uber", "label": "Uber"}],
        },
    )


def test_the_configured_trigger_carries_the_names_of_what_it_points_at():
    configured = run_context.configured_trigger({"trigger": TRIGGER})
    assert configured["when"] == TRIGGER["when"]
    assert configured["names"] == {"pay_grab": "Grab", "pay_uber": "Uber", "acc_card": "Visa"}


def test_a_schedule_needs_no_names():
    schedule = {"kind": "schedule", "type": "weekly", "day": "mon", "time": "08:30"}
    assert run_context.configured_trigger({"trigger": schedule}) == schedule


class _Runs:
    """The real store's query, over rows given oldest first as ``query`` returns them."""

    def __init__(self, rows):
        self.rows = rows

    def query(self, automation_id):
        return list(self.rows)

    latest_completed = AutomationRunStore.latest_completed


def test_since_the_last_run_means_the_last_run_that_completed(monkeypatch):
    """A failed run may have logged nothing — counting from it would skip a week of mail."""
    rows = [
        {"id": "run_old", "started_at": "2026-09-27T08:30:00", "status": "completed"},
        *[
            {"id": f"run_f{n}", "started_at": f"2026-10-0{n}T08:30:00", "status": "failed"}
            for n in range(1, 10)
        ],
        {"id": "run_now", "started_at": "2026-10-11T08:30:00", "status": "running"},
    ]
    monkeypatch.setattr(run_context, "AutomationRunStore", lambda: _Runs(rows))
    assert run_context.last_completed_run("a1", rows[-1]) == {"started_at": "2026-09-27T08:30:00"}
    assert run_context.last_completed_run("a1", rows[0]) is None


def test_the_run_message_shows_lucius_the_rule_and_the_last_completed_run():
    request = AutomationRunnerRequest(
        automation_id="a1",
        run_id="run_now",
        session_id="s1",
        title="Rides",
        instructions="Tell me about the ride.",
        trigger={"kind": "event", "event": "txn"},
        configured_trigger=run_context.configured_trigger({"trigger": TRIGGER}),
        last_completed_run={"started_at": "2026-10-04T08:30:00"},
    )
    text = _automation_message(request)["content"][1]["text"]
    assert '"configured_trigger"' in text and '"pay_grab": "Grab"' in text
    assert '"last_completed_run": {"started_at": "2026-10-04T08:30:00"}' in text
