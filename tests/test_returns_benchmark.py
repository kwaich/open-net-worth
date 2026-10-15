"""
Unit tests for the benchmark-comparison layer of services.returns (P2-INV4).

A benchmark index has no cash flows, so its return is a pure price/TWR return read from its
price_history series over the SAME window the portfolio used. These tests seed an isolated
InstrumentStore + PriceHistoryStore in tmp_path (no live data / network) and pin:
  * benchmark_price_series anchors to the window open/close (like value_series),
  * benchmark_return = end/start - 1, annualized for >= 1Y, against hand values,
  * the insufficient-history null gate (no fabrication),
  * resolve by id AND by symbol,
  * portfolio_vs_benchmark excess = portfolio_twr - benchmark_twr (null unless both sufficient).

Run:
    .venv/bin/python3 -m pytest tests/test_returns_benchmark.py -q
"""

from __future__ import annotations

from datetime import date

import pytest

from backend.services import returns as r

_TODAY = date(2026, 6, 5)


@pytest.fixture
def isolated(tmp_path, monkeypatch, db_isolated):
    """InstrumentStore and PriceHistoryStore both resolve against the per-test isolated
    database (db_isolated) — both are DB-backed (plan 2, tasks 3 and 5). Returns a
    helper to seed instruments + price series."""

    def seed(instruments, price_history):
        from sqlalchemy import delete

        from backend.db.engine import get_engine
        from backend.db.instrument_store import InstrumentStore
        from backend.db.price_history_io import import_price_history
        from backend.db.tables import price_points

        InstrumentStore().replace_all(instruments)  # full replace, like the old file write
        with get_engine().begin() as conn:
            conn.execute(delete(price_points))  # full replace, like the old file write
            import_price_history(conn, price_history)

    return seed


def _spy(extra=None):
    inst = {
        "id": "I-SPY",
        "symbol": "SPY",
        "name": "SPDR S&P 500",
        "price_source": "yahoo",
        "price_source_id": "SPY",
    }
    if extra:
        inst.update(extra)
    return inst


# ── benchmark_price_series anchoring ───────────────────────────────────────────────


def test_benchmark_price_series_anchors_window(isolated):
    isolated(
        [_spy()],
        {
            "SPY": [
                {"date": "2025-01-31", "price": 90.0},  # pre-window → carried to start
                {"date": "2025-07-15", "price": 100.0},  # in window
                {"date": "2025-12-31", "price": 120.0},  # last through end
            ]
        },
    )
    series = r.benchmark_price_series("SPY", "2025-06-01", "2025-12-31")
    assert [p["date"] for p in series] == ["2025-06-01", "2025-07-15", "2025-12-31"]
    assert series[0]["price"] == pytest.approx(90.0)  # carried forward to the open
    assert series[-1]["price"] == pytest.approx(120.0)  # end anchor


def test_benchmark_price_series_empty_when_no_points(isolated):
    isolated([_spy()], {"SPY": []})
    assert r.benchmark_price_series("SPY", "2025-06-01", "2025-12-31") == []


# ── benchmark_return ───────────────────────────────────────────────────────────────


def test_benchmark_return_simple_period_return(isolated):
    # 1M window: 100 → 110 → +10% period return (a flow-free price return).
    isolated(
        [_spy()],
        {
            "SPY": [
                {"date": "2026-05-05", "price": 100.0},
                {"date": "2026-06-05", "price": 110.0},
            ]
        },
    )
    out = r.benchmark_return("I-SPY", "1M", today=_TODAY)
    assert out["symbol"] == "SPY"
    assert out["instrument_id"] == "I-SPY"
    assert out["data_quality"]["sufficient"] is True
    assert out["twr"]["period_return"] == pytest.approx(0.10)
    # 1M does NOT annualize → annualized == period_return.
    assert out["twr"]["annualized"] == pytest.approx(0.10)


def test_benchmark_return_annualizes_over_1y(isolated):
    # A 1Y window (365 days) with 100 → 110 → +10% period, annualized ≈ +10% as well.
    isolated(
        [_spy()],
        {
            "SPY": [
                {"date": "2025-06-05", "price": 100.0},
                {"date": "2026-06-05", "price": 110.0},
            ]
        },
    )
    out = r.benchmark_return("I-SPY", "1Y", today=_TODAY)
    assert out["twr"]["period_return"] == pytest.approx(0.10)
    assert out["twr"]["annualized"] == pytest.approx(0.10, abs=1e-3)


def test_benchmark_return_resolves_by_symbol(isolated):
    isolated(
        [_spy()],
        {
            "SPY": [
                {"date": "2026-05-05", "price": 100.0},
                {"date": "2026-06-05", "price": 125.0},
            ]
        },
    )
    out = r.benchmark_return("spy", "1M", today=_TODAY)  # case-insensitive symbol
    assert out["instrument_id"] == "I-SPY"
    assert out["twr"]["period_return"] == pytest.approx(0.25)


def test_benchmark_return_unresolvable_ref_is_none(isolated):
    isolated([_spy()], {"SPY": [{"date": "2026-06-05", "price": 100.0}]})
    assert r.benchmark_return("NOPE", "1M", today=_TODAY) is None
    assert r.benchmark_return("", "1M", today=_TODAY) is None


def test_benchmark_return_insufficient_history_nulls_twr(isolated):
    # Only one point in a window whose start predates market_since → not sufficient, twr null.
    isolated([_spy()], {"SPY": [{"date": "2026-06-04", "price": 100.0}]})
    out = r.benchmark_return("I-SPY", "1Y", today=_TODAY)
    # 1Y starts 2025-06-05; earliest is 2026-06-04 > start → unanchored → insufficient.
    assert out["data_quality"]["sufficient"] is False
    assert out["twr"] is None  # never a fabricated number


def test_benchmark_return_no_price_source_id_nulls(isolated):
    isolated([_spy({"price_source_id": ""})], {})
    out = r.benchmark_return("I-SPY", "1M", today=_TODAY)
    assert out["twr"] is None
    assert out["data_quality"]["sufficient"] is False


# ── portfolio_vs_benchmark ─────────────────────────────────────────────────────────


def test_portfolio_vs_benchmark_excess(isolated):
    # Benchmark +8%, portfolio +12% → excess +4%, both over the same 1M clamped window.
    isolated(
        [_spy()],
        {
            "SPY": [
                {"date": "2026-05-05", "price": 100.0},
                {"date": "2026-06-05", "price": 108.0},  # benchmark +8%
            ]
        },
    )
    # portfolio dict includes effective_start/end/annualized (as portfolio_returns() emits).
    portfolio = {
        "twr": {"period_return": 0.12, "annualized": 0.12},
        "effective_start": "2026-05-05",
        "end": "2026-06-05",
        "annualized": False,  # 1M < 360d → cumulative
    }
    out = r.portfolio_vs_benchmark(portfolio, "I-SPY", "1M", today=_TODAY)
    assert out["portfolio_twr"] == pytest.approx(0.12)
    assert out["benchmark_twr"] == pytest.approx(0.08)
    assert out["excess_return"] == pytest.approx(0.04)  # 0.12 - 0.08
    assert out["benchmark"]["symbol"] == "SPY"
    # Both sides must agree on annualization: cumulative (annualized=False → period == ann).
    assert out["benchmark"]["twr"]["annualized"] == pytest.approx(0.08)


def test_portfolio_vs_benchmark_excess_null_when_portfolio_insufficient(isolated):
    isolated(
        [_spy()],
        {
            "SPY": [
                {"date": "2026-05-05", "price": 100.0},
                {"date": "2026-06-05", "price": 108.0},
            ]
        },
    )
    portfolio = {
        "twr": None,
        "effective_start": "2026-05-05",
        "end": "2026-06-05",
        "annualized": False,
    }
    out = r.portfolio_vs_benchmark(portfolio, "I-SPY", "1M", today=_TODAY)
    assert out["portfolio_twr"] is None
    assert out["benchmark_twr"] == pytest.approx(0.08)
    assert out["excess_return"] is None  # excess undefined unless BOTH sides present


def test_portfolio_vs_benchmark_unresolvable_is_none(isolated):
    isolated([_spy()], {"SPY": [{"date": "2026-06-05", "price": 100.0}]})
    portfolio = {
        "twr": None,
        "effective_start": "2026-05-05",
        "end": "2026-06-05",
        "annualized": False,
    }
    assert r.portfolio_vs_benchmark(portfolio, "NOPE", "1M", today=_TODAY) is None


def test_portfolio_vs_benchmark_clamped_window_consistent(isolated):
    """Regression: 1Y request on a young book (inception 3M ago) → effective window 3M
    → benchmark comparison uses the SAME 3M clamped window, both sides cumulative
    (annualized=False because 90 days < 360). Excess is window-consistent.

    This guards against the bug where benchmark_return re-resolved the FULL 1Y period
    while the portfolio used a 90-day clamped window — producing an apples-to-oranges
    excess return.
    """
    # Benchmark has a 1Y price history, but the portfolio was only started 90 days ago.
    # The clamped effective window is 2026-03-07 → 2026-06-05 (≈ 90 days).
    isolated(
        [_spy()],
        {
            "SPY": [
                {"date": "2025-06-05", "price": 80.0},  # pre-inception (full 1Y anchor)
                {"date": "2026-03-07", "price": 100.0},  # clamped window open
                {"date": "2026-06-05", "price": 106.0},  # clamped window close +6%
            ]
        },
    )
    # Simulate portfolio_returns() output for a clamped 1Y→90d window.
    # effective_start = inception date; span_days ≈ 90 → annualized=False.
    portfolio = {
        "twr": {"period_return": 0.10, "annualized": 0.10},
        "start": "2025-06-05",  # original 1Y requested start
        "effective_start": "2026-03-07",  # clamped to inception
        "end": "2026-06-05",
        "annualized": False,  # 90d < _SPAN_ANNUALIZE (360)
    }
    out = r.portfolio_vs_benchmark(portfolio, "I-SPY", "1Y", today=_TODAY)

    # Benchmark must use the clamped [2026-03-07, 2026-06-05] window → +6%.
    # NOT the full 1Y [2025-06-05, 2026-06-05] window → +32.5%.
    assert out["benchmark_twr"] == pytest.approx(0.06)
    assert out["benchmark"]["twr"]["annualized"] == pytest.approx(
        0.06
    )  # cumulative = ann when not annualized
    # Excess is window-consistent: both sides are 90d cumulative.
    assert out["excess_return"] == pytest.approx(0.04)  # 0.10 - 0.06
    # Both sides must have the same annualized flag.
    assert out["benchmark"]["twr"] is not None


# ── annualization-basis consistency ───────────────────────────────────────────────


def test_portfolio_vs_benchmark_uses_annualized_twr_on_long_window(isolated):
    """Regression: when annualized=True (≥360-day window), portfolio_vs_benchmark must use
    twr["annualized"] for BOTH portfolio_twr and benchmark_twr (not twr["period_return"]).
    Otherwise the stat card shows annualized portfolio vs. cumulative benchmark — wrong.
    """
    # 400-day window: start 2025-05-17 → end 2026-06-21. SPY: 100 → 115 over that span.
    isolated(
        [_spy()],
        {
            "SPY": [
                {"date": "2025-05-17", "price": 100.0},
                {"date": "2026-06-21", "price": 115.0},
            ]
        },
    )
    span_days = (date(2026, 6, 21) - date(2025, 5, 17)).days  # 400
    period_ret = 115.0 / 100.0 - 1.0  # 0.15
    years = span_days / 365.0
    expected_ann = round((1.0 + period_ret) ** (1.0 / years) - 1.0, 6)

    # Simulate portfolio_returns() for a ≥360d window: annualized=True.
    portfolio = {
        "twr": {"period_return": 0.20, "annualized": 0.18},
        "start": "2025-05-17",
        "effective_start": "2025-05-17",
        "end": "2026-06-21",
        "annualized": True,
    }
    out = r.portfolio_vs_benchmark(portfolio, "I-SPY", "1Y", today=date(2026, 6, 21))

    # portfolio_twr must come from twr["annualized"], not twr["period_return"].
    assert out["portfolio_twr"] == pytest.approx(0.18)
    # benchmark_twr must also be the annualized value.
    assert out["benchmark_twr"] == pytest.approx(expected_ann, rel=1e-4)
    # excess is annualized portfolio minus annualized benchmark.
    assert out["excess_return"] == pytest.approx(0.18 - expected_ann, rel=1e-4)


def test_portfolio_vs_benchmark_uses_period_return_on_short_window(isolated):
    """Complement: when annualized=False (<360-day window), both sides use period_return
    (cumulative), so comparison cards stay on the same basis as the stat card.
    """
    isolated(
        [_spy()],
        {
            "SPY": [
                {"date": "2026-03-07", "price": 100.0},
                {"date": "2026-06-05", "price": 106.0},  # +6% cumulative
            ]
        },
    )
    portfolio = {
        "twr": {"period_return": 0.10, "annualized": 0.45},  # annualized would be much higher
        "start": "2026-03-07",
        "effective_start": "2026-03-07",
        "end": "2026-06-05",
        "annualized": False,  # 90d < 360d → cumulative
    }
    out = r.portfolio_vs_benchmark(portfolio, "I-SPY", "3M", today=_TODAY)

    # Both sides must use the cumulative (period_return) values.
    assert out["portfolio_twr"] == pytest.approx(0.10)  # NOT 0.45 (the annualized value)
    assert out["benchmark_twr"] == pytest.approx(0.06)
    assert out["excess_return"] == pytest.approx(0.04)


# ── portfolio_returns_with_benchmark (composing entrypoint) ────────────────────────
#
# portfolio_returns_with_benchmark calls portfolio_returns (which internally calls
# portfolio_history.portfolio_value_series) and threads the value series through
# _value_pts. Tests that monkeypatch portfolio_returns must patch ph so the real
# portfolio_returns is never reached (it would look for live data via the primitive).


def _patch_ph(monkeypatch, series_pts=None):
    """Patch portfolio_history.portfolio_value_series to return an empty or small series.
    Needed because portfolio_returns calls this primitive; tests that mock portfolio_returns
    itself bypass this, but the patch is kept as a defensive isolation guard."""
    from backend.services import portfolio_history as ph

    ph_result = {
        "series": series_pts or [],
        "data_quality": {
            "market_since": None,
            "inception": None,
            "tiers": {},
            "has_unrecorded_risk": False,
            "has_unknown_cost": False,
            "fx_approximate": False,
        },
    }
    monkeypatch.setattr(ph, "portfolio_value_series", lambda *a, **k: ph_result)
    monkeypatch.setattr(ph, "inception_date", lambda *a, **k: None)


def test_with_benchmark_attaches_comparison(isolated, monkeypatch):
    isolated(
        [_spy()],
        {
            "SPY": [
                {"date": "2026-05-05", "price": 100.0},
                {"date": "2026-06-05", "price": 105.0},
            ]
        },
    )
    monkeypatch.setattr(
        r,
        "portfolio_returns",
        lambda period, today=None, **_range: {
            "twr": {"period_return": 0.20},
            "start": "2026-05-05",
            "end": "2026-06-05",
            "effective_start": "2026-05-05",
        },
    )
    _patch_ph(monkeypatch)
    out = r.portfolio_returns_with_benchmark("1M", benchmark="I-SPY", today=_TODAY)
    assert out["comparison"]["benchmark_twr"] == pytest.approx(0.05)
    assert out["comparison"]["excess_return"] == pytest.approx(0.15)


def test_without_benchmark_omits_comparison_key(isolated, monkeypatch):
    monkeypatch.setattr(
        r,
        "portfolio_returns",
        lambda period, today=None, **_range: {
            "twr": None,
            "start": "2026-05-05",
            "end": "2026-06-05",
            "effective_start": "2026-05-05",
        },
    )
    _patch_ph(monkeypatch)
    out = r.portfolio_returns_with_benchmark("1M", benchmark=None, today=_TODAY)
    assert "comparison" not in out


# ── Fix B: sparse benchmark anchor (BTC / recently-listed) ────────────────────────


def test_benchmark_price_series_no_prewindow_anchors_earliest_in_window(isolated):
    """A benchmark with no price on/before the window start anchors the earliest in-window
    price AT the window start so the rebased series is 0-based without a flat-zero prefix.

    Regression: BTC selected as benchmark on a 1Y window where BTC only has 90 days of
    history. Without this fix the series would start at its first real price point (not at
    the window start), making the rebased return misleadingly start mid-way through the window.
    """
    # BTC: first price is 90 days AFTER the window start.
    isolated(
        [{"id": "I-BTC", "symbol": "BTC", "price_source": "yahoo", "price_source_id": "BTC"}],
        {
            "BTC": [
                {
                    "date": "2026-04-01",
                    "price": 80_000.0,
                },  # 65 days after window start (2026-01-26)
                {"date": "2026-06-05", "price": 88_000.0},
            ]
        },
    )
    # Window: 2026-01-26 → 2026-06-05 (≈ 130 days); BTC data only starts 2026-04-01.
    series = r.benchmark_price_series("BTC", "2026-01-26", "2026-06-05")

    # Must be anchored at the window start with the first available price.
    assert series[0]["date"] == "2026-01-26"
    assert series[0]["price"] == pytest.approx(80_000.0)
    # End anchor must be at window end.
    assert series[-1]["date"] == "2026-06-05"
    assert series[-1]["price"] == pytest.approx(88_000.0)
    # All prices positive (no fabricated zeros).
    assert all(p["price"] > 0 for p in series)
    # Rebased return from start: 88000/80000 - 1 = 10% — reflects the actual price move.
    rebased_end = series[-1]["price"] / series[0]["price"] - 1.0
    assert rebased_end == pytest.approx(0.10)


def test_benchmark_price_series_prewindow_price_still_anchors_correctly(isolated):
    """When a pre-window price exists, behavior is unchanged (not affected by Fix B)."""
    isolated(
        [{"id": "I-SPY", "symbol": "SPY", "price_source": "yahoo", "price_source_id": "SPY"}],
        {
            "SPY": [
                {"date": "2026-03-01", "price": 90.0},  # pre-window (before 2026-04-01 start)
                {"date": "2026-05-01", "price": 100.0},
                {"date": "2026-06-05", "price": 105.0},
            ]
        },
    )
    series = r.benchmark_price_series("SPY", "2026-04-01", "2026-06-05")

    # Start anchor uses the pre-window price (90.0), dated at the window start.
    assert series[0]["date"] == "2026-04-01"
    assert series[0]["price"] == pytest.approx(90.0)
    assert series[-1]["date"] == "2026-06-05"
    assert series[-1]["price"] == pytest.approx(105.0)
