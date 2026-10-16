"""The Next-30-days feed carries the recurring income landing in the window.

Persona QA round 5 (2026-10-14, synthetic reproduction): a salary landing on the 25th, eleven
days away, never appeared; the card said "Nothing due". Recurring income is now listed beside
what goes out, from the same detected streams the cash-flow projection walks.
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from backend.core.user_time import user_today
from backend.services import commitments_summary, recurring_baseline


def _day(offset: int) -> str:
    return (user_today() + timedelta(days=offset)).isoformat()


def _stream(direction: str, label: str, amount: float, anchor: str, cadence: str = "monthly"):
    return {
        "direction": direction,
        "label": label,
        "cadence": cadence,
        "amount_base": amount,
        "anchor_date": anchor,
    }


@pytest.fixture
def streams(monkeypatch):
    """The detected streams the baseline reports. Detection itself is covered by
    test_recurring_detection; this feed only walks what it is handed."""
    found: list[dict] = []
    monkeypatch.setattr(recurring_baseline, "resolve_baseline", lambda *a, **k: {"streams": found})
    return found


def test_a_salary_stream_lands_in_the_window(db_isolated, fx_rates, streams):
    streams += [_stream("in", "Salary", 6_000.0, _day(11)), _stream("out", "Gym", 80.0, _day(5))]

    overview = commitments_summary.commitments_overview()

    assert [(e["date"], e["label"], e["amount_base"]) for e in overview["expected_income"]] == [
        (_day(11), "Salary", 6_000.0)
    ]
    assert overview["expected_income_total_base"] == 6_000.0


def test_a_stream_anchored_in_the_past_lands_on_its_next_occurrence(db_isolated, fx_rates, streams):
    streams.append(_stream("in", "Rent received", 1_500.0, _day(-20), cadence="weekly"))

    income = commitments_summary.commitments_overview()["expected_income"]

    dates = [e["date"] for e in income]
    assert dates == [_day(-20 + 7 * n) for n in range(3, 8) if 0 < -20 + 7 * n <= 30]
    assert all(e["amount_base"] == 1_500.0 for e in income)


def test_a_stream_beyond_the_window_is_not_listed(db_isolated, fx_rates, streams):
    streams.append(_stream("in", "Annual bonus", 20_000.0, _day(45), cadence="annual"))

    assert commitments_summary.commitments_overview()["expected_income"] == []
