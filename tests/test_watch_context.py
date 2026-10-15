"""watch_context plumbing — quote symbols, settled history, date-aligned benchmark momentum.
Synthetic data only, no network."""

from __future__ import annotations

from datetime import date, timedelta

import pytest

import backend.services.prices as prices
from backend.services import watch_context as wc
from backend.services.watch_stats import MOMENTUM_WINDOW, history_stats
from backend.services.watch_trend import trend


@pytest.mark.parametrize(
    ("item", "insts", "want"),
    [
        ({"value": "BTC", "asset_class": "crypto"}, None, "BTC-USD"),
        ({"value": "eth", "asset_class": "crypto"}, None, "ETH-USD"),
        ({"value": "BTC-USD", "asset_class": "crypto"}, None, "BTC-USD"),
        ({"value": "NVDA", "asset_class": "equity"}, None, "NVDA"),
        (
            {"value": "D05", "instrument_id": "i1"},
            {"i1": {"price_source": "yahoo", "price_source_id": "D05.SI"}},
            "D05.SI",
        ),
        (
            {"value": "SOL", "instrument_id": "i2"},
            {
                "i2": {
                    "price_source": "coingecko",
                    "price_source_id": "solana",
                    "asset_class": "crypto",
                }
            },
            "SOL-USD",
        ),
    ],
)
def test_a_watched_coin_is_quoted_as_the_coin_never_the_us_trust(item, insts, want):
    assert wc.quote_symbol_for(item, insts or {}) == want


def test_fill_market_context_maps_each_ticker_back_from_its_quote_symbol():
    ctx: dict = {"bench": None}
    snaps = {
        "BTC-USD": {"price": 60000.0, "prev_close": 59000.0},
        "SPY": {"price": 101.0, "prev_close": 100.0, "session_date": "2026-09-25"},
    }
    wc.fill_market_context(ctx, snaps, {"BTC": "BTC-USD", "SPY": "SPY"}, ("SPY", "SPY"), [])
    assert set(ctx["snaps"]) == {"BTC", "SPY"}
    assert ctx["bench"]["price"] == 101.0 and ctx["bench_session"] == "2026-09-25"


def test_settled_drops_the_bar_of_the_quotes_own_session():
    series = [("2026-09-24", 10.0), ("2026-09-25", 11.0), ("2026-09-26", 12.0)]
    assert wc.settled(series, "2026-09-25") == [("2026-09-24", 10.0)]
    assert wc.settled(series, None) == series


def test_snapshot_carries_the_exchange_local_session_date():
    # 2026-09-25 20:00 UTC is 16:00 in New York, and already 2026-09-26 in Singapore.
    snap = prices._snapshot_from_quote(
        {
            "regularMarketPrice": 10.0,
            "regularMarketTime": 1790366400,
            "exchangeTimezoneName": "America/New_York",
        }
    )
    assert snap["session_date"] == "2026-09-25"


def test_backfill_keeps_only_settled_bars(monkeypatch):
    stored: list[list[dict]] = []

    class _Store:
        def record_backfill_series(self, key, points):
            stored.append(points)
            return len(points)

    bars = [{"date": "2026-09-24", "price": 1.0}, {"date": "2026-09-25", "price": 1.1}]
    monkeypatch.setattr(prices, "_yf_history", lambda *a, **k: bars)
    monkeypatch.setattr("backend.db.price_history_store.PriceHistoryStore", _Store)
    assert prices.backfill_symbol_history("AMD", lookback="1M", before="2026-09-25") == 1
    assert stored == [[bars[0]]]


def _weekday_closes(n: int, start: float, step: float) -> list[tuple[str, float]]:
    out, d, px = [], date(2026, 9, 24), start
    while len(out) < n:
        if d.weekday() < 5:
            out.append((d.isoformat(), px))
            px -= step
        d -= timedelta(days=1)
    return sorted(out)


def _daily_closes(n: int, start: float, step: float) -> list[tuple[str, float]]:
    d0 = date(2026, 9, 24)
    return sorted(((d0 - timedelta(days=i)).isoformat(), start - i * step) for i in range(n))


def test_month_vs_benchmark_lines_up_by_date_with_a_seven_day_benchmark():
    """BTC trades every day, the stock only on weekdays: the flag and the trend caption must
    compare the same start date, so they agree to the cent."""
    stock = _weekday_closes(120, 100.0, 0.1)
    btc = _daily_closes(200, 50000.0, 20.0)
    stats = history_stats(
        stock, price=100.0, day_pct=0.5, bench_series=btc, bench_day=1.0, bench_price=50100.0
    )
    line = trend(stock, price=100.0, bench_series=btc, bench_day=1.0, bench_price=50100.0)
    assert stats is not None and line is not None and line["1M"] is not None
    assert stats["mom_1m_vs_bench"] == pytest.approx(line["1M"]["vs_bench_pct"], abs=0.01)
    start = dict(btc)[stock[-MOMENTUM_WINDOW][0]]
    want = (100.0 / stock[-MOMENTUM_WINDOW][1] - 1) * 100 - (50100.0 / start - 1) * 100
    assert stats["mom_1m_vs_bench"] == pytest.approx(want, abs=0.01)


def test_a_symbol_with_no_sector_is_not_asked_again_on_every_top_up(monkeypatch):
    asked: list[str] = []
    monkeypatch.setattr(prices, "_SECTOR_MISSES", {})
    monkeypatch.setattr(prices, "_fetch_sector", lambda sym: asked.append(sym) or "")
    assert prices.ensure_sector("VWRA.L") == ""
    assert prices.ensure_sector("VWRA.L") == ""
    assert asked == ["VWRA.L"]
