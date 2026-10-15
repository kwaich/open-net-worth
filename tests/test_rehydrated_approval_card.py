"""Rebuilding the approval card when a conversation is reopened.

A gated tool_use is left result-less in the transcript until the human answers (RFC
2026-07-22), and confirm cards were never persisted — so reopening a parked Automation run
showed the tool pills, no card, and no way to answer it. The run could only be resumed by
the tab that happened to be open when it stalled.

But "no result" alone does NOT mean "waiting for you": a call whose process died mid-flight
looks identical. Live, a run killed by a server restart came back offering Allow/Deny for a
turn that no longer existed. So a card is rebuilt only when a gate says the call is really
parked — and there are two gates, each with its own record.
"""

from __future__ import annotations

import pytest

from backend.ai import assistant

SESSION = "automation-run-run_test"

_TRANSCRIPT = [
    {"role": "user", "content": "do the thing"},
    {
        "role": "assistant",
        "content": [
            {"type": "text", "text": "Sending the summary."},
            {
                "type": "tool_use",
                "id": "call_1",
                "name": "telegram_send_message",
                "input": {"message": "hi"},
            },
        ],
    },
]


@pytest.fixture
def transcript(monkeypatch):
    monkeypatch.setattr(assistant, "_load_session", lambda _sid: [dict(m) for m in _TRANSCRIPT])
    monkeypatch.setattr(assistant, "_read_index", lambda: [])
    monkeypatch.setattr(assistant, "_rehydrated_turn_usage", lambda *a, **k: {})


@pytest.fixture
def no_staged_plan(monkeypatch):
    class _Store:
        def get_pending(self, _tool_use_id):
            return None

    monkeypatch.setattr("backend.db.pending_plan_store.PendingPlanStore", lambda: _Store())


@pytest.fixture
def not_an_automation(monkeypatch):
    monkeypatch.setattr(
        "backend.services.automations.runs.automation_run_for_session", lambda _sid: None
    )


def _card(messages):
    return (messages[-1].get("proposedChanges") or [None])[0]


def test_a_call_staged_in_the_pending_store_rebuilds_its_card(
    transcript, not_an_automation, monkeypatch
):
    class _Store:
        def get_pending(self, tool_use_id):
            return {"state": "awaiting_approval"} if tool_use_id == "call_1" else None

    monkeypatch.setattr("backend.db.pending_plan_store.PendingPlanStore", lambda: _Store())
    card = _card(assistant.rehydrate_messages(SESSION))

    assert card["grant_tool"] == "telegram_send_message"
    assert card["tool_use_id"] == "call_1"
    assert card["display"]["arguments"] == {"message": "hi"}


def test_a_parked_automation_run_rebuilds_its_card(transcript, no_staged_plan, monkeypatch):
    """An Automation's ids never reach the pending store — the run row is its record, and
    requiring a staged entry hid the card on exactly the runs that needed one."""
    monkeypatch.setattr(
        "backend.services.automations.runs.automation_run_for_session",
        lambda _sid: ({}, {"status": "waiting", "pending_tool_ids": ["call_1"]}),
    )
    assert _card(assistant.rehydrate_messages(SESSION))["tool_use_id"] == "call_1"


def test_the_message_is_marked_awaiting_so_the_bubble_reads_as_unfinished(
    transcript, no_staged_plan, monkeypatch
):
    monkeypatch.setattr(
        "backend.services.automations.runs.automation_run_for_session",
        lambda _sid: ({}, {"status": "waiting", "pending_tool_ids": ["call_1"]}),
    )
    assert assistant.rehydrate_messages(SESSION)[-1]["awaitingApproval"] is True


def test_a_run_killed_mid_call_does_NOT_offer_to_approve_anything(
    transcript, no_staged_plan, monkeypatch
):
    """The regression case. A server restart leaves a result-less tool_use behind, identical in the
    transcript to a paused one — and the card offered to approve a turn that was gone."""
    monkeypatch.setattr(
        "backend.services.automations.runs.automation_run_for_session",
        lambda _sid: ({}, {"status": "failed", "pending_tool_ids": []}),
    )
    assert _card(assistant.rehydrate_messages(SESSION)) is None


def test_a_finished_run_offers_nothing(transcript, no_staged_plan, monkeypatch):
    monkeypatch.setattr(
        "backend.services.automations.runs.automation_run_for_session",
        lambda _sid: ({}, {"status": "completed", "pending_tool_ids": []}),
    )
    assert _card(assistant.rehydrate_messages(SESSION)) is None


def test_a_waiting_run_waiting_on_a_DIFFERENT_call_does_not_claim_this_one(
    transcript, no_staged_plan, monkeypatch
):
    monkeypatch.setattr(
        "backend.services.automations.runs.automation_run_for_session",
        lambda _sid: ({}, {"status": "waiting", "pending_tool_ids": ["some_other_id"]}),
    )
    assert _card(assistant.rehydrate_messages(SESSION)) is None


def test_an_ordinary_conversation_is_untouched(transcript, no_staged_plan, not_an_automation):
    assert _card(assistant.rehydrate_messages(SESSION)) is None
    assert "awaitingApproval" not in assistant.rehydrate_messages(SESSION)[-1]


def test_a_completed_call_is_never_a_question(monkeypatch, no_staged_plan, not_an_automation):
    """A tool_use WITH its result is answered — the gate check should not even be reached."""
    monkeypatch.setattr(
        assistant,
        "_load_session",
        lambda _sid: [
            *_TRANSCRIPT,
            {
                "role": "user",
                "content": [{"type": "tool_result", "tool_use_id": "call_1", "content": "sent"}],
            },
        ],
    )
    monkeypatch.setattr(assistant, "_read_index", lambda: [])
    monkeypatch.setattr(assistant, "_rehydrated_turn_usage", lambda *a, **k: {})

    assert _card(assistant.rehydrate_messages(SESSION)) is None


def test_a_broken_gate_lookup_fails_safe_with_no_card(transcript, monkeypatch):
    """Better a re-ask than an Allow button wired to nothing."""

    def _boom():
        raise RuntimeError("store unavailable")

    monkeypatch.setattr("backend.db.pending_plan_store.PendingPlanStore", _boom)
    monkeypatch.setattr("backend.services.automations.runs.automation_run_for_session", _boom)

    assert _card(assistant.rehydrate_messages(SESSION)) is None


# ── The run header ────────────────────────────────────────────────────────────
# The opening message renders as "Ran <name>" linking to the run. The ids come from the
# server; the panel must never parse them out of the session id.


def test_the_opening_message_carries_the_automation_name_and_ids(transcript, monkeypatch):
    # rehydrate_messages resolves the automation brief via _session_meta_entry (a direct
    # keyed lookup), not _read_index (a full-listing scan) — see assistant.py's own
    # automation_brief block.
    monkeypatch.setattr(
        assistant,
        "_session_meta_entry",
        lambda _sid: {"id": SESSION, "source": "automation", "title": "Log transactions"},
    )
    monkeypatch.setattr(
        "backend.services.automations.runs.automation_run_for_session",
        lambda _sid: ({"id": "task-0fae2fb5"}, {"id": "run_a440", "status": "completed"}),
    )

    assert assistant.rehydrate_messages(SESSION)[0]["automationBrief"] == {
        "name": "Log transactions",
        "automationId": "task-0fae2fb5",
        "runId": "run_a440",
    }


def test_an_unresolvable_run_still_gets_a_header_just_not_a_link(transcript, monkeypatch):
    """Best-effort: losing the link is a smaller loss than losing the header."""
    monkeypatch.setattr(
        assistant,
        "_session_meta_entry",
        lambda _sid: {"id": SESSION, "source": "automation", "title": "Log transactions"},
    )
    monkeypatch.setattr(
        "backend.services.automations.runs.automation_run_for_session", lambda _sid: None
    )

    assert assistant.rehydrate_messages(SESSION)[0]["automationBrief"] == {
        "name": "Log transactions"
    }


def test_an_ordinary_conversation_gets_no_header(transcript, monkeypatch):
    monkeypatch.setattr(assistant, "_read_index", lambda: [{"id": SESSION, "source": "chat"}])

    assert "automationBrief" not in assistant.rehydrate_messages(SESSION)[0]
