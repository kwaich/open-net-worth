"""Unit tests for sector peer aggregation — pure math + stubbed I/O."""

from __future__ import annotations

import pytest

from backend.services import sector_fundamentals as sf


@pytest.fixture(autouse=True)
def _clear_cache():
    sf.clear_sector_cache()
    yield
    sf.clear_sector_cache()


# ── aggregate ────────────────────────────────────────────────────────────────


def test_aggregate_returns_none_below_peer_floor():
    assert sf.aggregate([1.0, 2.0, 3.0, 4.0]) is None  # 4 < 5


def test_aggregate_min_mean_max():
    out = sf.aggregate([10.0, 20.0, 30.0, 40.0, 50.0])
    assert out is not None
    assert out["low"] == 10.0
    assert out["high"] == 50.0
    assert out["mean"] == 30.0
    assert out["count"] == 5


# ── position_pct ─────────────────────────────────────────────────────────────


def test_position_pct_midpoint():
    assert sf.position_pct(25.0, 0.0, 50.0) == 50.0


def test_position_pct_clamps():
    assert sf.position_pct(-10.0, 0.0, 100.0) == 0.0
    assert sf.position_pct(200.0, 0.0, 100.0) == 100.0


def test_position_pct_null_when_range_collapses():
    assert sf.position_pct(10.0, 10.0, 10.0) is None
    assert sf.position_pct(10.0, 20.0, 10.0) is None


# ── outlier / clean ──────────────────────────────────────────────────────────


def test_clean_drops_negative_pe():
    assert sf._clean("pe_ratio", -5.0) is None


def test_clean_drops_extreme_pe():
    assert sf._clean("pe_ratio", 501.0) is None
    assert sf._clean("pe_ratio", 25.0) == 25.0


def test_clean_normalizes_dividend_yield_percent():
    # Screener percent (0.47 → 0.47%) → ratio.
    assert sf._clean("dividend_yield", 0.47, from_screener=True) == pytest.approx(0.0047)
    # .info ratio stays as-is.
    assert sf._clean("dividend_yield", 0.0047) == pytest.approx(0.0047)
    # .info percent form >1 → ratio.
    assert sf._clean("dividend_yield", 2.5) == pytest.approx(0.025)


def test_clean_drops_out_of_bounds_margin():
    assert sf._clean("profit_margin", 1.5) is None
    assert sf._clean("profit_margin", 0.22) == 0.22


# ── build_fund_row ───────────────────────────────────────────────────────────


def test_build_fund_row_with_sector():
    sector = {"pe_ratio": {"low": 10.0, "mean": 20.0, "high": 40.0, "count": 80}}
    row = sf.build_fund_row("pe_ratio", "P/E", "help", 25.0, "USD", sector)
    assert row["ticker"] == 25.0
    assert row["ticker_display"] == "25.00"
    assert row["higher_is_better"] is False
    assert row["sector_low"] == 10.0
    assert row["sector_mean"] == 20.0
    assert row["sector_high"] == 40.0
    assert row["position_pct"] == 50.0
    assert row["peer_count"] == 80


def test_higher_is_better_polarity():
    assert sf.higher_is_better("pe_ratio") is False
    assert sf.higher_is_better("debt_to_equity") is False
    assert sf.higher_is_better("dividend_yield") is True
    assert sf.higher_is_better("return_on_equity") is True


def test_build_fund_row_without_sector():
    row = sf.build_fund_row("pe_ratio", "P/E", "help", 25.0, "USD", {})
    assert row["ticker"] == 25.0
    assert row["sector_low"] is None
    assert row["position_pct"] is None


def test_build_fund_rows_skips_missing_metrics():
    stats = {"pe_ratio": 28.4, "eps": 3.5}
    rows = sf.build_fund_rows(sf.VALUATION_SPECS, stats, "USD", None)
    ids = [r["id"] for r in rows]
    assert ids == ["pe_ratio"]
    growth = sf.build_fund_rows(sf.GROWTH_SPECS, stats, "USD", None)
    assert [r["id"] for r in growth] == ["eps"]
    assert growth[0]["ticker_display"].startswith("$")


# ── sector_fundamentals_for (stubbed I/O) ─────────────────────────────────────


def test_sector_fundamentals_for_aggregates_tier1(monkeypatch):
    quotes = [
        {"symbol": f"T{i}", "trailingPE": 10.0 + i, "forwardPE": 8.0 + i, "priceToBook": 2.0}
        for i in range(10)
    ]
    monkeypatch.setattr(sf, "fetch_sector_peers", lambda *_a, **_k: quotes)
    monkeypatch.setattr(sf, "_tier2_from_peers", lambda *_a, **_k: {})
    out = sf.sector_fundamentals_for("Technology")
    assert "pe_ratio" in out
    assert out["pe_ratio"]["low"] == 10.0
    assert out["pe_ratio"]["high"] == 19.0
    assert out["pe_ratio"]["count"] == 10
    # Second call hits cache — same object identity via cache return
    out2 = sf.sector_fundamentals_for("Technology")
    assert out2 == out


def test_sector_fundamentals_for_empty_sector():
    assert sf.sector_fundamentals_for("") == {}
    assert sf.sector_fundamentals_for("   ") == {}


def test_sector_fundamentals_for_insufficient_peers(monkeypatch):
    quotes = [{"symbol": "A", "trailingPE": 10.0}] * 3  # below floor
    monkeypatch.setattr(sf, "fetch_sector_peers", lambda *_a, **_k: quotes)
    monkeypatch.setattr(sf, "_tier2_from_peers", lambda *_a, **_k: {})
    out = sf.sector_fundamentals_for("Energy")
    assert "pe_ratio" not in out


def test_resolve_sector_from_instrument(monkeypatch):
    assert sf.resolve_sector({"sector": "Technology"}) == "Technology"
    monkeypatch.setattr(
        "backend.services.prices._fetch_sector",
        lambda *_a, **_k: "Healthcare",
    )
    assert sf.resolve_sector({"sector": ""}, symbol="JNJ") == "Healthcare"
    assert sf.resolve_sector(None, symbol=None) is None
