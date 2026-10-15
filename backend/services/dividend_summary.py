"""Dividend/income Overview figures: received year-to-date and the 12-month forward calendar.

`GET /api/income`'s own summary exposes trailing-12m and all-time, never year-to-date, so the
widget used to pull the whole event list and sum this calendar year's dividends in the browser.
`GET /api/income/calendar` likewise returns individual payments, which the forecast widget
bucketed into months itself. Both folds run here now.
"""

from __future__ import annotations

from backend.core.fx import base_currency
from backend.core.user_time import user_today
from backend.services import dividend_calendar as dividend_calendar_service
from backend.services import income as income_service

FORECAST_MONTHS = 12
UPCOMING_DAYS = 90
_FORECAST_DAYS = 366


def _month_key(iso_date: str) -> str:
    return iso_date[:7]


def _forward_months(count: int) -> list[str]:
    today = user_today()
    anchor = today.year * 12 + (today.month - 1)
    return [f"{(anchor + i) // 12}-{(anchor + i) % 12 + 1:02d}" for i in range(count)]


def _dividends(payments: list[dict]) -> list[dict]:
    return [p for p in payments if p.get("kind") == "dividend"]


def income_aggregates() -> dict:
    """Dividends received YTD, the next 90 days of payments, and the 12-month forecast."""
    base = base_currency()
    year_prefix = f"{user_today().year}-"

    summary = income_service.income_summary()
    ytd = sum(
        float(e.get("base_amount") or 0.0)
        for e in (summary.get("events") or [])
        if e.get("income_kind") == "dividend"
        and str(e.get("paid_at") or "").startswith(year_prefix)
    )

    upcoming = _dividends(
        (dividend_calendar_service.upcoming_payments(days=UPCOMING_DAYS)).get("payments") or []
    )
    upcoming.sort(key=lambda p: p.get("pay_date") or "")

    forecast_payments = _dividends(
        (dividend_calendar_service.upcoming_payments(days=_FORECAST_DAYS)).get("payments") or []
    )
    months = _forward_months(FORECAST_MONTHS)
    buckets = {ym: 0.0 for ym in months}
    for payment in forecast_payments:
        ym = _month_key(str(payment.get("pay_date") or ""))
        if ym in buckets:
            buckets[ym] += float(payment.get("amount_base") or 0.0)

    forecast = [{"month": ym, "amount": round(buckets[ym], 2)} for ym in months]
    return {
        "base_currency": base,
        "received_ytd": round(ytd, 2),
        "upcoming": upcoming,
        "forecast": forecast,
        "forecast_total": round(sum(b["amount"] for b in forecast), 2),
    }
