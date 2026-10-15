"""CRUD + idempotence for the Plan -> Budget introduction dismissal flag. Mirrors
tests/db/test_onboarding_state_store.py's shape — the same namespaced-singleton pattern,
one namespace row on runtime_state."""

from __future__ import annotations

from backend.db.budget_lens_intro_store import BudgetLensIntroStore


def test_dismissed_is_false_on_a_fresh_instance(db_isolated):
    assert BudgetLensIntroStore().dismissed() is False


def test_dismiss_persists(db_isolated):
    assert BudgetLensIntroStore().dismiss() is True
    assert BudgetLensIntroStore().dismissed() is True


def test_dismiss_is_idempotent(db_isolated):
    store = BudgetLensIntroStore()
    store.dismiss()
    store.dismiss()
    assert store.dismissed() is True


def test_dismissal_is_visible_to_a_new_store_instance(db_isolated):
    """Per-book, not per-instance state — a fresh ``BudgetLensIntroStore()`` (e.g. a
    different request) reads the same persisted flag."""
    BudgetLensIntroStore().dismiss()
    assert BudgetLensIntroStore().dismissed() is True
