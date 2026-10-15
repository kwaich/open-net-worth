"""dividendYield unit handling — Yahoo percent vs ratio (GOOG regression)."""

from __future__ import annotations

import pytest

from backend.services.prices import _dividend_yield_ratio


def test_dividend_yield_prefers_trailing_annual_ratio():
    """GOOG-shaped: dividendYield=0.26 means 0.26%, trailing is the real ratio."""
    info = {
        "dividendYield": 0.26,
        "trailingAnnualDividendYield": 0.0024587084,
    }
    assert _dividend_yield_ratio(info) == pytest.approx(0.0024587084)


def test_dividend_yield_falls_back_to_percent_field():
    """Without trailing, dividendYield is percent (4.19 → 4.19%)."""
    assert _dividend_yield_ratio({"dividendYield": 4.19}) == pytest.approx(0.0419)
    assert _dividend_yield_ratio({"dividendYield": 0.26}) == pytest.approx(0.0026)


def test_dividend_yield_trailing_percent_edge():
    """If trailing somehow arrives as percent >1, scale it."""
    assert _dividend_yield_ratio({"trailingAnnualDividendYield": 2.5}) == pytest.approx(0.025)


def test_dividend_yield_missing():
    assert _dividend_yield_ratio({}) is None


def test_dividend_yield_zero_is_no_data():
    """VWRA.L regression: Yahoo's 0 means it has nothing, not a 0.00% payer."""
    assert _dividend_yield_ratio({"trailingAnnualDividendYield": 0, "dividendYield": 0}) is None
    assert _dividend_yield_ratio({"trailingAnnualDividendYield": 0, "yield": 0.0185}) == (
        pytest.approx(0.0185)
    )
