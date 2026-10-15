"""Daily briefing fixes from persona QA round 8 (2026-10-14), synthetic data.

The owner's salary due on the 28th was missing: the context listed only what goes OUT.
"""

from __future__ import annotations

from datetime import date

import pytest

import backend.api.briefing as brf
from backend.services import briefing_context as facts
from backend.services import commitments as commitments_service
from backend.services import recurring_baseline

# ── incoming income ───────────────────────────────────────────────────────────


@pytest.fixture
def no_outgoings(monkeypatch):
    empty = {
        "overdue": [],
        "due": [],
        "overdue_total_base": 0.0,
        "due_total_base": 0.0,
        "funding_overdue": [],
    }
    monkeypatch.setattr(commitments_service, "due_summary", lambda **_: empty)
    monkeypatch.setattr(facts, "read_actions", lambda: [])


def test_the_note_is_told_about_salary_that_lands_in_the_window(no_outgoings, monkeypatch):
    income = [{"date": "2026-10-28", "label": "Salary", "cadence": "monthly", "amount_base": 21000}]
    monkeypatch.setattr(recurring_baseline, "upcoming_income", lambda *a, **k: income)

    lines = facts.upcoming_lines(date(2026, 10, 14), "SGD", {"SGD": 1.0})

    assert lines == [
        "  INCOMING IN THE NEXT 30 DAYS (1, S$21,000 coming in):",
        "  [2026-10-28, 14d] Salary: S$21,000",
    ]


def test_no_income_in_the_window_adds_no_section(no_outgoings, monkeypatch):
    monkeypatch.setattr(recurring_baseline, "upcoming_income", lambda *a, **k: [])
    assert facts.upcoming_lines(date(2026, 10, 14), "SGD", {"SGD": 1.0}) == []


def test_an_unavailable_income_forecast_does_not_hide_the_outgoings(no_outgoings, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("no baseline")

    monkeypatch.setattr(recurring_baseline, "upcoming_income", boom)
    assert facts.upcoming_lines(date(2026, 10, 14), "SGD", {"SGD": 1.0}) == []


def test_the_system_prompt_asks_for_incoming_money_by_date():
    assert "INCOMING in the UPCOMING section" in brf._SYSTEM
    assert "Never advise taking on or keeping up debt" in brf._SYSTEM
