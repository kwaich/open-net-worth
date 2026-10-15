"""The pulse row's trend / next event / to-your-price cells — synthetic data, no network."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest

from backend.services import watch_rules as rules
from backend.services.dividend_calendar import next_ex_date
from backend.services.prices import _next_earnings
from backend.services.watch_stats import MOMENTUM_WINDOW, history_stats
from backend.services.watch_trend import row_trend, trend

NOW = datetime(2026, 9, 25, 15, 0, tzinfo=timezone.utc)


def dated(closes: list[float]) -> list[tuple[str, float]]:
    """Closes ending yesterday, one per calendar day (oldest first)."""
    end = date(2026, 9, 24)
    n = len(closes)
    return [((end - timedelta(days=n - 1 - i)).isoformat(), c) for i, c in enumerate(closes)]


def ramp(n: int, start: float = 100.0, step: float = 0.5) -> list[float]:
    return [start + i * step for i in range(n)]


# ── trend ────────────────────────────────────────────────────────────────────


def test_each_period_ends_on_todays_price_and_measures_from_its_first_close():
    hist = dated(ramp(300))
    out = trend(hist, price=260.0)
    one = out["1M"]
    assert one["closes"][-1] == 260.0
    assert len(one["closes"]) == MOMENTUM_WINDOW + 1
    first = hist[-MOMENTUM_WINDOW][1]
    assert one["pct"] == pytest.approx((260.0 / first - 1) * 100, abs=0.01)
    assert len(out["3M"]["closes"]) == 64


def test_one_year_is_thinned_to_weekly_but_keeps_its_first_close_and_today():
    hist = dated(ramp(300))
    y = trend(hist, price=260.0)["1Y"]
    assert y["closes"][0] == hist[-252][1]
    assert y["closes"][-1] == 260.0
    assert 50 <= len(y["closes"]) <= 54


def test_one_month_caption_matches_the_momentum_flag_arithmetic():
    """The caption and watch_stats' month-vs-benchmark must be the same number."""
    stock = dated(ramp(80, 50.0, 0.3))
    bench = dated(ramp(80, 400.0, 1.0))
    out = trend(stock, price=75.0, bench_series=bench, bench_day=0.4)["1M"]
    stats = history_stats(stock, price=75.0, day_pct=1.0, bench_series=bench, bench_day=0.4)
    assert out["pct"] == pytest.approx(stats["mom_1m"], abs=0.01)
    assert out["vs_bench_pct"] == pytest.approx(stats["mom_1m_vs_bench"], abs=0.02)
    assert len(out["bench"]) == len(out["closes"])
    assert out["bench"][-1] == pytest.approx(bench[-1][1] * 1.004, abs=1e-3)


def test_no_benchmark_move_means_no_benchmark_line():
    out = trend(dated(ramp(80)), price=150.0, bench_series=dated(ramp(80)), bench_day=None)
    assert out["1M"]["bench"] is None and out["1M"]["vs_bench_pct"] is None


def test_a_benchmark_that_starts_later_than_the_period_is_not_drawn():
    out = trend(dated(ramp(80)), price=150.0, bench_series=dated(ramp(10)), bench_day=0.1)
    assert out["1M"]["bench"] is None


def test_thin_history_draws_only_the_periods_it_covers():
    out = trend(dated(ramp(40)), price=130.0)
    assert out["1M"] is not None and out["3M"] is not None and out["1Y"] is None
    assert trend(dated(ramp(5)), price=130.0) is None
    assert trend(dated(ramp(80)), price=None) is None


# ── which line is dashed ─────────────────────────────────────────────────────


SPY = {"symbol": "SPY", "day_pct": 0.4}
XLK = {"symbol": "XLK", "day_pct": 1.0}


def test_sector_fund_is_the_line_when_it_draws_and_the_benchmark_rides_in_the_tooltip():
    stock, spy, xlk = dated(ramp(80, 50.0, 0.3)), dated(ramp(80, 400.0)), dated(ramp(80, 150.0))
    line, compare = row_trend(
        stock, price=75.0, bench=SPY, bench_series=spy, sector=XLK, sector_series=xlk
    )
    assert compare == {"symbol": "XLK", "kind": "sector"}
    one = line["1M"]
    assert one["bench"][-1] == pytest.approx(xlk[-1][1] * 1.01, abs=1e-3)
    market_only = trend(stock, price=75.0, bench_series=spy, bench_day=0.4)["1M"]
    assert one["vs_market_pct"] == market_only["vs_bench_pct"]
    assert one["vs_bench_pct"] != one["vs_market_pct"]


def test_falls_back_to_the_benchmark_without_sector_history():
    stock, spy = dated(ramp(80)), dated(ramp(80, 400.0))
    line, compare = row_trend(
        stock, price=150.0, bench=SPY, bench_series=spy, sector=XLK, sector_series=[]
    )
    assert compare == {"symbol": "SPY", "kind": "benchmark"}
    assert line["1M"]["bench"] is not None and line["1M"]["vs_market_pct"] is None


def test_no_history_means_no_line_and_nothing_to_compare():
    assert row_trend([], price=10.0, bench=SPY, bench_series=[]) == (None, None)


# ── next event ───────────────────────────────────────────────────────────────


def test_next_event_is_the_soonest_of_earnings_and_ex_dividend():
    snap = {"earnings_at": (NOW + timedelta(days=12)).isoformat()}
    assert rules.next_event(snap, None, NOW)["kind"] == "earnings"
    ev = rules.next_event(snap, (NOW + timedelta(days=4)).date().isoformat(), NOW)
    assert ev == {"kind": "ex_dividend", "at": ev["at"], "in_days": 4}


def test_next_event_ignores_past_and_far_dates():
    past = {"earnings_at": (NOW - timedelta(days=2)).isoformat()}
    far = {"earnings_at": (NOW + timedelta(days=90)).isoformat()}
    assert rules.next_event(past, None, NOW) is None
    assert rules.next_event(far, None, NOW) is None


def test_snapshot_earnings_prefers_the_next_report_over_the_last_one():
    now = NOW.timestamp()
    q = {"earningsTimestamp": now - 40 * 86400, "earningsTimestampStart": now + 30 * 86400}
    got = datetime.fromisoformat(_next_earnings(q, now))
    assert (got - NOW).days == 30
    assert _next_earnings({"earningsTimestamp": now - 40 * 86400}, now) is not None
    assert _next_earnings({}, now) is None


def test_next_ex_date_declared_else_projected():
    today = date(2026, 9, 25)
    assert next_ex_date({"next_ex_date": "2026-10-02"}, today) == "2026-10-02"
    projected = next_ex_date(
        {"last_ex_date": "2026-08-10", "dividend_frequency": "quarterly"}, today
    )
    assert projected is not None and projected >= today.isoformat()
    assert next_ex_date({}, today) is None


def test_attach_loads_each_sector_funds_history_once(monkeypatch):
    from backend.services import watch_context as wc

    monkeypatch.setattr(wc, "history_for", lambda pid, now: [("2026-09-24", 1.0)])
    monkeypatch.setattr(wc, "benchmark_history", lambda sym, now: [("2026-09-24", sym)])
    ctx = {"now": NOW, "sector_etf": {"AMD": "XLK", "NVDA": "XLK", "XOM": "XLE"}}
    wc.attach(ctx, pids={"AMD": "AMD"}, bench_key="SPY")
    assert ctx["sector_series"] == {"XLE": [("2026-09-24", "XLE")], "XLK": [("2026-09-24", "XLK")]}
    assert ctx["bench_series"] == [("2026-09-24", "SPY")]
