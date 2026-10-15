"""Regression tests for the 1D-chart previous-session fallback.

Root cause (owner-reported, KSTL.DE/XETRA before the regular session opens): Yahoo's chart
API returns ZERO bars for BOTH the 1m and 5m `_yf_history` 1D probes on a non-extended-hours
venue viewed before its session opens (or shortly after it closes) — unlike Yahoo's own web
frontend, which quietly draws the prior session instead of erroring. Covers:

- `_yf_previous_session_bars`: widens to a trailing week, selects ONLY the most recent
  session that isn't today's, with the same 1m->5m fallback chain `_yf_history` uses.
- `fetch_history_result`: wires the fallback in for 1D/non-crypto only, flags it via
  `degraded_reason: "previous_session"` + `session_date` rather than presenting it as live,
  and preserves the original "genuinely no data anywhere" unavailable behavior when even the
  widened fetch comes up empty.
- `asset_detail.price_series`: passes `session_date` through to the API response.

See ADR docs/adr/2026-09-08-1d-chart-falls-back-to-the-previous-session.md.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from backend.core.errors import ValidationError
from backend.services import asset_detail as ad
from backend.services import prices as prices_mod
from backend.services.prices import (
    _assert_yahoo_symbol,
    _yf_history,
    _yf_previous_session_bars,
    fetch_history_result,
)

BERLIN = ZoneInfo("Europe/Berlin")


@pytest.fixture(autouse=True)
def _clean_caches():
    """Both the live-fetch cache and the previous-session cache are module-global."""
    prices_mod._history_cache.clear()
    prices_mod._prev_session_cache.clear()
    yield
    prices_mod._history_cache.clear()
    prices_mod._prev_session_cache.clear()


def _bars(day, hours: list[str], tz=BERLIN) -> pd.DataFrame:
    idx = pd.DatetimeIndex([pd.Timestamp(f"{day} {h}", tz=tz) for h in hours])
    n = len(hours)
    return pd.DataFrame(
        {
            "Open": [1.0] * n,
            "High": [2.0] * n,
            "Low": [0.5] * n,
            "Close": [1.5] * n,
            "Volume": [10.0] * n,
        },
        index=idx,
    )


class _FakeWideTicker:
    """Records every `.history(period=, interval=, prepost=)` call; serves canned frames
    keyed by interval — `_yf_previous_session_bars` always requests `period="5d"`."""

    def __init__(self, frames_by_interval: dict[str, pd.DataFrame]):
        self._frames = frames_by_interval
        self.calls: list[tuple[str, str, bool]] = []

    def history(self, period="1y", interval="1d", prepost=False):
        self.calls.append((period, interval, prepost))
        return self._frames.get(interval, pd.DataFrame())


# ── _yf_previous_session_bars ──────────────────────────────────────────────────


class TestYfPreviousSessionBars:
    def test_selects_only_the_most_recent_complete_session(self, monkeypatch):
        now = datetime.now(BERLIN)
        today = now.date()
        yesterday = today - timedelta(days=1)
        frame = pd.concat(
            [
                _bars(yesterday, ["09:00", "09:01"]),
                _bars(today, ["08:55"]),  # a stray pre-open print for TODAY — must be excluded
            ]
        )
        ticker = _FakeWideTicker({"1m": frame})
        monkeypatch.setattr(prices_mod.yf, "Ticker", lambda s: ticker)

        points, session_date = _yf_previous_session_bars("KSTL.DE")

        assert session_date == yesterday.isoformat()
        assert len(points) == 2
        assert ticker.calls == [("5d", "1m", True)]

    def test_falls_back_to_5m_when_1m_widened_probe_is_empty(self, monkeypatch):
        yesterday = datetime.now(BERLIN).date() - timedelta(days=1)
        frame = _bars(yesterday, ["09:00"])
        ticker = _FakeWideTicker({"5m": frame})  # no "1m" entry -> empty
        monkeypatch.setattr(prices_mod.yf, "Ticker", lambda s: ticker)

        points, session_date = _yf_previous_session_bars("THIN.L")

        assert ticker.calls == [("5d", "1m", True), ("5d", "5m", True)]
        assert session_date == yesterday.isoformat()
        assert len(points) == 1

    def test_no_session_older_than_today_returns_none(self, monkeypatch):
        today = datetime.now(BERLIN).date()
        frame = _bars(today, ["08:55"])  # only today's stray print, both intervals
        ticker = _FakeWideTicker({"1m": frame, "5m": frame})
        monkeypatch.setattr(prices_mod.yf, "Ticker", lambda s: ticker)

        points, session_date = _yf_previous_session_bars("FRESHIPO.DE")

        assert points == []
        assert session_date is None

    def test_completely_empty_widened_fetch_returns_none(self, monkeypatch):
        ticker = _FakeWideTicker({})
        monkeypatch.setattr(prices_mod.yf, "Ticker", lambda s: ticker)

        points, session_date = _yf_previous_session_bars("DELISTED.DE")

        assert points == []
        assert session_date is None
        assert ticker.calls == [("5d", "1m", True), ("5d", "5m", True)]


# ── fetch_history_result ───────────────────────────────────────────────────────


class TestFetchHistoryResultPreviousSessionFallback:
    def test_falls_back_to_previous_session_when_1d_live_fetch_is_empty(self, monkeypatch):
        monkeypatch.setattr(prices_mod, "_yf_history", lambda *a, **k: [])
        fake_points = [
            {"date": "2026-09-04T09:00:00+02:00", "price": 36.75},
            {"date": "2026-09-04T09:01:00+02:00", "price": 36.8},
        ]
        monkeypatch.setattr(
            prices_mod, "_yf_previous_session_bars", lambda symbol: (fake_points, "2026-09-04")
        )

        result = fetch_history_result("KSTL.DE", "1D", "yahoo")

        assert result["state"] == "stale"
        assert result["degraded_reason"] == "previous_session"
        assert result["session_date"] == "2026-09-04"
        assert result["provider"] == "yahoo"
        assert result["points"] == fake_points
        assert result["as_of"] == fake_points[-1]["date"]

    def test_stays_unavailable_when_there_is_no_prior_session_either(self, monkeypatch):
        """The genuinely-no-data-anywhere case (e.g. a delisted symbol): the existing
        "unavailable" behavior from before this fallback existed is preserved exactly."""
        monkeypatch.setattr(prices_mod, "_yf_history", lambda *a, **k: [])
        monkeypatch.setattr(prices_mod, "_yf_previous_session_bars", lambda symbol: ([], None))

        result = fetch_history_result("DELISTED.DE", "1D", "yahoo")

        assert result == {
            "provider": None,
            "state": "unavailable",
            "as_of": None,
            "degraded_reason": "upstream_unavailable",
            "points": [],
        }

    def test_never_widens_for_non_1d_timeframes(self, monkeypatch):
        monkeypatch.setattr(prices_mod, "_yf_history", lambda *a, **k: [])
        called: list[str] = []
        monkeypatch.setattr(
            prices_mod,
            "_yf_previous_session_bars",
            lambda symbol: (called.append(symbol), ([], None))[1],
        )

        result = fetch_history_result("AAPL", "1M", "yahoo")

        assert called == []
        assert result["degraded_reason"] == "upstream_unavailable"

    def test_never_widens_for_crypto(self, monkeypatch):
        called: list[str] = []
        monkeypatch.setattr(
            prices_mod,
            "_yf_previous_session_bars",
            lambda symbol: (called.append(symbol), ([], None))[1],
        )
        monkeypatch.setattr(
            "backend.services.crypto_market_data.fetch_crypto_history",
            lambda *a, **k: {
                "provider": None,
                "state": "unavailable",
                "as_of": None,
                "degraded_reason": "no_data",
                "points": [],
            },
        )

        fetch_history_result("bitcoin", "1D", "coingecko")

        assert called == []

    def test_real_bars_win_over_the_previous_session_when_present(self, monkeypatch):
        """A transient live-fetch failure with REAL cached intraday bars from earlier today
        must still serve those (the existing stale-but-real path), never fall through to
        yesterday's session — today's own (if stale) data always outranks yesterday's. The
        cache entry is stamped OUTSIDE the fresh-hit TTL so the function actually attempts
        (and fails) a live refetch, exercising the stale-but-real branch rather than the
        fresh-cache-hit fast path."""
        import time as _time

        key = ("KSTL.DE", "1D", "usd")
        prices_mod._history_cache[key] = (
            _time.monotonic() - prices_mod._HISTORY_TTL_1D - 1,
            [{"date": datetime.now(BERLIN).replace(microsecond=0).isoformat(), "price": 40.0}],
        )
        monkeypatch.setattr(prices_mod, "_yf_history", lambda *a, **k: [])
        called: list[str] = []
        monkeypatch.setattr(
            prices_mod,
            "_yf_previous_session_bars",
            lambda symbol: (called.append(symbol), ([], None))[1],
        )

        result = fetch_history_result("KSTL.DE", "1D", "yahoo")

        assert result["degraded_reason"] == "upstream_unavailable"
        assert called == []

    def test_a_tape_cached_during_an_earlier_session_is_not_an_outage(self, monkeypatch):
        """Owner-reported (KSTL.DE, pre-open): yesterday's 1D tape still in `_history_cache`
        overnight + the routine empty pre-open refresh was served as `upstream_unavailable`,
        drawing the "Saved data — live providers are temporarily unavailable" banner every
        morning. It must go through the silent previous-session path instead."""
        import time as _time

        yesterday = (datetime.now(BERLIN) - timedelta(days=1)).replace(hour=17, minute=35)
        prices_mod._history_cache[("KSTL.DE", "1D", "usd")] = (
            _time.monotonic() - prices_mod._HISTORY_TTL_1D - 1,
            [{"date": yesterday.isoformat(), "price": 36.95}],
        )
        monkeypatch.setattr(prices_mod, "_yf_history", lambda *a, **k: [])
        full = [{"date": yesterday.isoformat(), "price": 36.95}]
        monkeypatch.setattr(
            prices_mod,
            "_yf_previous_session_bars",
            lambda symbol: (full, yesterday.date().isoformat()),
        )

        result = fetch_history_result("KSTL.DE", "1D", "yahoo")

        assert result["degraded_reason"] == "previous_session"
        assert result["points"] == full

    def test_an_earlier_session_tape_still_serves_stale_when_the_widened_fetch_fails(
        self, monkeypatch
    ):
        import time as _time

        yesterday = (datetime.now(BERLIN) - timedelta(days=1)).replace(hour=17, minute=35)
        cached = [{"date": yesterday.isoformat(), "price": 36.95}]
        prices_mod._history_cache[("KSTL.DE", "1D", "usd")] = (
            _time.monotonic() - prices_mod._HISTORY_TTL_1D - 1,
            cached,
        )
        monkeypatch.setattr(prices_mod, "_yf_history", lambda *a, **k: [])
        monkeypatch.setattr(prices_mod, "_yf_previous_session_bars", lambda symbol: ([], None))

        result = fetch_history_result("KSTL.DE", "1D", "yahoo")

        assert result["degraded_reason"] == "upstream_unavailable"
        assert result["points"] == cached

    def test_previous_session_fetch_is_memoized_across_polls(self, monkeypatch):
        monkeypatch.setattr(prices_mod, "_yf_history", lambda *a, **k: [])
        calls: list[str] = []

        def fake_widen(symbol):
            calls.append(symbol)
            return [{"date": "2026-09-04T09:00:00Z", "price": 36.75}], "2026-09-04"

        monkeypatch.setattr(prices_mod, "_yf_previous_session_bars", fake_widen)

        first = fetch_history_result("KSTL.DE", "1D", "yahoo")
        second = fetch_history_result("KSTL.DE", "1D", "yahoo")

        assert calls == ["KSTL.DE"]  # the second call is served from _prev_session_cache
        assert first["degraded_reason"] == second["degraded_reason"] == "previous_session"

    def test_negative_previous_session_result_is_also_cached(self, monkeypatch):
        """Security review 2026-09-08: an uncached NEGATIVE result (no prior session either)
        meant every single poll re-issued the widened Yahoo fetch, uncapped — the exact gap
        `test_previous_session_fetch_is_memoized_across_polls` above didn't cover, since it
        only exercises the success path."""
        monkeypatch.setattr(prices_mod, "_yf_history", lambda *a, **k: [])
        calls: list[str] = []

        def fake_widen(symbol):
            calls.append(symbol)
            return [], None

        monkeypatch.setattr(prices_mod, "_yf_previous_session_bars", fake_widen)

        first = fetch_history_result("DELISTED.DE", "1D", "yahoo")
        second = fetch_history_result("DELISTED.DE", "1D", "yahoo")

        assert calls == ["DELISTED.DE"]  # the second call must NOT re-widen
        assert first["degraded_reason"] == second["degraded_reason"] == "upstream_unavailable"

    def test_negative_result_is_cached_even_when_the_widened_fetch_raises(self, monkeypatch):
        monkeypatch.setattr(prices_mod, "_yf_history", lambda *a, **k: [])
        calls: list[str] = []

        def boom(symbol):
            calls.append(symbol)
            raise RuntimeError("Yahoo 429")

        monkeypatch.setattr(prices_mod, "_yf_previous_session_bars", boom)

        fetch_history_result("RATE-LIMITED.DE", "1D", "yahoo")
        fetch_history_result("RATE-LIMITED.DE", "1D", "yahoo")

        assert calls == ["RATE-LIMITED.DE"]

    def test_the_live_fetch_back_off_window_never_triggers_a_fresh_widened_fetch(self, monkeypatch):
        """The HIGH-severity gap: `fetch_history_result`'s cached-EMPTY-live-result back-off
        branch (the one bounding retries to `_HISTORY_FAIL_TTL`) must consult ONLY an
        already-populated `_prev_session_cache` entry — issuing a fresh widened fetch from
        inside that branch defeats its entire purpose (a request that used to cost 0
        outbound calls would cost up to 2 on every poll within the back-off window,
        tripling the request rate exactly when Yahoo is already rate-limiting)."""
        import time as _time

        key = ("STILL-BACKING-OFF.DE", "1D", "usd")
        # A fresh cached-empty live result, well inside _HISTORY_FAIL_TTL.
        prices_mod._history_cache[key] = (_time.monotonic(), [])
        calls: list[str] = []
        monkeypatch.setattr(
            prices_mod,
            "_yf_previous_session_bars",
            lambda symbol: (calls.append(symbol), ([], None))[1],
        )

        result = fetch_history_result("STILL-BACKING-OFF.DE", "1D", "yahoo")

        assert calls == []  # the back-off branch never widens
        assert result["state"] == "unavailable"

    def test_the_live_fetch_back_off_window_still_serves_an_already_cached_fallback(
        self, monkeypatch
    ):
        """The flip side: `allow_fetch=False` must still SERVE an existing
        `_prev_session_cache` entry from an earlier (allowed) fetch — it only refuses to
        trigger a NEW one."""
        import time as _time

        symbol = "ALREADY-CACHED.DE"
        key = (symbol, "1D", "usd")
        prices_mod._history_cache[key] = (_time.monotonic(), [])
        prices_mod._prev_session_cache[symbol] = (
            _time.monotonic(),
            [{"date": "2026-09-04T09:00:00Z", "price": 36.75}],
            "2026-09-04",
        )
        calls: list[str] = []
        monkeypatch.setattr(
            prices_mod,
            "_yf_previous_session_bars",
            lambda s: (calls.append(s), ([], None))[1],
        )

        result = fetch_history_result(symbol, "1D", "yahoo")

        assert calls == []  # served from cache, never re-fetched
        assert result["degraded_reason"] == "previous_session"
        assert result["session_date"] == "2026-09-04"

    def test_prev_session_cache_is_bounded(self):
        prices_mod._prev_session_cache.clear()
        cap = prices_mod._PREV_SESSION_CACHE_MAX
        for i in range(cap + 50):
            prices_mod._prev_session_cache_set(
                f"SYM{i}", [{"date": "2026-09-04", "price": 1.0}], "2026-09-04"
            )

        assert len(prices_mod._prev_session_cache) <= cap


# ── asset_detail.price_series passthrough ───────────────────────────────────────


class _FakeInstStore:
    def __init__(self, inst):
        self._inst = inst

    def find_by_id(self, iid):
        return self._inst if self._inst.get("id") == iid else None


class TestPriceSeriesSessionDatePassthrough:
    def test_session_date_flows_through_to_the_response(self, monkeypatch):
        monkeypatch.setattr(
            ad,
            "InstrumentStore",
            lambda: _FakeInstStore(
                {
                    "id": "i1",
                    "price_source_id": "KSTL.DE",
                    "price_source": "yahoo",
                    "quote_currency": "EUR",
                    "asset_class": "equity",
                }
            ),
        )
        points = [{"date": "2026-09-04T09:00:00+02:00", "price": 36.75}]
        monkeypatch.setattr(
            prices_mod,
            "fetch_history_result",
            lambda *a, **k: {
                "points": points,
                "provider": "yahoo",
                "state": "stale",
                "as_of": points[-1]["date"],
                "degraded_reason": "previous_session",
                "session_date": "2026-09-04",
            },
        )

        out = ad.price_series("i1", "1D")

        assert out["session_date"] == "2026-09-04"
        assert out["degraded_reason"] == "previous_session"

    def test_session_date_absent_when_not_a_fallback_result(self, monkeypatch):
        monkeypatch.setattr(
            ad,
            "InstrumentStore",
            lambda: _FakeInstStore(
                {
                    "id": "i1",
                    "price_source_id": "AAPL",
                    "price_source": "yahoo",
                    "quote_currency": "USD",
                    "asset_class": "equity",
                }
            ),
        )
        points = [{"date": "2026-09-04", "price": 200.0}]
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

        assert out.get("session_date") is None


# ── _assert_yahoo_symbol ─────────────────────────────────────────────────────────


class TestAssertYahooSymbol:
    @pytest.mark.parametrize(
        "symbol",
        ["MSFT", "KSTL.DE", "BRK.B", "^GSPC", "EURUSD=X", "BTC-USD", "A" * 20],
    )
    def test_accepts_normal_yahoo_symbols(self, symbol):
        _assert_yahoo_symbol(symbol)  # must not raise

    @pytest.mark.parametrize(
        "symbol",
        [
            "",
            "A" * 21,  # over length
            "MSFT; rm -rf /",
            "../../etc/passwd",
            "MSFT\n",
            "MSFT/../x",
            "sym bol",
            "sym\x00bol",
        ],
    )
    def test_rejects_anything_outside_the_allowed_shape(self, symbol):
        with pytest.raises(ValidationError):
            _assert_yahoo_symbol(symbol)

    def test_yf_history_refuses_a_malformed_symbol_before_touching_the_network(self, monkeypatch):
        def boom(_symbol):
            raise AssertionError("yf.Ticker must never be reached for a malformed symbol")

        monkeypatch.setattr(prices_mod.yf, "Ticker", boom)

        with pytest.raises(ValidationError):
            _yf_history("MSFT; DROP TABLE", "1M")

    def test_yf_previous_session_bars_refuses_a_malformed_symbol_before_touching_the_network(
        self, monkeypatch
    ):
        def boom(_symbol):
            raise AssertionError("yf.Ticker must never be reached for a malformed symbol")

        monkeypatch.setattr(prices_mod.yf, "Ticker", boom)

        with pytest.raises(ValidationError):
            _yf_previous_session_bars("MSFT; DROP TABLE")

    def test_fetch_history_result_degrades_to_unavailable_rather_than_crash_on_a_bad_symbol(self):
        """`fetch_history_result` already wraps `_yf_history` in a broad except — a
        `ValidationError` from the new guard is caught the same way any other upstream
        failure is, so a hostile/malformed price_source_id degrades gracefully instead of
        raising through to the API layer."""
        result = fetch_history_result("MSFT; DROP TABLE", "1M", "yahoo")

        assert result["state"] == "unavailable"
        assert result["points"] == []
