"""GET /api/intel/watch-pulse + watch_pulse.build_pulse — synthetic data, no network."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from backend.core.errors import ValidationError
from backend.db.intel_stories_store import IntelStoriesStore
from backend.main import app
from backend.services import watch_pulse, watch_signals

NOW = datetime(2026, 9, 24, 12, 0, tzinfo=timezone.utc)


def _item(value, **kw) -> dict:
    return {
        "id": f"wli_{value}",
        "type": "ticker",
        "value": value,
        "label": value,
        "asset_class": "equity",
        "monitor_news": True,
        "added_by": "user",
        "watchlist_ids": ["wl_1"],
        **kw,
    }


def _ctx(items, snaps, **kw) -> dict:
    return {
        "now": NOW,
        "items": items,
        "snaps": snaps,
        "bench": kw.get("bench"),
        "alerts": kw.get("alerts", {}),
        "held_by_sector": kw.get("held_by_sector", {}),
        "sectors": kw.get("sectors", {}),
    }


@pytest.fixture
def client():
    return TestClient(app)


def test_empty_watchlist(monkeypatch):
    monkeypatch.setattr(watch_signals, "gather_context", lambda now=None, **kw: _ctx([], {}))
    out = watch_pulse.build_pulse()
    assert out["status"] == "empty" and out["rows"] == []


def test_unavailable_when_no_quote_resolves(monkeypatch):
    monkeypatch.setattr(
        watch_signals, "gather_context", lambda now=None, **kw: _ctx([_item("NVDA")], {})
    )
    monkeypatch.setattr(watch_pulse, "_live_stories_by_ticker", lambda: {})
    out = watch_pulse.build_pulse()
    assert out["status"] == "unavailable" and out["summary"]["tracked"] == 1


def test_rows_are_ranked_by_attention_and_carry_context(monkeypatch):
    snaps = {
        "AMD": {"price": 100.0, "prev_close": 100.0, "week52_low": 60.0, "week52_high": 140.0},
        "NVDA": {
            "price": 108.0,
            "prev_close": 100.0,
            "week52_low": 60.0,
            "week52_high": 150.0,
            "volume": 3_000_000,
            "avg_volume": 1_000_000,
            "avg_200d": 90.0,
            "earnings_at": (NOW + timedelta(days=3)).isoformat(),
            "currency": "USD",
        },
    }
    ctx = _ctx(
        [_item("AMD"), _item("NVDA")],
        snaps,
        bench={"symbol": "SPY", "day_pct": 0.5},
        alerts={"NVDA": [{"kind": "price", "value": 110.0}]},
        held_by_sector={"Technology": 40.0},
        sectors={"NVDA": "Technology"},
    )
    monkeypatch.setattr(watch_signals, "gather_context", lambda now=None, **kw: ctx)
    monkeypatch.setattr(
        watch_pulse,
        "_live_stories_by_ticker",
        lambda: {"NVDA": [{"id": "s1", "updated_at": "2026-09-24T10:00:00"}]},
    )
    out = watch_pulse.build_pulse()
    assert [r["ticker"] for r in out["rows"]] == ["NVDA", "AMD"]
    nvda = out["rows"][0]
    assert nvda["day_pct"] == 8.0 and nvda["rel_day_pct"] == 7.5
    assert nvda["earnings_in_days"] == 3 and nvda["volume_ratio"] == 3.0
    assert nvda["nearest_alert"]["level"] == 110.0
    assert nvda["fit"]["rank"] == 1 and nvda["story_id"] == "s1" and nvda["story_count"] == 1
    assert nvda["range_pos"] == pytest.approx(48 / 90, abs=1e-3)
    assert nvda["next_event"]["kind"] == "earnings" and nvda["next_event"]["in_days"] == 3
    assert nvda["trend"] is None  # no history in ctx
    assert out["summary"] == {"tracked": 2, "priced": 2, "up": 1, "down": 0, "flagged": 2}


def test_endpoint_shape(client, monkeypatch):
    monkeypatch.setattr(watch_signals, "gather_context", lambda now=None, **kw: _ctx([], {}))
    res = client.get("/api/intel/watch-pulse")
    assert res.status_code == 200
    body = res.json()
    assert set(body) == {"generated_at", "population", "benchmark", "status", "summary", "rows"}
    assert body["population"] == "watched"
    assert client.get("/api/intel/watch-pulse?population=nope").status_code == 422


def test_live_stories_by_ticker_ignores_archived_dismissed_and_resolved(db_isolated):
    from backend.db.intel_stories_store import IntelStoriesStore

    s = IntelStoriesStore()
    base = {
        "type": "asset",
        "label": "x",
        "current_summary": "",
        "action_items": [],
        "status": "active",
    }
    s.insert({**base, "id": "live", "ticker": "nvda"})
    s.insert({**base, "id": "dis", "ticker": "NVDA", "dismissed": True})
    s.insert({**base, "id": "res", "ticker": "NVDA", "resolved": True})
    s.insert({**base, "id": "none"})
    got = watch_pulse._live_stories_by_ticker()
    assert list(got) == ["NVDA"] and [x["id"] for x in got["NVDA"]] == ["live"]


def _held_row(iid, ticker, qty, *, account="a1", cls="equity", source="yahoo", day_change=None):
    return {
        "instrument_id": iid,
        "ticker": ticker,
        "symbol": ticker,
        "label": f"{ticker} Co",
        "asset_class": cls,
        "quantity": qty,
        "price": 100.0,
        "quote_per_unit": 1.0,
        "quote_currency": "SGD",
        "currency": "SGD",
        "vesting_state": "vested",
        "price_source": source,
        "price_source_id": f"{ticker}.X" if source == "yahoo" else ticker,
        "day_change": day_change,
        "account_id": account,
    }


def test_held_pulse_sums_legs_carries_position_and_quotes_the_listing(monkeypatch):
    """The Holdings pulse runs the SAME rules over what you own: one row per name (legs at two
    accounts summed), its weight from the invested book, quoted by its Yahoo listing symbol — a
    coin as <SYM>-USD, never the bare symbol (a US-listed trust). Cash never reaches the feed."""
    import backend.services.alerts as alerts
    import backend.services.fx_cache as fx_cache
    import backend.services.positions as positions
    import backend.services.prices as prices

    monkeypatch.setattr(
        positions,
        "hydrated",
        lambda: [
            _held_row("i1", "NVDA", 3, account="a1", day_change=24.0),
            _held_row("i1", "NVDA", 3, account="a2", day_change=24.0),
            _held_row("i2", "BTC", 4, cls="crypto", source="coingecko"),
            _held_row("i3", "CASH", 500, cls="cash"),
        ],
    )
    monkeypatch.setattr(fx_cache, "load_fx_cache", lambda: {"SGD": 1.0})
    monkeypatch.setattr(alerts, "alerts_by_ticker", lambda: {})
    monkeypatch.setattr(watch_signals, "benchmark_quote_symbol", lambda: ("SPY", "SPY"))
    monkeypatch.setattr(watch_pulse, "_live_stories_by_ticker", lambda: {})
    asked: list[list[str]] = []

    def quotes(syms):
        asked.append(list(syms))
        return {
            "NVDA.X": {"price": 100.0, "prev_close": 92.0, "currency": "USD"},
            "SPY": {"price": 101.0, "prev_close": 100.0},
        }

    monkeypatch.setattr(prices, "fetch_quote_snapshots", quotes)
    out = watch_pulse.build_pulse(NOW, population="held")
    assert asked == [["NVDA.X", "BTC-USD", "SPY"]]
    assert out["population"] == "held" and out["status"] == "ok"
    assert out["summary"]["tracked"] == 2 and out["summary"]["priced"] == 1
    (nvda,) = out["rows"]
    assert nvda["ticker"] == "NVDA" and nvda["fit"] is None
    assert nvda["position"] == {"value": 600.0, "weight": 60.0, "day_pl": 48.0}
    assert any(f["kind"] == "big_move" for f in nvda["findings"])


def test_held_pulse_raises_no_stories(monkeypatch, db_isolated):
    """Crash-watch and the risk engine own held-name stories; the held pulse is read-only."""
    import backend.services.positions as positions

    monkeypatch.setattr(positions, "hydrated", lambda: [])
    before = len(IntelStoriesStore().all())
    watch_pulse.build_pulse(NOW, population="held")
    assert len(IntelStoriesStore().all()) == before


def test_unknown_population_is_refused():
    with pytest.raises(ValidationError):
        watch_pulse.build_pulse(NOW, population="everything")


def _hist(n: int, start: float, step: float) -> list[tuple[str, float]]:
    end = NOW.date()
    return [((end - timedelta(days=n - i)).isoformat(), start + i * step) for i in range(n)]


def test_watched_trend_compares_with_the_sector_fund_held_with_the_benchmark(monkeypatch):
    """Watching asks "is it winning in its industry"; Holdings asks "what would the index have
    done" — so the dashed line is the sector fund on one and the benchmark on the other."""
    snap = {"price": 110.0, "prev_close": 108.0, "week52_low": 60.0, "week52_high": 150.0}
    ctx = _ctx(
        [_item("AMD"), _item("GLD", asset_class="commodity")],
        {"AMD": snap, "GLD": dict(snap)},
        bench={"symbol": "SPY", "day_pct": 0.5},
        sectors={"AMD": "Technology"},
    )
    ctx.update(
        history={"AMD": _hist(80, 80.0, 0.3), "GLD": _hist(80, 90.0, 0.2)},
        bench_series=_hist(80, 400.0, 1.0),
        sector_etf={"AMD": "XLK"},
        sector_moves={"XLK": 1.2},
        sector_series={"XLK": _hist(80, 150.0, 0.5)},
    )
    monkeypatch.setattr(watch_signals, "gather_context", lambda now=None, **kw: dict(ctx))
    monkeypatch.setattr(watch_pulse, "held_context", lambda now=None: dict(ctx))
    monkeypatch.setattr(watch_pulse, "_live_stories_by_ticker", lambda: {})
    watched = {r["ticker"]: r for r in watch_pulse.build_pulse()["rows"]}
    assert watched["AMD"]["compare"] == {"symbol": "XLK", "kind": "sector"}
    assert watched["AMD"]["trend"]["1M"]["vs_market_pct"] is not None
    assert watched["GLD"]["compare"] == {"symbol": "SPY", "kind": "benchmark"}  # not an equity
    held = {r["ticker"]: r for r in watch_pulse.build_pulse(population="held")["rows"]}
    assert held["AMD"]["compare"] == {"symbol": "SPY", "kind": "benchmark"}


def test_watched_rows_without_a_line_or_sector_ask_for_history(monkeypatch):
    """A ticker drawn with no trend line (just added / typed by symbol) or no sector to compare
    with is handed to watch_history for a background fetch; one that has both is not."""
    snap = {"price": 110.0, "prev_close": 108.0, "week52_low": 60.0, "week52_high": 150.0}
    ctx = _ctx(
        [_item("AMD"), _item("NEW"), _item("NOSECTOR")],
        {"AMD": snap, "NEW": dict(snap), "NOSECTOR": dict(snap)},
        bench={"symbol": "SPY", "day_pct": 0.5},
        sectors={"AMD": "Technology", "NEW": "Technology"},
    )
    ctx.update(
        history={"AMD": _hist(80, 80.0, 0.3), "NOSECTOR": _hist(80, 80.0, 0.3)},
        bench_series=_hist(80, 400.0, 1.0),
    )
    monkeypatch.setattr(watch_signals, "gather_context", lambda now=None, **kw: dict(ctx))
    monkeypatch.setattr(watch_pulse, "_live_stories_by_ticker", lambda: {})
    asked: list[list[str]] = []
    from backend.services import watch_history

    monkeypatch.setattr(
        watch_history, "request", lambda items: asked.append([i["value"] for i in items]) or []
    )
    watch_pulse.build_pulse()
    assert asked == [["NEW", "NOSECTOR"]]
