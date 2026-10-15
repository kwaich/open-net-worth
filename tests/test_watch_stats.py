"""watch_stats + the history-aware watch rules — synthetic series only, no I/O."""

from __future__ import annotations

import math
import random
from datetime import date, datetime, timedelta, timezone

import pytest

from backend.services import watch_rules as rules
from backend.services.watch_intent import in_buy_zone, intent_findings
from backend.services.watch_market import split_suspect, volume_pace
from backend.services.watch_stats import history_stats

NOW = datetime(2026, 9, 25, 15, 0, tzinfo=timezone.utc)


def series(daily_sd: float, n: int = 260, *, seed: int = 1, drift: float = 0.0, start=100.0):
    """n dated closes (oldest first, ending yesterday) with daily % moves ~ N(drift, daily_sd)."""
    rnd = random.Random(seed)
    closes, px = [], start
    for i in range(n):
        px *= 1 + rnd.gauss(drift, daily_sd) / 100
        closes.append(((date(2026, 9, 24) - timedelta(days=n - 1 - i)).isoformat(), px))
    return closes


def market_linked(bench, beta: float, noise_sd: float, seed: int = 2):
    """A stock whose daily return = beta × the benchmark's + noise."""
    rnd = random.Random(seed)
    out, px = [(bench[0][0], 50.0)], 50.0
    for (_, a), (d, b) in zip(bench, bench[1:]):
        r = (b / a - 1) * 100
        px *= 1 + (beta * r + rnd.gauss(0, noise_sd)) / 100
        out.append((d, px))
    return out


def _last(s):
    return s[-1][1]


# ── the stock's own "usual move" ─────────────────────────────────────────────────


def test_same_percent_move_reads_very_differently_for_a_calm_and_a_volatile_stock():
    calm, wild = series(0.8), series(4.0)
    z_calm = history_stats(calm, price=_last(calm) * 0.96, day_pct=-4.0)["z"]
    z_wild = history_stats(wild, price=_last(wild) * 0.96, day_pct=-4.0)["z"]
    assert z_calm < -3.5
    assert -1.5 < z_wild < 0


def test_too_little_history_means_no_stats_never_an_estimate():
    assert history_stats(series(1.0, n=20), price=100, day_pct=-5) is None


def test_market_move_vs_company_move():
    bench = series(1.0, seed=7)
    stock = market_linked(bench, beta=1.5, noise_sd=0.5)
    px = _last(stock)
    market_day = history_stats(
        stock, price=px * 0.955, day_pct=-4.5, bench_series=bench, bench_day=-3.0
    )
    assert market_day["beta"] == pytest.approx(1.5, abs=0.25)
    assert market_day["move_kind"] == "market"
    company_day = history_stats(
        stock, price=px * 0.955, day_pct=-4.5, bench_series=bench, bench_day=0.2
    )
    assert company_day["move_kind"] == "company"
    sector_day = history_stats(
        stock, price=px * 0.955, day_pct=-4.5, bench_series=bench, bench_day=0.2, sector_day=-4.2
    )
    assert sector_day["move_kind"] == "sector"


def test_streak_drawdown_and_momentum():
    base = series(0.5, seed=3)
    # an up day, then five straight down days of 3% at the end
    base[-6] = (base[-6][0], base[-7][1] * 1.01)
    tail, px = [], base[-6][1]
    for i, (d, _) in enumerate(base[-5:]):
        px *= 0.97
        tail.append((d, px))
    s = base[:-5] + tail
    st = history_stats(s, price=px * 0.97, day_pct=-3.0)
    assert st["streak"]["days"] == -6  # five in history + today
    assert st["streak"]["cum_pct"] == pytest.approx((0.97**6 - 1) * 100, abs=0.01)
    assert st["drawdown_20d"] <= st["drawdown_threshold"]


def test_recent_200_day_cross_is_remembered_for_a_few_sessions():
    # a slow decline (always just under its own 200-day average), then a jump two sessions ago
    flat = [
        ((date(2026, 1, 1) + timedelta(days=i)).isoformat(), 100.0 - i * 0.001) for i in range(230)
    ]
    # jump above the average two sessions ago and stay there
    flat[-2] = (flat[-2][0], 110.0)
    flat[-1] = (flat[-1][0], 111.0)
    st = history_stats(flat, price=111.5, day_pct=0.4)
    assert st["cross_200d"] == {"direction": "up", "days_ago": 2}


# ── rules on top of the stats ────────────────────────────────────────────────────


def test_scored_move_says_how_unusual_and_what_drove_it():
    calm = series(0.8)
    snap = {"price": _last(calm) * 0.96, "prev_close": _last(calm)}
    stats = history_stats(calm, price=snap["price"], day_pct=-4.0)
    stats["move_kind"] = "company"
    found = rules.evaluate(
        snap, asset_class="equity", now=NOW, stats=stats, bench={"symbol": "SPY", "day_pct": 0.3}
    )
    big = next(f for f in found if f["kind"] == "big_move")
    assert "its usual daily move" in big["text"] and big["severity"] == "high"
    assert any(f["kind"] == "company_move" and "SPY +0.3%" in f["text"] for f in found)


def test_an_ordinary_day_for_a_volatile_stock_is_not_flagged():
    wild = series(4.0)
    snap = {"price": _last(wild) * 0.94, "prev_close": _last(wild)}
    stats = history_stats(wild, price=snap["price"], day_pct=-6.0)
    assert not any(
        f["kind"] == "big_move"
        for f in rules.evaluate(snap, asset_class="equity", now=NOW, stats=stats)
    )


def test_a_split_day_is_named_not_mistaken_for_a_crash():
    snap = {"price": 25.0, "prev_close": 100.0, "volume": 1_000_000, "avg_volume": 900_000}
    found = rules.evaluate(snap, asset_class="equity", now=NOW)
    assert [f["kind"] for f in found] == ["possible_split"]
    assert split_suspect(-75.0, 1.1) and not split_suspect(-75.0, 6.0) and not split_suspect(-9, 1)
    # no volume → no way to tell it from a crash; a coin or a commodity never splits
    assert not split_suspect(-50.0, None)
    assert not split_suspect(-50.0, 1.0, "crypto") and split_suspect(-50.0, 1.0, "equity")


def test_volume_is_judged_on_pace_for_the_time_of_day():
    snap = {
        "volume": 900_000,
        "avg_volume": 3_000_000,
        "market_state": "REGULAR",
        "exchange_tz": "America/New_York",
    }
    morning = datetime(2026, 9, 25, 14, 15, tzinfo=timezone.utc)  # 10:15 New York — ~11% in
    assert volume_pace(snap, morning) == pytest.approx(
        900_000 / (3_000_000 * (0.75 / 6.5)), rel=0.01
    )
    closed = {**snap, "market_state": "CLOSED"}
    assert volume_pace(closed, morning) == pytest.approx(0.3)


def test_material_triggers_are_few_and_specific():
    company = [{"kind": "big_move", "meta": {"z": -3.1, "move_kind": "company"}}]
    market = [{"kind": "big_move", "meta": {"z": -3.1, "move_kind": "market"}}]
    mild = [{"kind": "big_move", "meta": {"z": -2.1, "move_kind": "company"}}]
    assert rules.material_triggers(company) == ["company_move"]
    assert rules.material_triggers(market) == []
    assert rules.material_triggers(mild) == []
    assert rules.material_triggers([{"kind": "entered_buy_zone"}]) == ["buy_zone"]
    assert rules.material_triggers([{"kind": "crossed_below_fair_value"}]) == ["fair_value"]
    assert rules.material_triggers([{"kind": "breakout"}], pace=1.2) == []
    assert rules.material_triggers([{"kind": "breakout"}], pace=2.4) == ["breakout"]


# ── intent ──────────────────────────────────────────────────────────────────────


def test_entering_the_buy_zone_vs_already_in_it():
    intent = {"buy_below": 90.0, "buy_above": 70.0}
    assert in_buy_zone(88, intent) and not in_buy_zone(92, intent) and not in_buy_zone(60, intent)
    entered = intent_findings({"price": 88.0, "prev_close": 93.0}, intent)
    assert entered[0]["kind"] == "entered_buy_zone" and "70–90" in entered[0]["text"]
    stay = intent_findings({"price": 85.0, "prev_close": 88.0}, intent)
    assert stay[0]["kind"] == "in_buy_zone"
    assert in_buy_zone(88, {}) is None


def test_fair_value_gap_is_news_only_on_the_day_it_opens():
    intent = {"fair_value": 150.0}
    crossed = intent_findings({"price": 133.0, "prev_close": 137.0}, intent)
    assert crossed[0]["kind"] == "crossed_below_fair_value"
    already = intent_findings({"price": 120.0, "prev_close": 121.0}, intent)
    assert already[0]["kind"] == "below_fair_value"
    rich = intent_findings({"price": 180.0, "prev_close": 179.0}, intent)
    assert rich[0]["kind"] == "above_fair_value"


def test_earnings_reminds_you_of_your_thesis():
    snap = {
        "price": 100.0,
        "prev_close": 100.0,
        "earnings_at": (NOW + timedelta(days=2)).isoformat(),
    }
    found = rules.evaluate(snap, asset_class="equity", now=NOW, thesis="AI capex keeps growing")
    assert any(f["text"].endswith("check your thesis") for f in found)


def test_sigma_is_the_sample_stdev_of_daily_returns():
    s = [
        ((date(2026, 1, 1) + timedelta(days=i)).isoformat(), 100 * (1.01 if i % 2 else 1.0))
        for i in range(60)
    ]
    st = history_stats(s, price=100.0, day_pct=0.0)
    assert st["sigma"] == pytest.approx(
        math.sqrt(sum(x * x for x in [1.0, -0.990099] * 29) / 57), rel=0.05
    )
