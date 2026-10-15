"""
Regression test for MATH-24 (docs/review/money-math-audit.md#math-24): the
``get_spending_summary`` AI tool must read the current HOME budget-lens engine
(``budget_lens.home_spending_summary``), the same function ``reports.py`` and the daily
briefing use, not the retired per-category-limit engine (``budgets.spending_summary``) —
RFC 2026-08-22-pluggable-budget-engines.md §8.1. Before this fix Lucius alone still read the
retired engine, so on a non-default budget engine its "how's my spending?" answer could
disagree with the Overview/Reports for the same month.

Run:
    .venv/bin/python3 -m pytest tests/test_get_spending_summary_tool.py -q
"""

from __future__ import annotations

import asyncio
import json

from backend.ai.tools.ledger import get_spending_summary
from backend.services import budget_lens, budgets


def _call(month=6, year=2026):
    text, proposed = asyncio.run(get_spending_summary({"month": month, "year": year}))
    return json.loads(text), proposed


def test_get_spending_summary_reads_home_budget_lens_not_legacy_engine(monkeypatch):
    lens_result = {"total_spent": 222.0, "source": "lens"}
    legacy_result = {"total_spent": 111.0, "source": "legacy"}
    lens_calls: list[tuple] = []
    legacy_calls: list[tuple] = []

    def fake_lens(*, month=None, year=None, base=None, txns=None, rates=None):
        lens_calls.append((month, year))
        return lens_result

    def fake_legacy(month=None, year=None, *, txns=None, base=None, rates=None):
        legacy_calls.append((month, year))
        return legacy_result

    monkeypatch.setattr(budget_lens, "home_spending_summary", fake_lens)
    monkeypatch.setattr(budgets, "spending_summary", fake_legacy)

    result, proposed = _call(month=6, year=2026)

    assert result == lens_result, "tool must return the HOME budget-lens summary, unchanged"
    assert lens_calls == [(6, 2026)], "home_spending_summary must be called with month/year"
    assert legacy_calls == [], "the retired budgets.spending_summary must not be called"
    assert proposed is None
