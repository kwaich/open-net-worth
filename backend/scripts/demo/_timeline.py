"""Deterministic clock + RNG for the demo book.

Every date in the demo is RELATIVE to the day the book is built, so a hosted demo
never drifts into looking abandoned: the newest transaction is always "yesterday",
the vesting ladder always has a next tranche, and the budget month is always the
current one. The anchor is passed in (never read from ``date.today()`` deep inside a
generator) so a build is reproducible from ``(anchor, seed)`` alone.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import date, timedelta

# Months of history the book carries. 30 gives every chart (net worth, cashflow,
# spending-by-month, portfolio) more than two full years to draw, which is what makes
# the demo read as a real book rather than a fixture.
HISTORY_MONTHS = 30

# One fixed seed => one fixed book. Bump it only to deliberately reshuffle the noise.
SEED = 20260823


def add_months(d: date, n: int) -> date:
    """Shift by whole months, clamping the day into the target month (Jan 31 + 1mo = Feb 28)."""
    total = (d.year * 12 + d.month - 1) + n
    year, month = divmod(total, 12)
    month += 1
    day = min(d.day, _days_in_month(year, month))
    return date(year, month, day)


def _days_in_month(year: int, month: int) -> int:
    nxt = date(year + (month == 12), (month % 12) + 1, 1)
    return (nxt - date(year, month, 1)).days


def month_key(d: date) -> str:
    return f"{d.year:04d}-{d.month:02d}"


def last_business_day(year: int, month: int) -> date:
    """Last weekday of the month — payroll lands on a business day, not a Sunday."""
    d = date(year, month, _days_in_month(year, month))
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d


def on_business_day(d: date) -> date:
    """Nudge a weekend date back to Friday (bank-initiated debits behave this way)."""
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d


@dataclass(frozen=True)
class Timeline:
    """The demo's clock: ``start`` (first day of the oldest month) .. ``today``."""

    today: date
    start: date

    @classmethod
    def anchored_on(cls, today: date, months: int = HISTORY_MONTHS) -> "Timeline":
        return cls(today=today, start=add_months(today.replace(day=1), -(months - 1)))

    @property
    def months(self) -> list[date]:
        """First-of-month dates from ``start`` through the current month, inclusive."""
        out, cur = [], self.start
        while cur <= self.today:
            out.append(cur)
            cur = add_months(cur, 1)
        return out

    def clamp(self, d: date) -> date | None:
        """Drop a generated date that would land in the future (the current month is partial)."""
        return None if d > self.today or d < self.start else d

    def days_from_start(self, d: date) -> int:
        return (d - self.start).days

    @property
    def total_days(self) -> int:
        return (self.today - self.start).days


def rng(stream: str) -> random.Random:
    """A private, reproducible RNG per generator stream.

    Per-stream rather than one shared generator so adding (say) a new subscription does
    not reshuffle every grocery amount in the book — each stream draws from its own
    sequence, keyed by name.
    """
    return random.Random(f"{SEED}:{stream}")
