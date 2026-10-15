"""watch_rules — deterministic signal rules for watched-not-held tickers (pure, no I/O)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from backend.services import watch_rules as rules

NOW = datetime(2026, 9, 24, 12, 0, tzinfo=timezone.utc)


def _snap(**kw) -> dict:
    return {"price": 100.0, "prev_close": 100.0, **kw}


def _kinds(snap: dict, **kw) -> set[str]:
    kw.setdefault("asset_class", "equity")
    return {f["kind"] for f in rules.evaluate(snap, now=NOW, **kw)}


def test_flat_day_raises_nothing():
    assert rules.evaluate(_snap(), asset_class="equity", now=NOW) == []


def test_big_move_up_and_down_use_the_class_threshold():
    assert "big_move" in _kinds(_snap(price=106.0))
    assert "big_move" in _kinds(_snap(price=94.0))
    assert "big_move" not in _kinds(_snap(price=104.0))
    assert "big_move" not in _kinds(_snap(price=108.0), asset_class="crypto")


def test_big_move_severity_is_high_at_double_the_threshold():
    (f,) = [x for x in rules.evaluate(_snap(price=111.0), asset_class="equity", now=NOW)]
    assert f["kind"] == "big_move" and f["severity"] == "high" and f["tone"] == "good"


def test_big_move_annotates_relative_strength_only_when_it_differs():
    bench = {"symbol": "SPY", "day_pct": 0.5}
    (f,) = rules.evaluate(_snap(price=106.0), asset_class="equity", bench=bench, now=NOW)
    assert "ahead of SPY" in f["text"]
    bench = {"symbol": "SPY", "day_pct": 5.0}
    (f,) = rules.evaluate(_snap(price=106.0), asset_class="equity", bench=bench, now=NOW)
    assert "SPY" not in f["text"]


def test_52_week_extremes():
    assert "breakout" in _kinds(_snap(week52_high=100.05, week52_low=50.0))
    assert "near_high" in _kinds(_snap(week52_high=101.5, week52_low=50.0))
    assert "breakdown" in _kinds(_snap(week52_high=150.0, week52_low=99.95))
    assert "near_low" in _kinds(_snap(week52_high=150.0, week52_low=98.0))


def test_discount_needs_25pct_below_high_and_is_suppressed_at_a_low():
    assert "discount" in _kinds(_snap(week52_high=140.0, week52_low=60.0))
    assert "discount" not in _kinds(_snap(week52_high=140.0, week52_low=99.95))


def test_average_cross_needs_prior_close_and_price_to_straddle():
    assert "reclaim_200d" in _kinds(_snap(price=101.0, prev_close=99.0, avg_200d=100.0))
    assert "lost_200d" in _kinds(_snap(price=99.0, prev_close=101.0, avg_200d=100.0))
    assert "reclaim_200d" not in _kinds(_snap(price=102.0, prev_close=101.0, avg_200d=100.0))
    assert "reclaim_50d" in _kinds(_snap(price=101.0, prev_close=99.0, avg_50d=100.0))


def test_volume_surge_needs_the_ratio():
    assert "volume" in _kinds(_snap(volume=3_000_000, avg_volume=1_000_000))
    assert "volume" not in _kinds(_snap(volume=1_500_000, avg_volume=1_000_000))


def test_earnings_window():
    def soon(days: int) -> set[str]:
        at = (NOW + timedelta(days=days, hours=9)).isoformat()
        return _kinds(_snap(earnings_at=at))

    assert "earnings_soon" in soon(0) and "earnings_soon" in soon(7)
    assert "earnings_soon" not in soon(8)
    assert "earnings_soon" not in _kinds(
        _snap(earnings_at=datetime(2026, 9, 20, tzinfo=timezone.utc).isoformat())
    )


def test_alert_proximity_ignores_disabled_and_move_alerts():
    alerts = [
        {"kind": "price", "op": ">=", "value": 101.5},
        {"kind": "price", "op": ">=", "value": 100.5, "enabled": False},
        {"kind": "move", "op": ">=", "value": 5},
    ]
    near = rules.nearest_alert(100.0, alerts)
    assert near and near["level"] == 101.5
    assert "alert_near" in _kinds(_snap(), alerts=alerts)
    assert "alert_near" not in _kinds(_snap(), alerts=[{"kind": "price", "op": ">=", "value": 120}])


def test_findings_are_sorted_by_weight_and_story_kinds_exclude_pulse_only_ones():
    snap = _snap(price=107.0, week52_high=150.0, week52_low=60.0, volume=9, avg_volume=1)
    found = rules.evaluate(snap, asset_class="equity", now=NOW)
    assert [f["weight"] for f in found] == sorted((f["weight"] for f in found), reverse=True)
    assert {f["kind"] for f in rules.story_findings(found)} == {"big_move"}
    assert {"discount", "volume"} <= {f["kind"] for f in found}


def test_story_type_is_opportunity_only_when_constructive_and_not_bearish():
    up = rules.evaluate(_snap(week52_high=100.05, week52_low=50.0), asset_class="equity", now=NOW)
    assert rules.story_type(rules.story_findings(up)) == "opportunity"
    mixed = rules.evaluate(
        _snap(price=94.0, week52_high=200.0, week52_low=93.0), asset_class="equity", now=NOW
    )
    assert rules.story_type(rules.story_findings(mixed)) == "asset"


def test_portfolio_fit():
    held = {"Technology": 40.0, "Energy": 10.0}
    assert rules.portfolio_fit(None, held) is None
    assert "New sector" in rules.portfolio_fit("Healthcare", held)["note"]
    top = rules.portfolio_fit("Technology", held)
    assert top["rank"] == 1 and "largest sector" in top["note"]
    second = rules.portfolio_fit("Energy", held)
    assert second["rank"] == 2 and "largest" not in second["note"]


def test_range_position_and_missing_fields_degrade_to_none():
    assert rules.range_position(_snap(week52_low=50.0, week52_high=150.0)) == 0.5
    assert rules.range_position(_snap()) is None
    assert rules.day_pct({"price": 5.0}) is None
