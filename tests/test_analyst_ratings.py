"""Unit tests for prices.fetch_analyst_ratings — Yahoo per-firm ratings + staleness filter.

Hermetic: yfinance is replaced by a fake whose .upgrades_downgrades returns a real pandas
DataFrame (the same shape Yahoo returns), so no network. Dates are relative to now() so the
staleness assertion never becomes a time-bomb.
"""

from __future__ import annotations

import types
from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest

import backend.services.prices as prices


def _frame(rows: list[tuple]) -> pd.DataFrame:
    idx = pd.DatetimeIndex([r[0] for r in rows], name="GradeDate")
    return pd.DataFrame(
        {
            "Firm": [r[1] for r in rows],
            "ToGrade": [r[2] for r in rows],
            "FromGrade": ["" for _ in rows],
            "Action": ["main" for _ in rows],
            "priceTargetAction": [r[3] for r in rows],
            "currentPriceTarget": [r[4] for r in rows],
            "priorPriceTarget": [r[5] for r in rows],
        },
        index=idx,
    )


@pytest.fixture
def fake_yf(monkeypatch):
    frames: dict = {}

    class _T:
        def __init__(self, sym):
            self.sym = sym

        @property
        def upgrades_downgrades(self):
            return frames.get(self.sym)

    monkeypatch.setattr(prices, "yf", types.SimpleNamespace(Ticker=_T))
    prices._ratings_cache.clear()
    return frames


def test_recent_kept_stale_dropped_and_sorted(fake_yf):
    now = datetime.now(timezone.utc)
    fake_yf["UBER"] = _frame(
        [
            (now - timedelta(days=60), "JP Morgan", "Overweight", "Raises", 110.0, 105.0),
            (now - timedelta(days=5), "Tigress", "Buy", "Raises", 115.0, 110.0),
            (
                now - timedelta(days=365 * 3),
                "OldCo",
                "Buy",
                "Maintains",
                50.0,
                50.0,
            ),  # stale → drop
        ]
    )
    rows = prices.fetch_analyst_ratings("UBER")
    # Stale row dropped; remaining most-recent-first.
    assert [r["firm"] for r in rows] == ["Tigress", "JP Morgan"]
    assert rows[0]["target"] == 115.0 and rows[0]["prior_target"] == 110.0


def test_zero_and_nan_targets_blanked(fake_yf):
    now = datetime.now(timezone.utc)
    fake_yf["X"] = _frame(
        [(now - timedelta(days=5), "Bernstein", "Outperform", "Maintains", 0.0, float("nan"))]
    )
    rows = prices.fetch_analyst_ratings("X")
    assert len(rows) == 1
    assert rows[0]["target"] is None and rows[0]["prior_target"] is None
    assert rows[0]["firm"] == "Bernstein" and rows[0]["rating"] == "Outperform"


def test_all_stale_yields_empty(fake_yf):
    # The ONWAI.DE case: a single 5-year-old row → [] so the frontend hides the table.
    now = datetime.now(timezone.utc)
    fake_yf["ONWAI.DE"] = _frame(
        [(now - timedelta(days=365 * 5), "Bernstein", "Outperform", "Maintains", 0.0, 0.0)]
    )
    assert prices.fetch_analyst_ratings("ONWAI.DE") == []


def test_missing_or_empty_symbol(fake_yf):
    assert prices.fetch_analyst_ratings("MISSING") == []  # frames.get → None
    assert prices.fetch_analyst_ratings("") == []
