"""Commitments feed for the Overview, with its window totals already summed.

`GET /api/commitments/summary` serves the feed the "Next 30 days" card renders, the overdue and
due-soon figures (read from `commitments.due_summary()` — the ONE definition the Commitments
masthead, its attention lane and the daily briefing read too), the recurring income expected
inside the same window, and the other bills due in it — recurring outflows and card statements
(`upcoming_bills`). "Going out" is the commitments due plus those bills: the commitments-only
figure stays `strip_total_base`, the one the Commitments masthead quotes. The coverage verdict is NOT served here: `GET /api/commitments/coverage`
is read by the Dashboard's flags and the Commitments masthead from one shared cache entry, so the
funding shortfall is one fetched object on both screens. Totals are money math, so they are
summed here, never in the browser.
"""

from __future__ import annotations

from backend.core.fx import base_currency
from backend.core.user_time import user_today
from backend.services import commitments as commitments_service
from backend.services import recurring_baseline, upcoming_bills
from backend.services.fx_cache import load_fx_cache

FEED_DAYS = 120
STRIP_DAYS = commitments_service.DUE_WINDOW_DAYS


def commitments_overview(days: int = FEED_DAYS, strip_days: int = STRIP_DAYS) -> dict:
    """The upcoming-commitments feed plus what is overdue, what goes out within the next
    `strip_days`, and what recurring income is expected in the same window."""
    base = base_currency()
    rates = load_fx_cache()
    events = commitments_service.upcoming_events(
        days=max(1, min(days, 365)), base=base, rates=rates
    )
    due = commitments_service.due_summary(days=strip_days, base=base, rates=rates)
    today = user_today()
    income = recurring_baseline.upcoming_income(strip_days, base, rates, today=today)
    bills = upcoming_bills.recurring_bills(strip_days, base, rates, today, due["due"])
    cards = upcoming_bills.card_bills(strip_days, base, rates, today)
    bills_total = _total(bills)
    cards_total = _total(cards)
    return {
        "base_currency": base,
        "events": events,
        "strip_days": strip_days,
        "strip_total_base": due["due_total_base"],
        "overdue": due["overdue"],
        "overdue_count": due["overdue_count"],
        "overdue_total_base": due["overdue_total_base"],
        "expected_income": income,
        "expected_income_total_base": _total(income),
        "bills": bills,
        "bills_total_base": bills_total,
        "card_bills": cards,
        "card_bills_total_base": cards_total,
        "going_out_total_base": round(due["due_total_base"] + bills_total + cards_total, 2),
    }


def _total(events: list[dict]) -> float:
    return round(sum(float(e["amount_base"] or 0.0) for e in events), 2)
