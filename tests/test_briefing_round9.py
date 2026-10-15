"""Daily briefing fixes from persona QA round 9 (2026-10-14), synthetic data.

* It stayed stale after a recurring stream was REMOVED: "your salary of S$5,400 arrives on
  1 November" outlived the stream's deletion. The activity fingerprint now carries the declared
  streams and the user's confirm/dismiss corrections.
* On a 50/30/20 book it wrote that the "Ready to Assign figure isn't available to me". Ready to
  Assign belongs to the zero-based method only; the context offers none there and the prompt
  must tell the model to say nothing about a figure it was not given.
"""

from __future__ import annotations

import pytest

from backend.api import briefing
from backend.db.recurring_stream_store import RecurringStreamStore
from backend.services import briefing_book_state as book_state
from backend.services import briefing_context as facts


@pytest.fixture
def book(db_isolated):
    from backend.services import accounts as accounts_service

    accounts_service.create_account(
        {"name": "Current", "type": "cash", "currency": "SGD", "initial_balance": 500.0}
    )


def _declare(**extra) -> dict:
    return RecurringStreamStore().create_declared(
        {
            "label": "Salary",
            "direction": "in",
            "amount": 5400,
            "cadence": "monthly",
            "anchor_date": "2026-11-01",
            **extra,
        }
    )


def test_declaring_a_stream_changes_the_activity_fingerprint(book):
    before = book_state.activity_fingerprint("SGD")
    _declare()
    assert book_state.activity_fingerprint("SGD") != before


def test_removing_a_declared_stream_changes_it_back(book):
    row = _declare()
    declared = book_state.activity_fingerprint("SGD")

    RecurringStreamStore().delete_declared(row["id"])

    assert book_state.activity_fingerprint("SGD") != declared


def test_editing_a_declared_streams_amount_changes_the_fingerprint(book):
    row = _declare()
    before = book_state.activity_fingerprint("SGD")

    RecurringStreamStore().update_declared(row["id"], {"amount": 6000})

    assert book_state.activity_fingerprint("SGD") != before


def test_dismissing_a_detected_stream_changes_the_fingerprint(book):
    before = book_state.activity_fingerprint("SGD")
    RecurringStreamStore().set_status("sig_abc", "dismissed")
    assert book_state.activity_fingerprint("SGD") != before


def test_an_unchanged_stream_set_leaves_the_fingerprint_alone(book):
    _declare()
    assert book_state.activity_fingerprint("SGD") == book_state.activity_fingerprint("SGD")


def test_a_proportional_book_gets_no_ready_to_assign_section(book, monkeypatch):
    monkeypatch.setattr(facts.budget_engines, "home_engine_id", lambda: "proportional")
    assert facts.cash_availability_lines("2026-10", "SGD", card_owed=0.0) == []


def test_the_prompt_forbids_narrating_a_missing_ready_to_assign():
    prompt = " ".join(briefing._SYSTEM.split())
    assert "say nothing about Ready to Assign" in prompt
    assert "Never narrate an input you were not given" in prompt
    assert "only under a zero-based budget" in prompt
