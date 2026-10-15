"""CRUD + idempotence for the first-run completion stamp (spec §5.5)."""

from __future__ import annotations

from backend.db.onboarding_state_store import OnboardingStateStore


def test_completed_at_is_none_on_a_fresh_instance(db_isolated):
    assert OnboardingStateStore().completed_at() is None


def test_mark_complete_stamps_and_persists(db_isolated):
    stamped = OnboardingStateStore().mark_complete()
    assert stamped
    assert OnboardingStateStore().completed_at() == stamped


def test_mark_complete_is_idempotent(db_isolated):
    store = OnboardingStateStore()
    first = store.mark_complete()
    second = store.mark_complete()
    assert first == second


def test_a_blank_stamp_is_treated_as_absent(db_isolated):
    """A row written with an empty string must not read as "set up" — otherwise a partial
    write would silently suppress first run forever."""
    store = OnboardingStateStore()
    store.save({"completed_at": ""})
    assert store.completed_at() is None
