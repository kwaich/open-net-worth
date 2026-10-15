"""
MATH-19 regression: the `plan-runway` widget's plotted trajectory must cross zero on the
SAME month as the headline `runway_months`, which compounds `expense_growth_pct`
(`cashflow._runway_with_growth`). The trajectory used to be a flat `cash - burn*i` line, so
cash 50,000 / burn 2,000 (3%/yr growth) showed a headline of 24.3 months but the chart still
had $2,000 left at month 24 and crossed zero at month 25.
"""

from __future__ import annotations

import math

import pytest

from backend.services import cashflow as cashflow_service
from backend.services import cashflow_projections


def _snapshot(cash: float, burn: float, growth_pct: float, months) -> dict:
    return {
        "liquid_balance_base": cash,
        "monthly_expenses_avg": burn,
        "income_runway_months": months,
        "expense_growth_pct": growth_pct,
    }


def test_runway_trajectory_zero_crossing_matches_headline_with_growth(monkeypatch):
    headline = cashflow_service._runway_with_growth(50_000.0, 2_000.0, 3.0)
    assert headline == 24.3  # sanity: matches the finding's own numbers

    monkeypatch.setattr(
        cashflow_projections,
        "_cashflow_snapshot",
        lambda: _snapshot(50_000.0, 2_000.0, 3.0, headline),
    )
    trajectory = cashflow_projections.runway()["trajectory"]

    # Same growth compounding as the headline: month 24 (floor(24.3)) still has cash left,
    # but nowhere near the flat line's naive $2,000 — the bug this regresses.
    assert trajectory[24] == pytest.approx(594.36, abs=0.02)
    assert trajectory[24] != pytest.approx(2_000.0, abs=0.02)

    first_non_positive = next(i for i, v in enumerate(trajectory) if v <= 0)
    assert first_non_positive == math.ceil(headline)


def test_runway_trajectory_is_flat_with_zero_growth(monkeypatch):
    monkeypatch.setattr(
        cashflow_projections,
        "_cashflow_snapshot",
        lambda: _snapshot(50_000.0, 2_000.0, 0.0, 25.0),
    )
    trajectory = cashflow_projections.runway()["trajectory"]
    expected = [round(max(0.0, 50_000.0 - 2_000.0 * i), 2) for i in range(len(trajectory))]
    assert trajectory == expected


def test_runway_months_never_negative_with_overdrawn_cash(monkeypatch):
    """An already-overdrawn liquid balance used to produce a NEGATIVE `income_runway_months`
    (`round(balance / monthly_expenses, 1)` with no floor), which sent `runway()`'s `horizon`
    negative and collapsed the plotted trajectory to a single point — no curve to draw."""
    headline = cashflow_service._runway_with_growth(-100.0, 50.0, 0.0)
    assert headline == 0.0  # exhausted, not negative

    monkeypatch.setattr(
        cashflow_projections,
        "_cashflow_snapshot",
        lambda: _snapshot(-100.0, 50.0, 0.0, headline),
    )
    result = cashflow_projections.runway()
    assert result["runway_months"] == 0.0
    trajectory = result["trajectory"]
    assert len(trajectory) >= 2
    assert trajectory[0] == pytest.approx(-100.0)


def test_runway_months_never_negative_with_zero_cash(monkeypatch):
    """Same shape as the overdrawn case, at the exact boundary: zero liquid cash with positive
    burn is also an exhausted runway (0 months), not a negative one."""
    headline = cashflow_service._runway_with_growth(0.0, 50.0, 0.0)
    assert headline == 0.0

    monkeypatch.setattr(
        cashflow_projections,
        "_cashflow_snapshot",
        lambda: _snapshot(0.0, 50.0, 0.0, headline),
    )
    result = cashflow_projections.runway()
    assert result["runway_months"] == 0.0
    trajectory = result["trajectory"]
    assert len(trajectory) >= 2
    assert trajectory[0] == pytest.approx(0.0)


def test_runway_months_unchanged_for_positive_balance():
    """Regression guard: the balance <= 0 clamp must not touch the ordinary positive-balance
    path — same headline as before the fix."""
    assert cashflow_service._runway_with_growth(50_000.0, 2_000.0, 3.0) == 24.3
    assert cashflow_service._runway_with_growth(50_000.0, 2_000.0, 0.0) == 25.0
