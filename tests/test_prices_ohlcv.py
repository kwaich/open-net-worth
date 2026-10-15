"""Unit tests for OHLCV chart support (asset-chart-types design).

Covers:
- `_ohlcv_granularity_for_timeframe`: the equity (yfinance interval) / crypto (CoinGecko
  `/ohlc` days-tier) granularity table, incl. the 5Y/MAX `best_effort` cap.
- `_yf_history`: OHLCV fields populated from the SAME yfinance row as Close; the 1D/5D
  interval fallback (1m→5m, 15m→30m) when the primary interval comes back empty.
- `_cg_history`: the CoinGecko merge — close+volume from `/market_chart` (byte-identical to
  the pre-OHLCV series), open/high/low overlaid from `/ohlc` ONLY when it covers the full
  requested window; any OHLC-side failure/insufficient-coverage degrades to a plain
  close(+volume) series, never raises.
- `_cg_ohlc_covers` / `_merge_cg_ohlc`: the coverage check and nearest-timestamp merge, as
  pure functions.
- `asset_detail.price_series`: `ohlc_available` reflects whether EVERY returned point carries
  open/high/low/close — never true for a partially-candled series.

All network calls (yfinance `Ticker.history`, CoinGecko HTTP) are monkeypatched — no real
network access, matching the rest of this file's test siblings (test_price_backfill.py,
test_crypto_chart_currency.py).
"""

from __future__ import annotations

import json

import pandas as pd
import pytest

import backend.core.net_guard as net_guard
from backend.services import asset_detail as ad
from backend.services import prices as prices_mod
from backend.services.prices import (
    _cg_history,
    _cg_ohlc_covers,
    _merge_cg_ohlc,
    _ohlcv_granularity_for_timeframe,
    _yf_history,
)


@pytest.fixture(autouse=True)
def _clean_ohlc_cache():
    """The last-good /ohlc candle cache is module-global and must not leak across tests."""
    prices_mod._cg_ohlc_cache.clear()
    yield
    prices_mod._cg_ohlc_cache.clear()


@pytest.fixture(autouse=True)
def _no_real_dns(monkeypatch):
    """`_cg_history`/`_cg_ohlc_candles` re-import `assert_public_url` from `core.net_guard`
    inside the function body (SSRF guard), which resolves DNS for real. Stub it to a no-op
    pass-through so these tests never touch the network even though the URL itself
    (api.coingecko.com) is legitimately public."""
    monkeypatch.setattr(net_guard, "assert_public_url", lambda u: u)


# ── _ohlcv_granularity_for_timeframe ───────────────────────────────────────────


class TestGranularityTable:
    def test_equity_intervals_match_design_table(self):
        expected = {
            "1D": ("1m", "5m"),
            "5D": ("15m", "30m"),
            "1M": ("1d", None),
            "6M": ("1d", None),
            "YTD": ("1d", None),
            "1Y": ("1d", None),
            "5Y": ("1wk", None),
            "MAX": ("1mo", None),
        }
        for tf, (interval, fallback) in expected.items():
            gran = _ohlcv_granularity_for_timeframe(tf, "equity")
            assert gran["interval"] == interval, tf
            assert gran["fallback_interval"] == fallback, tf

    def test_crypto_days_match_design_table(self):
        # (days requested, best_effort) — 5D rounds UP to CoinGecko's nearest supported
        # /ohlc tier (7, not the literal 5); 5Y/MAX are capped at the free-tier max (365) and
        # marked best_effort since that can't reach the full requested window.
        expected = {
            "1D": (1, False),
            "5D": (7, False),
            "1M": (30, False),
            "6M": (180, False),
            "YTD": (365, False),
            "1Y": (365, False),
            "5Y": (365, True),
            "MAX": (365, True),
        }
        for tf, (days, best_effort) in expected.items():
            gran = _ohlcv_granularity_for_timeframe(tf, "crypto")
            assert gran["cg_days"] == days, tf
            assert gran["best_effort"] == best_effort, tf

    def test_unknown_timeframe_defaults_to_daily_equity(self):
        gran = _ohlcv_granularity_for_timeframe("BOGUS", "equity")
        assert gran["interval"] == "1d"
        assert gran["fallback_interval"] is None

    def test_unknown_timeframe_defaults_to_365d_full_coverage_crypto(self):
        # An unrecognized timeframe falls back to _HISTORY_CG_DAYS's own default (365,
        # matching YTD/1Y) — which /ohlc's free tier covers exactly, so NOT best-effort.
        gran = _ohlcv_granularity_for_timeframe("BOGUS", "crypto")
        assert gran["cg_days"] == 365
        assert gran["best_effort"] is False


# ── _yf_history ─────────────────────────────────────────────────────────────


def _ohlcv_df(rows: list[dict], freq: str = "D") -> pd.DataFrame:
    idx = pd.date_range("2026-06-01", periods=len(rows), freq=freq)
    return pd.DataFrame(rows, index=idx)


class _FakeTicker:
    """Records every `history(period=, interval=)` call and serves canned frames."""

    def __init__(self, frames_by_interval: dict[str, pd.DataFrame]):
        self._frames = frames_by_interval
        self.calls: list[tuple[str, str]] = []

    def history(self, period="1y", interval="1d", prepost=False):
        self.calls.append((period, interval))
        return self._frames.get(interval, pd.DataFrame())


class TestYfHistoryOhlcv:
    def test_ohlcv_fields_populated_for_daily_bar(self, monkeypatch):
        rows = [
            {"Open": 100.0, "High": 105.0, "Low": 99.0, "Close": 103.0, "Volume": 123456},
            {"Open": 103.0, "High": 108.0, "Low": 102.0, "Close": 107.0, "Volume": 98765},
        ]
        ticker = _FakeTicker({"1d": _ohlcv_df(rows)})
        monkeypatch.setattr(prices_mod.yf, "Ticker", lambda s: ticker)

        out = _yf_history("AAPL", "1M")

        assert len(out) == 2
        p0 = out[0]
        assert p0["price"] == pytest.approx(103.0)
        assert p0["open"] == pytest.approx(100.0)
        assert p0["high"] == pytest.approx(105.0)
        assert p0["low"] == pytest.approx(99.0)
        assert p0["close"] == p0["price"]  # close mirrors the price alias
        assert p0["volume"] == pytest.approx(123456)
        assert ticker.calls == [("1mo", "1d")]

    def test_1d_uses_1m_interval(self, monkeypatch):
        rows = [{"Open": 1, "High": 2, "Low": 0.5, "Close": 1.5, "Volume": 10}]
        ticker = _FakeTicker({"1m": _ohlcv_df(rows, freq="min")})
        monkeypatch.setattr(prices_mod.yf, "Ticker", lambda s: ticker)

        out = _yf_history("AAPL", "1D")

        assert ticker.calls == [("1d", "1m")]
        assert len(out) == 1
        # Intraday points carry a full timestamp, not just a date.
        assert "T" in out[0]["date"]

    def test_1d_falls_back_to_5m_when_1m_empty(self, monkeypatch):
        rows = [{"Open": 1, "High": 2, "Low": 0.5, "Close": 1.5, "Volume": 10}]
        ticker = _FakeTicker({"5m": _ohlcv_df(rows, freq="min")})  # no "1m" entry → empty
        monkeypatch.setattr(prices_mod.yf, "Ticker", lambda s: ticker)

        out = _yf_history("THIN.L", "1D")

        assert ticker.calls == [("1d", "1m"), ("1d", "5m")]  # primary tried, then fallback
        assert len(out) == 1

    def test_5d_falls_back_to_30m_when_15m_empty(self, monkeypatch):
        rows = [{"Open": 1, "High": 2, "Low": 0.5, "Close": 1.5, "Volume": 10}]
        ticker = _FakeTicker({"30m": _ohlcv_df(rows, freq="min")})
        monkeypatch.setattr(prices_mod.yf, "Ticker", lambda s: ticker)

        out = _yf_history("THIN.L", "5D")

        assert ticker.calls == [("5d", "15m"), ("5d", "30m")]
        assert len(out) == 1

    def test_5y_uses_weekly_interval(self, monkeypatch):
        ticker = _FakeTicker(
            {"1wk": _ohlcv_df([{"Open": 1, "High": 2, "Low": 0.5, "Close": 1.5, "Volume": 1}])}
        )
        monkeypatch.setattr(prices_mod.yf, "Ticker", lambda s: ticker)

        _yf_history("AAPL", "5Y")

        assert ticker.calls == [("5y", "1wk")]

    def test_max_uses_monthly_interval(self, monkeypatch):
        ticker = _FakeTicker(
            {"1mo": _ohlcv_df([{"Open": 1, "High": 2, "Low": 0.5, "Close": 1.5, "Volume": 1}])}
        )
        monkeypatch.setattr(prices_mod.yf, "Ticker", lambda s: ticker)

        _yf_history("AAPL", "MAX")

        assert ticker.calls == [("max", "1mo")]

    def test_row_with_nan_open_keeps_close_only(self, monkeypatch):
        """A sparse row (fresh IPO's first bar) with NaN Open/High/Low still yields a
        close-only point instead of being dropped or crashing on the NaN."""
        rows = [
            {
                "Open": float("nan"),
                "High": float("nan"),
                "Low": float("nan"),
                "Close": 50.0,
                "Volume": float("nan"),
            }
        ]
        ticker = _FakeTicker({"1d": _ohlcv_df(rows)})
        monkeypatch.setattr(prices_mod.yf, "Ticker", lambda s: ticker)

        out = _yf_history("IPO", "1M")

        assert len(out) == 1
        assert out[0]["price"] == pytest.approx(50.0)
        assert "open" not in out[0]
        assert "volume" not in out[0]

    def test_nan_close_row_dropped_entirely(self, monkeypatch):
        rows = [
            {"Open": 1, "High": 2, "Low": 0.5, "Close": float("nan"), "Volume": 10},
            {"Open": 1, "High": 2, "Low": 0.5, "Close": 1.5, "Volume": 10},
        ]
        ticker = _FakeTicker({"1d": _ohlcv_df(rows)})
        monkeypatch.setattr(prices_mod.yf, "Ticker", lambda s: ticker)

        out = _yf_history("AAPL", "1M")

        assert len(out) == 1  # the NaN-close row never makes it into the series


class TestYfHistoryBackfillDecoupling:
    """backfill_history persists into price_history.json, which CLAUDE.md pins as
    daily-close-only (net worth reconstruction trusts it). The chart's OWN granularity table
    deliberately coarsens 5Y→weekly/MAX→monthly, but backfill_history's default lookback is
    ALSO literally "5Y" — so ohlcv=False must keep requesting daily bars regardless, or the
    persisted series would silently lose density."""

    def test_ohlcv_false_keeps_5y_lookback_daily(self, monkeypatch):
        ticker = _FakeTicker({"1d": _ohlcv_df([{"Close": 100.0}])})
        monkeypatch.setattr(prices_mod.yf, "Ticker", lambda s: ticker)

        out = _yf_history("AAPL", "5Y", ohlcv=False)

        assert ticker.calls == [("5y", "1d")]  # NOT ("5y", "1wk")
        assert out == [{"date": "2026-06-01", "price": 100.0}]  # close-only, no extra fields

    def test_ohlcv_false_keeps_max_lookback_daily(self, monkeypatch):
        ticker = _FakeTicker({"1d": _ohlcv_df([{"Close": 100.0}])})
        monkeypatch.setattr(prices_mod.yf, "Ticker", lambda s: ticker)

        _yf_history("AAPL", "MAX", ohlcv=False)

        assert ticker.calls == [("max", "1d")]  # NOT ("max", "1mo")

    def test_ohlcv_false_drops_open_high_low_volume(self, monkeypatch):
        rows = [{"Open": 1.0, "High": 2.0, "Low": 0.5, "Close": 1.5, "Volume": 10.0}]
        ticker = _FakeTicker({"1d": _ohlcv_df(rows)})
        monkeypatch.setattr(prices_mod.yf, "Ticker", lambda s: ticker)

        out = _yf_history("AAPL", "1M", ohlcv=False)

        assert out == [{"date": "2026-06-01", "price": 1.5}]

    def test_ohlcv_false_1d_never_falls_back(self, monkeypatch):
        """Reproduces the PRE-feature 1D behavior exactly: always 5m, no 1m probe."""
        ticker = _FakeTicker({"5m": _ohlcv_df([{"Close": 1.0}], freq="min")})
        monkeypatch.setattr(prices_mod.yf, "Ticker", lambda s: ticker)

        _yf_history("AAPL", "1D", ohlcv=False)

        assert ticker.calls == [("1d", "5m")]  # single call, no fallback probe


class TestCgHistoryBackfillDecoupling:
    """Mirrors TestYfHistoryBackfillDecoupling for the CoinGecko path: ohlcv=False must skip
    the extra /ohlc network round-trip entirely (record_backfill_series only ever reads
    date/price, so backfill has no use for it and shouldn't pay CoinGecko's rate limit for it)."""

    def test_ohlcv_false_skips_ohlc_endpoint_and_volume(self, monkeypatch):
        market_chart = {
            "prices": [[_epoch_ms("2026-05-01"), 100.0]],
            "total_volumes": [[_epoch_ms("2026-05-01"), 500.0]],
        }

        def _boom():
            raise AssertionError("ohlcv=False must never call the /ohlc endpoint")

        _patch_cg_opener(monkeypatch, market_chart, _boom)

        out = _cg_history("bitcoin", "5Y", "usd", ohlcv=False)

        assert out == [{"date": "2026-05-01", "price": 100.0}]  # no volume, no OHLC


# ── CoinGecko: market_chart + ohlc merge ───────────────────────────────────────


class _Resp:
    def __init__(self, payload: dict | list):
        self._data = json.dumps(payload).encode()

    def read(self, n=None):
        return self._data

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _epoch_ms(date_str: str) -> int:
    import calendar
    import time

    t = time.strptime(date_str, "%Y-%m-%d")
    return calendar.timegm(t) * 1000


def _patch_cg_opener(monkeypatch, market_chart, ohlc):
    """Stub the controlled CoinGecko boundary with market-chart/OHLC fixtures."""

    def fake_get_json(path, params, **kwargs):
        if path.endswith("/market_chart"):
            if callable(market_chart):
                return market_chart()
            return market_chart
        if path.endswith("/ohlc"):
            if callable(ohlc):
                return ohlc()
            return ohlc
        raise AssertionError(f"unexpected path: {path}")

    monkeypatch.setattr(prices_mod.coingecko_client, "get_json", fake_get_json)


class TestCgHistoryMerge:
    def test_full_merge_attaches_ohlc_and_volume(self, monkeypatch):
        # Three aligned daily samples so the /ohlc candles cover exactly the same range as
        # /market_chart (the common case for 1M/6M/YTD/1Y — see the granularity table).
        days = ["2026-05-01", "2026-05-02", "2026-05-03"]
        market_chart = {
            "prices": [[_epoch_ms(d), 100.0 + i] for i, d in enumerate(days)],
            "total_volumes": [[_epoch_ms(d), 1000.0 + i * 10] for i, d in enumerate(days)],
        }
        ohlc = [
            [_epoch_ms(d), 99.0 + i, 105.0 + i, 98.0 + i, 100.0 + i] for i, d in enumerate(days)
        ]
        _patch_cg_opener(monkeypatch, market_chart, ohlc)

        out = _cg_history("bitcoin", "1M", "usd")

        assert len(out) == 3
        for i, p in enumerate(out):
            assert p["date"] == days[i]
            assert p["price"] == pytest.approx(100.0 + i)  # unchanged from market_chart
            assert p["volume"] == pytest.approx(1000.0 + i * 10)  # unchanged from market_chart
            assert p["open"] == pytest.approx(99.0 + i)
            assert p["high"] == pytest.approx(105.0 + i)
            assert p["low"] == pytest.approx(98.0 + i)
            assert p["close"] == pytest.approx(100.0 + i)
            assert "_epoch" not in p  # internal merge key never leaks

    def test_ohlc_endpoint_failure_degrades_to_close_and_volume(self, monkeypatch):
        market_chart = {
            "prices": [[_epoch_ms("2026-05-01"), 100.0]],
            "total_volumes": [[_epoch_ms("2026-05-01"), 500.0]],
        }

        def _boom():
            raise TimeoutError("coingecko unavailable")

        _patch_cg_opener(monkeypatch, market_chart, _boom)

        out = _cg_history("bitcoin", "1M", "usd")

        assert len(out) == 1
        assert out[0]["price"] == pytest.approx(100.0)
        assert out[0]["volume"] == pytest.approx(500.0)
        assert "open" not in out[0]
        assert "high" not in out[0]

    def test_ohlc_empty_response_degrades_gracefully(self, monkeypatch):
        market_chart = {
            "prices": [[_epoch_ms("2026-05-01"), 100.0]],
            "total_volumes": [[_epoch_ms("2026-05-01"), 500.0]],
        }
        _patch_cg_opener(monkeypatch, market_chart, [])  # /ohlc returns an empty list

        out = _cg_history("bitcoin", "1M", "usd")

        assert len(out) == 1
        assert "open" not in out[0]

    def test_ohlc_insufficient_coverage_degrades_for_long_window(self, monkeypatch):
        """Simulates the 5Y/MAX case: /market_chart covers years, but the free-tier /ohlc
        (capped at 365 days) only reaches back a few months — coverage fails, so the whole
        series stays close-only rather than a partially-candled chart."""
        far_back = "2021-01-01"
        recent = "2026-05-01"
        market_chart = {
            "prices": [[_epoch_ms(far_back), 50.0], [_epoch_ms(recent), 100.0]],
            "total_volumes": [[_epoch_ms(far_back), 10.0], [_epoch_ms(recent), 500.0]],
        }
        # /ohlc only has a candle near "recent" — nowhere close to covering "far_back".
        ohlc = [[_epoch_ms(recent), 99.0, 101.0, 98.0, 100.0]]
        _patch_cg_opener(monkeypatch, market_chart, ohlc)

        out = _cg_history("bitcoin", "5Y", "usd")

        assert len(out) == 2
        assert all("open" not in p for p in out)
        # The close-only series itself is untouched by the coverage failure.
        assert out[0]["price"] == pytest.approx(50.0)
        assert out[1]["price"] == pytest.approx(100.0)


class TestCgOhlcCoversPure:
    def test_covers_true_when_ranges_align(self):
        base = [{"_epoch": 0.0}, {"_epoch": 100.0}]
        ohlc = [{"epoch": 0.0}, {"epoch": 100.0}]
        assert _cg_ohlc_covers(base, ohlc) is True

    def test_covers_false_when_ohlc_starts_much_later(self):
        # Mirrors the real 5Y/MAX case: /ohlc's own candles are tightly spaced (small
        # tolerance) but start FAR after the close series' earliest point.
        base = [{"_epoch": 0.0}]
        ohlc = [{"epoch": 5_000_000.0}, {"epoch": 5_100_000.0}]
        assert _cg_ohlc_covers(base, ohlc) is False

    def test_covers_false_when_either_side_empty(self):
        assert _cg_ohlc_covers([], [{"epoch": 0.0}]) is False
        assert _cg_ohlc_covers([{"_epoch": 0.0}], []) is False
        assert _cg_ohlc_covers([], []) is False


class TestMergeCgOhlcPure:
    def test_attaches_nearest_candle_without_touching_price_or_date(self):
        base = [
            {"date": "2026-05-01", "price": 100.0, "volume": 10.0, "_epoch": 0.0},
            {"date": "2026-05-02", "price": 101.0, "volume": 11.0, "_epoch": 86400.0},
        ]
        ohlc = [{"epoch": 0.0, "open": 99.0, "high": 102.0, "low": 98.0, "close": 100.5}]
        _merge_cg_ohlc(base, ohlc)

        assert base[0]["open"] == 99.0
        assert base[1]["open"] == 99.0  # nearest (only) candle shared across both points
        assert base[0]["date"] == "2026-05-01"  # untouched
        assert base[0]["price"] == 100.0  # untouched
        assert base[0]["volume"] == 10.0  # untouched

    def test_noop_when_ohlc_empty(self):
        base = [{"date": "2026-05-01", "price": 100.0, "_epoch": 0.0}]
        _merge_cg_ohlc(base, [])
        assert "open" not in base[0]


# ── asset_detail.price_series: ohlc_available ──────────────────────────────────


class _FakeInstStore:
    def __init__(self, inst):
        self._inst = inst

    def find_by_id(self, iid):
        return self._inst if self._inst.get("id") == iid else None


def _inst(asset_class="equity", price_source="yahoo", price_source_id="AAPL"):
    return {
        "id": "i1",
        "price_source_id": price_source_id,
        "price_source": price_source,
        "quote_currency": "USD",
        "asset_class": asset_class,
    }


class TestPriceSeriesOhlcAvailable:
    def test_true_when_every_point_has_full_ohlc(self, monkeypatch):
        monkeypatch.setattr(ad, "InstrumentStore", lambda: _FakeInstStore(_inst()))
        points = [
            {
                "date": "2026-05-01",
                "price": 100.0,
                "open": 99.0,
                "high": 101.0,
                "low": 98.0,
                "close": 100.0,
                "volume": 10.0,
            },
            {
                "date": "2026-05-02",
                "price": 102.0,
                "open": 100.0,
                "high": 103.0,
                "low": 99.0,
                "close": 102.0,
                "volume": 12.0,
            },
        ]
        monkeypatch.setattr(
            prices_mod,
            "fetch_history_result",
            lambda *a, **k: {
                "points": points,
                "provider": "yahoo",
                "state": "live",
                "as_of": points[-1]["date"],
                "degraded_reason": None,
            },
        )

        out = ad.price_series("i1", "1M")

        assert out["ohlc_available"] is True
        assert out["points"] == points  # passed through unchanged (purely additive)

    def test_false_when_points_are_close_only(self, monkeypatch):
        monkeypatch.setattr(ad, "InstrumentStore", lambda: _FakeInstStore(_inst()))
        points = [{"date": "2026-05-01", "price": 100.0}]
        monkeypatch.setattr(
            prices_mod,
            "fetch_history_result",
            lambda *a, **k: {
                "points": points,
                "provider": "yahoo",
                "state": "live",
                "as_of": points[-1]["date"],
                "degraded_reason": None,
            },
        )

        out = ad.price_series("i1", "1M")

        assert out["ohlc_available"] is False

    def test_false_when_only_some_points_have_ohlc(self, monkeypatch):
        """A partial OHLC series (one point missing open/high/low) must not claim
        ohlc_available=True — a candle chart can't have a hole in the middle."""
        monkeypatch.setattr(ad, "InstrumentStore", lambda: _FakeInstStore(_inst()))
        points = [
            {
                "date": "2026-05-01",
                "price": 100.0,
                "open": 99.0,
                "high": 101.0,
                "low": 98.0,
                "close": 100.0,
            },
            {"date": "2026-05-02", "price": 102.0},  # sparse row, OHLC missing
        ]
        monkeypatch.setattr(
            prices_mod,
            "fetch_history_result",
            lambda *a, **k: {
                "points": points,
                "provider": "yahoo",
                "state": "live",
                "as_of": points[-1]["date"],
                "degraded_reason": None,
            },
        )

        out = ad.price_series("i1", "1M")

        assert out["ohlc_available"] is False

    def test_false_when_no_points(self, monkeypatch):
        monkeypatch.setattr(ad, "InstrumentStore", lambda: _FakeInstStore(_inst()))
        monkeypatch.setattr(
            prices_mod,
            "fetch_history_result",
            lambda *a, **k: {
                "points": [],
                "provider": None,
                "state": "unavailable",
                "as_of": None,
                "degraded_reason": "upstream_unavailable",
            },
        )
        monkeypatch.setattr(
            "backend.db.price_history_store.PriceHistoryStore.series", lambda self, *a, **k: []
        )

        out = ad.price_series("i1", "1M")

        assert out["ohlc_available"] is False
        assert out["points"] == []

    def test_false_for_unknown_instrument(self, monkeypatch):
        monkeypatch.setattr(ad, "InstrumentStore", lambda: _FakeInstStore({"id": "other"}))

        out = ad.price_series("does-not-exist", "1M")

        assert out["ohlc_available"] is False
        assert out["points"] == []


# ── fetch_history cache: CG intraday TTL + stale-serve on failed refresh ──────


class TestCgOhlcStaleReuse:
    """A transient /ohlc failure (429 burst) while market_chart succeeded must NOT ship a
    close-only series when we have recent candles — reuse them (bounded at 30 min, matching
    CoinGecko's own intraday candle cadence); past the bound, degrade for real."""

    @pytest.fixture(autouse=True)
    def _clean_cache(self):
        prices_mod._cg_ohlc_cache.clear()
        yield
        prices_mod._cg_ohlc_cache.clear()

    def _run(self, monkeypatch, candles_or_exc, now):
        days = ["2026-05-01", "2026-05-02", "2026-05-03"]
        market_chart = {
            "prices": [[_epoch_ms(d), 100.0 + i] for i, d in enumerate(days)],
            "total_volumes": [[_epoch_ms(d), 1000.0] for d in days],
        }
        _patch_cg_opener(monkeypatch, market_chart, [])
        if isinstance(candles_or_exc, Exception):

            def _raise(*a, **k):
                raise candles_or_exc

            monkeypatch.setattr(prices_mod, "_cg_ohlc_candles", _raise)
        else:
            monkeypatch.setattr(prices_mod, "_cg_ohlc_candles", lambda *a, **k: candles_or_exc)
        monkeypatch.setattr(prices_mod.time, "monotonic", lambda: now)
        return _cg_history("bitcoin", "1M", "usd")

    def _candles(self):
        days = ["2026-05-01", "2026-05-02", "2026-05-03"]
        return [
            {
                "epoch": _epoch_ms(d) / 1000.0,
                "open": 99.0,
                "high": 105.0,
                "low": 98.0,
                "close": 100.0,
            }
            for d in days
        ]

    def test_stale_candles_reused_through_transient_failure(self, monkeypatch):
        out1 = self._run(monkeypatch, self._candles(), now=1000.0)
        assert all("open" in p for p in out1)
        # 5 min later the /ohlc leg 429s — the cached candles still merge.
        out2 = self._run(monkeypatch, RuntimeError("429"), now=1300.0)
        assert all("open" in p for p in out2)

    def test_degrades_for_real_past_the_staleness_bound(self, monkeypatch):
        self._run(monkeypatch, self._candles(), now=1000.0)
        out = self._run(
            monkeypatch, RuntimeError("429"), now=1000.0 + prices_mod._CG_OHLC_STALE_MAX + 1
        )
        assert all("open" not in p for p in out)
