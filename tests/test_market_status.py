"""
Market-status seam + market-hours gating (deterministic; no network).

Covers:
- _quote_to_market: classified from ONE batched v7 quote (marketState) + a FRESHNESS gate — a
  PRE/POST is honoured only when the quote actually updated recently, else CLOSED (Yahoo keeps
  saying POST through XETRA's phantom post window and the sticky overnight, with a frozen price).
- _latest_quote_epoch: most-recent trade timestamp across regular/pre/post.
- _parse_chart_next_open: holiday-accurate earliest FUTURE session start (reopens countdown only).
- market_status: crypto always-open; equities via the batched quote; unresolved omitted (fail-open);
  with_reopens=False skips the per-symbol chart call (the gate's hot path).
- fetch_live: batched v7 path skips per-ticker; an empty/failed batch falls back per-ticker.
- _trading_day_map / fetch_live's prior-close anchor: each ticker's OWN exchange trading-day
  date (2026-08-11 PLTR incident — a single shared "today" boundary is wrong whenever an
  exchange's session straddles it).
- price_monitor.run_price_check market-hours gate: skips closed markets, polls only open ones.
"""

from __future__ import annotations

import asyncio
import importlib
import time
from datetime import datetime, timezone

import pytest

from backend.services import prices as p

_NOW = datetime.now(timezone.utc).timestamp()


def _q(state, **extra) -> dict:
    """A v7 quote with sane NY-market metadata; override timestamps/fields via kwargs."""
    q = {
        "marketState": state,
        "fullExchangeName": "NasdaqGS",
        "exchangeTimezoneName": "America/New_York",
        "exchangeTimezoneShortName": "EDT",
        "gmtOffSetMilliseconds": -14400000,
    }
    q.update(extra)
    return q


# ── Yahoo response lifecycle ────────────────────────────────────────────────────


class _ClosingResponse:
    def __init__(self, payload=None, error: Exception | None = None):
        self.payload = payload
        self.error = error
        self.closed = False

    def json(self):
        if self.error is not None:
            raise self.error
        return self.payload

    def close(self):
        self.closed = True


def test_yf_batch_quote_closes_response_after_success(monkeypatch):
    response = _ClosingResponse({"quoteResponse": {"result": [{"symbol": "AAPL"}]}})

    class _FakeYfData:
        def _get_crumb_basic(self):
            return "crumb"

        def get(self, url):
            return response

    monkeypatch.setattr(importlib.import_module("yfinance.data"), "YfData", _FakeYfData)

    assert p._yf_batch_quote(["AAPL"]) == {"AAPL": {"symbol": "AAPL"}}
    assert response.closed


def test_yf_batch_quote_closes_response_when_json_decode_fails(monkeypatch):
    response = _ClosingResponse(error=ValueError("bad upstream JSON"))

    class _FakeYfData:
        def _get_crumb_basic(self):
            return "crumb"

        def get(self, url):
            return response

    monkeypatch.setattr(importlib.import_module("yfinance.data"), "YfData", _FakeYfData)

    assert p._yf_batch_quote(["AAPL"]) == {}
    assert response.closed


def test_fetch_chart_closes_response(monkeypatch):
    response = _ClosingResponse({"chart": {"result": []}})

    class _FakeYfData:
        def get(self, url, timeout):
            return response

    monkeypatch.setattr(importlib.import_module("yfinance.data"), "YfData", _FakeYfData)
    p._chart_cache.clear()

    assert p._fetch_chart("AAPL") == {"chart": {"result": []}}
    assert response.closed


def test_fetch_chart_requests_an_intraday_interval(monkeypatch):
    """meta.tradingPeriods (the session the returned bars belong to — what session_bounds
    reads) is only present on an INTRADAY interval; `interval=1d` silently drops it and every
    other test here would still pass via the currentTradingPeriod fallback."""
    urls: list[str] = []

    class _FakeYfData:
        def get(self, url, timeout):
            urls.append(url)
            return _ClosingResponse({"chart": {"result": []}})

    monkeypatch.setattr(importlib.import_module("yfinance.data"), "YfData", _FakeYfData)
    p._chart_cache.clear()
    p._fetch_chart("AAPL")
    assert len(urls) == 1
    assert "interval=1h" in urls[0] and "range=1d" in urls[0] and "includePrePost=true" in urls[0]


def test_fetch_chart_failure_is_retried_after_the_short_ttl_not_the_full_20_min(monkeypatch):
    """Owner-reproduced live bug, 2026-09-20: a single transient Yahoo timeout cached `None`
    for the full 20-minute success TTL, silently dropping session_bounds and blanking the 1D
    chart's pre/post-market dotted bands for that whole window ("why don't I see the dots
    anymore"). A failed fetch must self-heal on a much shorter clock than a real success."""
    calls = {"n": 0}

    class _FakeYfData:
        def get(self, url, timeout):
            calls["n"] += 1
            return _ClosingResponse({"chart": {"result": [{"meta": {"symbol": "AAPL"}}]}})

    monkeypatch.setattr(importlib.import_module("yfinance.data"), "YfData", _FakeYfData)
    p._chart_cache.clear()

    # A failure just inside the short TTL is still served from cache — no retry yet.
    p._chart_cache["AAPL"] = (time.monotonic() - (p._CHART_CACHE_FAILURE_TTL - 5), None)
    assert p._fetch_chart("AAPL") is None
    assert calls["n"] == 0

    # Past the short TTL — but well within the long success TTL — a failure retries.
    p._chart_cache["AAPL"] = (time.monotonic() - (p._CHART_CACHE_FAILURE_TTL + 5), None)
    assert p._fetch_chart("AAPL") == {"chart": {"result": [{"meta": {"symbol": "AAPL"}}]}}
    assert calls["n"] == 1


def test_fetch_chart_success_still_uses_the_long_ttl(monkeypatch):
    """The failure TTL must never shorten a genuine success's cache lifetime."""
    calls = {"n": 0}

    class _FakeYfData:
        def get(self, url, timeout):
            calls["n"] += 1
            return _ClosingResponse({"chart": {"result": []}})

    monkeypatch.setattr(importlib.import_module("yfinance.data"), "YfData", _FakeYfData)
    p._chart_cache.clear()
    # Older than the failure TTL, but still well inside the 20-min success TTL.
    p._chart_cache["AAPL"] = (
        time.monotonic() - (p._CHART_CACHE_FAILURE_TTL + 5),
        {"chart": {"result": []}},
    )
    assert p._fetch_chart("AAPL") == {"chart": {"result": []}}
    assert calls["n"] == 0


def _payload(own_regular_start: int, current_pre_start: int) -> dict:
    """A chart payload whose BARS belong to the session starting `own_regular_start`
    (meta.tradingPeriods) while meta.currentTradingPeriod already names the next one."""
    day = 86_400
    return {
        "chart": {
            "result": [
                {
                    "meta": {
                        "symbol": "MSFT",
                        "tradingPeriods": {
                            "pre": [
                                [{"start": own_regular_start - 19_800, "end": own_regular_start}]
                            ],
                            "regular": [
                                [{"start": own_regular_start, "end": own_regular_start + 23_400}]
                            ],
                            "post": [
                                [
                                    {
                                        "start": own_regular_start + 23_400,
                                        "end": own_regular_start + 37_800,
                                    }
                                ]
                            ],
                        },
                        "currentTradingPeriod": {
                            "pre": {"start": current_pre_start, "end": current_pre_start + 19_800},
                            "regular": {
                                "start": current_pre_start + 19_800,
                                "end": current_pre_start + 43_200,
                            },
                            "post": {
                                "start": current_pre_start + 43_200,
                                "end": current_pre_start + 57_600,
                            },
                        },
                        "_day": day,
                    }
                }
            ]
        }
    }


def test_fetch_chart_expires_a_cached_payload_once_the_next_session_has_begun(monkeypatch):
    """Owner-reproduced live bug, 2026-09-21, MSFT at 04:02 ET Monday: a payload cached at
    03:5x still described FRIDAY's session (its bars' own tradingPeriods) for the full 20-min
    TTL, while the 1-minute tape and the live print were already Monday's — the 1D chart drew
    Friday's window extended to Monday's first print ("pre-market on the right side instead of
    left"), and one poll earlier, with no valued bar in that window at all, crashed the page.
    Once the session named by currentTradingPeriod has BEGUN, the cached payload is served
    only on the short (failure) clock so the bounds catch up within a minute."""
    fri_open = 1_789_738_200  # Fri 2026-09-18 09:30 ET
    mon_pre = 1_789_977_600  # Mon 2026-09-21 04:00 ET
    stale = _payload(fri_open, mon_pre)
    calls = {"n": 0}

    class _FakeYfData:
        def get(self, url, timeout):
            calls["n"] += 1
            return _ClosingResponse(_payload(mon_pre + 19_800, mon_pre))

    monkeypatch.setattr(importlib.import_module("yfinance.data"), "YfData", _FakeYfData)
    p._chart_cache.clear()
    # Two minutes into the cache's life — deep inside the 20-min success TTL either way.
    p._chart_cache["MSFT"] = (time.monotonic() - 120, stale)

    # 03:58 ET: Monday's pre-market hasn't started — Friday's payload is still the truth.
    assert p._fetch_chart("MSFT", now_epoch=mon_pre - 120) is stale
    assert calls["n"] == 0

    # 04:02 ET: the next session has begun → the stale payload is past its (short) clock.
    fresh = p._fetch_chart("MSFT", now_epoch=mon_pre + 120)
    assert calls["n"] == 1
    assert p._parse_trading_periods(fresh)["regular"]["start"].startswith("2026-09-21")


def test_fetch_chart_stale_by_roll_still_honours_the_short_ttl(monkeypatch):
    """Yahoo itself lags the roll by a few minutes: a refetch can come back STILL describing
    the old session. That payload is not refetched on every call — it sits on the short
    failure clock, not a hot loop."""
    fri_open = 1_789_738_200
    mon_pre = 1_789_977_600
    calls = {"n": 0}

    class _FakeYfData:
        def get(self, url, timeout):
            calls["n"] += 1
            return _ClosingResponse(_payload(fri_open, mon_pre))

    monkeypatch.setattr(importlib.import_module("yfinance.data"), "YfData", _FakeYfData)
    p._chart_cache.clear()
    p._chart_cache["MSFT"] = (time.monotonic() - 10, _payload(fri_open, mon_pre))
    assert p._fetch_chart("MSFT", now_epoch=mon_pre + 120)["chart"]  # served from cache
    assert calls["n"] == 0
    p._chart_cache["MSFT"] = (
        time.monotonic() - (p._CHART_CACHE_FAILURE_TTL + 5),
        _payload(fri_open, mon_pre),
    )
    p._fetch_chart("MSFT", now_epoch=mon_pre + 120)
    assert calls["n"] == 1


# ── _quote_to_market: marketState + freshness gate ───────────────────────────────


def test_quote_to_market_regular_is_open_and_never_freshness_gated():
    # REGULAR is trusted as-is (a thin regular session can legitimately go quiet).
    m = p._quote_to_market(_q("REGULAR", regularMarketTime=_NOW - 9999), now_epoch=_NOW)
    assert m["is_open"] and m["is_regular"]
    assert m["exchange"] == "NasdaqGS" and m["tz_short"] == "EDT"
    assert m["reopens_at"] is None  # open → no reopen hint


def test_quote_to_market_post_fresh_is_open_not_regular():
    m = p._quote_to_market(_q("POST", postMarketTime=_NOW - 60), now_epoch=_NOW)
    assert m["is_open"] and not m["is_regular"] and m["state"] == "POST"


def test_quote_to_market_quote_time_is_regular_market_time():
    # The header's "As of …" must reflect the quote's real trade time, not wall-clock now —
    # so a delayed feed (Yahoo's XETRA tape frozen at the open) reads "As of 9:02 AM", not "now".
    ts = _NOW - 7200  # last trade 2h ago
    m = p._quote_to_market(_q("REGULAR", regularMarketTime=ts), now_epoch=_NOW)
    assert m["quote_time"] == datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()


def test_quote_to_market_quote_time_none_when_absent():
    m = p._quote_to_market(_q("REGULAR"), now_epoch=_NOW)
    assert m["quote_time"] is None  # crypto / no timestamp → caller falls back to now


def test_quote_to_market_post_stale_forced_closed():
    # The XETRA 1:42 AM bug: marketState=POST but the last trade was hours ago (price frozen).
    m = p._quote_to_market(_q("POST", regularMarketTime=_NOW - 7200), now_epoch=_NOW)
    assert not m["is_open"] and m["state"] == "CLOSED"
    assert m["reopens_at"]  # closed → reopen estimate present


def test_quote_to_market_post_no_timestamp_forced_closed():
    m = p._quote_to_market(_q("POST"), now_epoch=_NOW)
    assert not m["is_open"] and m["state"] == "CLOSED"


def test_quote_to_market_pre_fresh_is_open():
    m = p._quote_to_market(_q("PRE", preMarketTime=_NOW - 120), now_epoch=_NOW)
    assert m["is_open"] and not m["is_regular"] and m["state"] == "PRE"


def test_quote_to_market_prepre_postpost_closed():
    for state in ("PREPRE", "POSTPOST"):
        m = p._quote_to_market(_q(state, regularMarketTime=_NOW - 60), now_epoch=_NOW)
        assert not m["is_open"], state
        assert m["reopens_at"], state


def test_quote_to_market_closed_has_future_reopen():
    m = p._quote_to_market(_q("CLOSED"), now_epoch=_NOW)
    assert not m["is_open"]
    assert m["reopens_at"] and datetime.fromisoformat(m["reopens_at"]) > datetime.now(timezone.utc)


def test_quote_to_market_unknown_fails_closed():
    m = p._quote_to_market({}, now_epoch=_NOW)
    assert m["state"] == "UNKNOWN" and not m["is_open"]


def test_quote_to_market_delayed_minutes_from_exchange_flag():
    # XETRA and most non-US exchanges report exchangeDataDelayedBy under Yahoo's free-tier feed.
    m = p._quote_to_market(_q("REGULAR", exchangeDataDelayedBy=15), now_epoch=_NOW)
    assert m["delayed_minutes"] == 15


def test_quote_to_market_delayed_minutes_none_for_realtime_venue():
    # 0 (or absent) means real-time — e.g. Nasdaq — must not read as delayed.
    m = p._quote_to_market(_q("REGULAR", exchangeDataDelayedBy=0), now_epoch=_NOW)
    assert m["delayed_minutes"] is None
    assert p._quote_to_market(_q("REGULAR"), now_epoch=_NOW)["delayed_minutes"] is None


@pytest.mark.parametrize("exchange, delay", [("NYSE", 20), ("NasdaqGM", 15), ("NasdaqGS", 15)])
def test_a_real_time_price_is_not_labelled_delayed(exchange, delay):
    """Found 2026-10-12: ORCL read "Delayed 20m" and XNDU "Delayed 15m" with quotes seconds old.
    For US listings Yahoo reports the exchange's licensing delay but serves a real-time price,
    and says so in quoteSourceName — that, not the flag, decides."""
    q = _q(
        "REGULAR",
        fullExchangeName=exchange,
        exchangeDataDelayedBy=delay,
        quoteSourceName="Nasdaq Real Time Price",
    )
    assert p._quote_to_market(q, now_epoch=_NOW)["delayed_minutes"] is None
    closed = {**q, "marketState": "CLOSED"}
    assert p._quote_to_market(closed, now_epoch=_NOW)["delayed_minutes"] is None


def test_a_delayed_quote_keeps_its_delay():
    q = _q(
        "REGULAR",
        fullExchangeName="XETRA",
        exchangeDataDelayedBy=15,
        quoteSourceName="Delayed Quote",
    )
    assert p._quote_to_market(q, now_epoch=_NOW)["delayed_minutes"] == 15


def test_latest_quote_epoch_takes_max_else_none():
    assert p._latest_quote_epoch({"regularMarketTime": 10, "postMarketTime": 50}) == 50
    assert p._latest_quote_epoch({"preMarketTime": 7}) == 7
    assert p._latest_quote_epoch({}) is None


def test_reopens_none_when_open():
    assert p._reopens_at("REGULAR", "America/New_York", -14400000) is None


# ── _parse_chart_next_open (holiday-accurate reopens; chip only) ──────────────────


def _chart(*starts) -> dict:
    ctp = {seg: {"start": s} for seg, s in zip(("pre", "regular", "post"), starts) if s}
    return {"chart": {"result": [{"meta": {"currentTradingPeriod": ctp}}]}}


def test_parse_chart_next_open_prefers_regular_over_pre():
    # "Reopens" means the REGULAR session open, never the (possibly 2h-earlier) pre window.
    out = p._parse_chart_next_open(
        _chart(_NOW + 7200, _NOW + 9000), _NOW
    )  # pre sooner than regular → still pick regular
    assert out and abs(datetime.fromisoformat(out).timestamp() - (_NOW + 9000)) < 1


def test_parse_chart_next_open_xetra_pre_auction_not_counted():
    # Regression: XETRA reports a 2h pre-trading auction window before the 09:00 regular open.
    # Yahoo SAP.DE shape: pre at +13min, regular at +133min. We must count the regular open,
    # not the pre auction (the bug showed "reopens in 18m" while the market opened ~2h later).
    out = p._parse_chart_next_open(_chart(_NOW + 780, _NOW + 7980), _NOW)
    assert out and abs(datetime.fromisoformat(out).timestamp() - (_NOW + 7980)) < 1


def test_parse_chart_next_open_falls_back_to_earliest_when_no_regular():
    # No regular in the payload (or it already passed) → earliest future session is still useful.
    out = p._parse_chart_next_open(_chart(None, None, _NOW + 600), _NOW)  # only post present
    assert out and abs(datetime.fromisoformat(out).timestamp() - (_NOW + 600)) < 1


def test_parse_chart_next_open_all_past_returns_none():
    assert p._parse_chart_next_open(_chart(_NOW - 3600, _NOW - 1800), _NOW) is None


def test_parse_chart_next_open_missing_returns_none():
    assert p._parse_chart_next_open({}, _NOW) is None


# ── market_status ────────────────────────────────────────────────────────────────


class _FakeInst:
    def all(self):
        return [{"symbol": "BTC", "price_source": "coingecko", "price_source_id": "bitcoin"}]


def test_market_status_crypto_always_open(monkeypatch):
    monkeypatch.setattr("backend.db.instrument_store.InstrumentStore", _FakeInst)
    out = p.market_status(["BTC"])
    assert out["BTC"]["always_open"] and out["BTC"]["is_open"]


def test_market_status_equity_open_via_batch(monkeypatch):
    monkeypatch.setattr("backend.db.instrument_store.InstrumentStore", _FakeInst)
    p._market_cache.clear()
    monkeypatch.setattr(p, "_yf_batch_quote", lambda syms: {"AAPL": _q("REGULAR")})
    out = p.market_status(["AAPL"], with_reopens=False)
    assert out["AAPL"]["state"] == "REGULAR" and out["AAPL"]["is_open"]


def test_market_status_stale_post_is_closed(monkeypatch):
    monkeypatch.setattr("backend.db.instrument_store.InstrumentStore", _FakeInst)
    p._market_cache.clear()
    monkeypatch.setattr(p, "_chart_next_open", lambda s: None)  # don't hit network
    monkeypatch.setattr(
        p, "_yf_batch_quote", lambda syms: {"AAPL": _q("POST", regularMarketTime=_NOW - 7200)}
    )
    out = p.market_status(["AAPL"])
    assert not out["AAPL"]["is_open"] and out["AAPL"]["state"] == "CLOSED"


def test_market_status_omits_unresolved(monkeypatch):
    monkeypatch.setattr("backend.db.instrument_store.InstrumentStore", _FakeInst)
    p._market_cache.clear()
    monkeypatch.setattr(p, "_yf_batch_quote", lambda syms: {})  # batch unavailable
    assert "AAPL" not in p.market_status(["AAPL"])  # caller fails open


def test_market_status_with_reopens_prefers_chart(monkeypatch):
    monkeypatch.setattr("backend.db.instrument_store.InstrumentStore", _FakeInst)
    p._market_cache.clear()
    monkeypatch.setattr(p, "_yf_batch_quote", lambda syms: {"AAPL": _q("CLOSED")})
    monkeypatch.setattr(p, "_chart_next_open", lambda s: "2099-01-04T14:30:00+00:00")
    out = p.market_status(["AAPL"], with_reopens=True)
    assert out["AAPL"]["reopens_at"] == "2099-01-04T14:30:00+00:00"  # holiday-accurate overrides


def test_market_status_gate_path_skips_chart(monkeypatch):
    # with_reopens=False (the gate) must NOT make per-symbol chart calls — one batched quote only.
    monkeypatch.setattr("backend.db.instrument_store.InstrumentStore", _FakeInst)
    p._market_cache.clear()
    monkeypatch.setattr(p, "_yf_batch_quote", lambda syms: {"AAPL": _q("CLOSED")})
    chart_calls: list[str] = []
    monkeypatch.setattr(p, "_chart_next_open", lambda s: chart_calls.append(s) or "x")
    out = p.market_status(["AAPL"], with_reopens=False)
    assert not out["AAPL"]["is_open"]
    assert chart_calls == []  # no chart fetch on the hot path


# ── Session bounds (real pre/regular/post windows, owner ask 2026-08-31: "we shouldn't
#    hardcode data" — the 1D chart's fixed range + the header's countdown must be sourced
#    from Yahoo's own currentTradingPeriod, never a guessed 4am/9:30am/8pm convention) ──


def _chart_payload(trading_periods: dict | list | None = None, **periods) -> dict:
    """`periods` -> meta.currentTradingPeriod; `trading_periods` -> meta.tradingPeriods in
    Yahoo's own nested shape ({seg: [[{start, end}, ...]]} — a list of days, each a list of
    segments), or passed through verbatim when not a dict (the malformed-shape test)."""
    meta: dict = {"currentTradingPeriod": periods}
    if isinstance(trading_periods, dict):
        meta["tradingPeriods"] = {seg: [[win]] for seg, win in trading_periods.items()}
    elif trading_periods is not None:
        meta["tradingPeriods"] = trading_periods
    return {"chart": {"result": [{"meta": meta}]}}


_YESTERDAY = {
    "pre": {"start": 1788163200, "end": 1788183000},
    "regular": {"start": 1788183000, "end": 1788206400},
    "post": {"start": 1788206400, "end": 1788220800},
}
_TOMORROW = {  # a full day after _YESTERDAY, not started yet
    "pre": {"start": 1788249600, "end": 1788256800},
    "regular": {"start": 1788256800, "end": 1788280200},
    "post": {"start": 1788280200, "end": 1788294600},
}


def _iso(epoch: int) -> str:
    return datetime.fromtimestamp(epoch, tz=timezone.utc).isoformat()


def test_parse_trading_periods_prefers_the_datas_own_trading_periods_over_current():
    """The 2am-ET bug (owner-reproduced 2026-09-15, "2pm SGT, ORCL/RKLB show no pre/post
    dots"): past LOCAL MIDNIGHT Yahoo rolls currentTradingPeriod to the next, not-yet-started
    session while the 1D bars it returns are still yesterday's — verified live against
    Tokyo/HK/ASX at ~04:55 local. tradingPeriods describes the session the DATA belongs to,
    so it wins whenever present."""
    payload = _chart_payload(trading_periods=_YESTERDAY, **_TOMORROW)
    bounds = p._parse_trading_periods(payload)
    assert bounds == {
        seg: {"start": _iso(win["start"]), "end": _iso(win["end"])}
        for seg, win in _YESTERDAY.items()
    }


def test_parse_trading_periods_drops_zero_length_segments():
    """A venue with no extended session (Tokyo, futures, FX) reports pre/post windows whose
    start == end — that's "no such session", never a `pre` key the chart would draw a band
    for."""
    payload = _chart_payload(
        trading_periods={
            "pre": {"start": 1788183000, "end": 1788183000},
            "regular": {"start": 1788183000, "end": 1788206400},
            "post": {"start": 1788206400, "end": 1788206400},
        }
    )
    assert p._parse_trading_periods(payload) == {
        "regular": {"start": _iso(1788183000), "end": _iso(1788206400)}
    }


def test_parse_trading_periods_falls_back_to_current_when_trading_periods_malformed():
    """Yahoo's shape is an external boundary — anything other than the {seg: [[...]]} dict
    (e.g. the bare list it returns without includePrePost) is ignored, never a crash."""
    payload = _chart_payload(trading_periods=[[{"start": 1, "end": 2}]], **_YESTERDAY)
    bounds = p._parse_trading_periods(payload)
    assert bounds is not None and bounds["regular"]["start"] == _iso(1788183000)


def test_parse_trading_periods_real_shape():
    payload = _chart_payload(
        pre={"start": 1788163200, "end": 1788183000},
        regular={"start": 1788183000, "end": 1788206400},
        post={"start": 1788206400, "end": 1788220800},
    )
    bounds = p._parse_trading_periods(payload)
    assert bounds == {
        "pre": {
            "start": datetime.fromtimestamp(1788163200, tz=timezone.utc).isoformat(),
            "end": datetime.fromtimestamp(1788183000, tz=timezone.utc).isoformat(),
        },
        "regular": {
            "start": datetime.fromtimestamp(1788183000, tz=timezone.utc).isoformat(),
            "end": datetime.fromtimestamp(1788206400, tz=timezone.utc).isoformat(),
        },
        "post": {
            "start": datetime.fromtimestamp(1788206400, tz=timezone.utc).isoformat(),
            "end": datetime.fromtimestamp(1788220800, tz=timezone.utc).isoformat(),
        },
    }


def test_parse_trading_periods_none_when_missing():
    assert p._parse_trading_periods({}) is None
    assert p._parse_trading_periods({"chart": {"result": []}}) is None
    assert p._parse_trading_periods(_chart_payload()) is None


# ── _has_real_extended_bars: ground truth, not an inferred proxy (owner correction,
#    2026-08-31: "even if it's not delayed, it doesn't mean that the session is extended") ──


def _bars_at(*epoch_seconds: float) -> list[dict]:
    return [{"date": datetime.fromtimestamp(t, tz=timezone.utc).isoformat()} for t in epoch_seconds]


_WINDOW_START = "2026-08-31T08:00:00+00:00"  # 1788163200
_WINDOW_END = "2026-08-31T13:30:00+00:00"  # 1788183000
_AFTER_WINDOW = 1788183000 + 3600  # 1h after the window closes — fully checkable


def test_has_real_extended_bars_true_with_dense_coverage(monkeypatch):
    # 6 one-minute bars spread across a window starting at 1788163200 — a real session.
    bars = _bars_at(*[1788163200 + i * 60 for i in range(6)])
    monkeypatch.setattr(p, "fetch_history_result", lambda *a, **k: {"points": bars})
    assert (
        p._has_real_extended_bars("AAPL", _WINDOW_START, _WINDOW_END, now_epoch=_AFTER_WINDOW)
        is True
    )


def test_has_real_extended_bars_false_for_bare_opening_print(monkeypatch):
    # Only 2 bars, both right at the window's own open — a pre-open auction print, not a
    # continuously-traded session.
    bars = _bars_at(1788163200, 1788163260)
    monkeypatch.setattr(p, "fetch_history_result", lambda *a, **k: {"points": bars})
    assert (
        p._has_real_extended_bars("AAPL", _WINDOW_START, _WINDOW_END, now_epoch=_AFTER_WINDOW)
        is False
    )


def test_has_real_extended_bars_ignores_bars_outside_the_window(monkeypatch):
    # 6 bars, but all during the REGULAR session — none actually inside the requested window.
    bars = _bars_at(*[1788200000 + i * 60 for i in range(6)])
    monkeypatch.setattr(p, "fetch_history_result", lambda *a, **k: {"points": bars})
    assert (
        p._has_real_extended_bars("AAPL", _WINDOW_START, _WINDOW_END, now_epoch=_AFTER_WINDOW)
        is False
    )


def test_has_real_extended_bars_false_on_fetch_failure(monkeypatch):
    def _boom(*a, **k):
        raise RuntimeError("upstream unavailable")

    monkeypatch.setattr(p, "fetch_history_result", _boom)
    assert (
        p._has_real_extended_bars("AAPL", _WINDOW_START, _WINDOW_END, now_epoch=_AFTER_WINDOW)
        is False
    )


def test_has_real_extended_bars_none_when_window_hasnt_started_yet():
    """The core fix (owner-reproduced live bug, 2026-08-31): a POST window checked during PRE/
    REGULAR (before it's even started) must read as "not yet checkable" (None), never as a
    false "no real session" — there's nothing to count yet, that's not the same claim."""
    before_window_starts = 1788163200 - 3600
    assert (
        p._has_real_extended_bars(
            "AAPL", _WINDOW_START, _WINDOW_END, now_epoch=before_window_starts
        )
        is None
    )


def test_has_real_extended_bars_judges_in_progress_window_on_elapsed_portion(monkeypatch):
    """Checking a window we're CURRENTLY inside (e.g. still in PRE) must judge only the
    elapsed portion so far, not penalize it for not having finished."""
    bars = _bars_at(*[1788163200 + i * 60 for i in range(6)])  # first 6 minutes of the window
    monkeypatch.setattr(p, "fetch_history_result", lambda *a, **k: {"points": bars})
    ten_minutes_in = 1788163200 + 600
    assert (
        p._has_real_extended_bars("AAPL", _WINDOW_START, _WINDOW_END, now_epoch=ten_minutes_in)
        is True
    )


def test_has_real_extended_bars_in_progress_window_with_few_bars_is_inconclusive(monkeypatch):
    """Owner-reproduced live, 2026-09-22, ALOY at 04:27 ET: one pre-market bar so far (a $9
    stock, quiet morning) read as a hard False — "no extended session" — so the session
    bounds shrank to the regular window, the 04:27 print fell before the window's start and
    the 1D chart was EMPTY with no bands while Yahoo showed the band and the dot. A window
    still in progress with too few bars is not yet checkable (None); only a COMPLETED window
    can prove absence."""
    bars = _bars_at(1788163200)  # the 04:00 print, and nothing else — yet
    monkeypatch.setattr(p, "fetch_history_result", lambda *a, **k: {"points": bars})
    half_an_hour_in = 1788163200 + 27 * 60
    assert (
        p._has_real_extended_bars("ALOY", _WINDOW_START, _WINDOW_END, now_epoch=half_an_hour_in)
        is None
    )


def test_has_real_extended_bars_completed_thin_window_is_real_when_bars_are_spread(monkeypatch):
    """A completed window with 2 bars 27 minutes apart (04:00 and 04:27) is thin, real trading —
    not the 1-3 bars clustered at the open the threshold exists to reject."""
    spread = _bars_at(1788163200, 1788163200 + 27 * 60)
    monkeypatch.setattr(p, "fetch_history_result", lambda *a, **k: {"points": spread})
    assert (
        p._has_real_extended_bars("ALOY", _WINDOW_START, _WINDOW_END, now_epoch=_AFTER_WINDOW)
        is True
    )


def test_market_status_with_session_bounds_keeps_pre_with_real_bars(monkeypatch):
    monkeypatch.setattr("backend.db.instrument_store.InstrumentStore", _FakeInst)
    p._market_cache.clear()
    monkeypatch.setattr(p, "_yf_batch_quote", lambda syms: {"AAPL": _q("PRE", preMarketTime=_NOW)})
    monkeypatch.setattr(
        p,
        "_fetch_chart",
        lambda s: _chart_payload(
            pre={"start": 1788163200, "end": 1788183000},
            regular={"start": 1788183000, "end": 1788206400},
        ),
    )
    monkeypatch.setattr(
        p,
        "fetch_history_result",
        lambda *a, **k: {"points": _bars_at(*[1788163200 + i * 60 for i in range(6)])},
    )
    out = p.market_status(["AAPL"], with_session_bounds=True, now_epoch=_AFTER_WINDOW)
    assert (
        out["AAPL"]["session_bounds"]["regular"]["start"]
        == datetime.fromtimestamp(1788183000, tz=timezone.utc).isoformat()
    )
    assert "pre" in out["AAPL"]["session_bounds"]


def test_market_status_with_session_bounds_drops_pre_without_real_bars(monkeypatch):
    """The core new behavior: a reported pre-market window with no real trading behind it
    (a bare opening print, or none at all) is dropped from session_bounds entirely — regardless
    of whether the venue is a delayed or real-time quote (that flag alone is NOT the check)."""
    monkeypatch.setattr("backend.db.instrument_store.InstrumentStore", _FakeInst)
    p._market_cache.clear()
    monkeypatch.setattr(p, "_yf_batch_quote", lambda syms: {"AAPL": _q("PRE", preMarketTime=_NOW)})
    monkeypatch.setattr(
        p,
        "_fetch_chart",
        lambda s: _chart_payload(
            pre={"start": 1788163200, "end": 1788183000},
            regular={"start": 1788183000, "end": 1788206400},
        ),
    )
    monkeypatch.setattr(p, "fetch_history_result", lambda *a, **k: {"points": _bars_at(1788163200)})
    out = p.market_status(["AAPL"], with_session_bounds=True, now_epoch=_AFTER_WINDOW)
    assert "pre" not in out["AAPL"]["session_bounds"]
    assert "regular" in out["AAPL"]["session_bounds"]  # regular itself is never filtered


def test_market_status_trusts_pre_verdict_for_a_post_window_not_started_yet(monkeypatch):
    """The core fix (owner-reproduced live bug, 2026-08-31, RKLB: a real post-market band
    vanished at 10am ET because post hasn't started yet today). PRE has already happened and
    shows real bars; POST hasn't started yet (unjudgeable on its own) — POST must still be kept,
    trusting PRE's verdict for the same venue."""
    monkeypatch.setattr("backend.db.instrument_store.InstrumentStore", _FakeInst)
    p._market_cache.clear()
    monkeypatch.setattr(p, "_yf_batch_quote", lambda syms: {"AAPL": _q("REGULAR")})
    monkeypatch.setattr(
        p,
        "_fetch_chart",
        lambda s: _chart_payload(
            pre={"start": 1788163200, "end": 1788183000},
            regular={"start": 1788183000, "end": 1788206400},
            post={"start": 1788206400, "end": 1788220800},
        ),
    )
    monkeypatch.setattr(
        p,
        "fetch_history_result",
        lambda *a, **k: {"points": _bars_at(*[1788163200 + i * 60 for i in range(6)])},
    )
    # "Now" is mid-REGULAR — well after PRE ended (checkable, real bars), well before POST starts.
    mid_regular = 1788183000 + 3600
    out = p.market_status(["AAPL"], with_session_bounds=True, now_epoch=mid_regular)
    assert "pre" in out["AAPL"]["session_bounds"]
    assert "post" in out["AAPL"]["session_bounds"]  # kept despite not having started yet


def test_market_status_session_bounds_off_by_default(monkeypatch):
    monkeypatch.setattr("backend.db.instrument_store.InstrumentStore", _FakeInst)
    p._market_cache.clear()
    monkeypatch.setattr(p, "_yf_batch_quote", lambda syms: {"AAPL": _q("PRE", preMarketTime=_NOW)})
    chart_calls: list[str] = []
    monkeypatch.setattr(p, "_fetch_chart", lambda s: chart_calls.append(s) or None)
    out = p.market_status(["AAPL"])  # default: no with_session_bounds
    assert "session_bounds" not in out["AAPL"]
    assert chart_calls == []


def test_market_status_with_session_bounds_kept_for_postpost(monkeypatch):
    """POSTPOST (deep overnight — Yahoo's own distinction from a live POST window) still gets
    session_bounds: it's TODAY's just-finished session, not a genuinely different trading day
    (owner-reproduced 2026-09-01: MSFT's chart lost its dotted pre/post bands entirely the
    moment marketState ticked from POST to POSTPOST, even mid-evening on the SAME calendar
    day). Plain CLOSED behaves the same way now — see
    `test_market_status_with_session_bounds_kept_for_plain_closed_with_real_bars` below. "Now"
    is well after the reported regular session has ENDED — the defining trait of a real
    POSTPOST case (see the sibling PREPRE-rolled-to-tomorrow test for the case where it
    hasn't)."""
    monkeypatch.setattr("backend.db.instrument_store.InstrumentStore", _FakeInst)
    p._market_cache.clear()
    monkeypatch.setattr(p, "_yf_batch_quote", lambda syms: {"AAPL": _q("POSTPOST")})
    monkeypatch.setattr(
        p,
        "_fetch_chart",
        lambda s: _chart_payload(
            pre={"start": 1788163200, "end": 1788183000},
            regular={"start": 1788183000, "end": 1788206400},
            post={"start": 1788206400, "end": 1788220800},
        ),
    )
    monkeypatch.setattr(
        p,
        "fetch_history_result",
        lambda *a, **k: {"points": _bars_at(*[1788163200 + i * 60 for i in range(6)])},
    )
    well_after_close = 1788220800 + 3600  # after post ends, not just after pre ends
    out = p.market_status(["AAPL"], with_session_bounds=True, now_epoch=well_after_close)
    assert "session_bounds" in out["AAPL"]
    assert "pre" in out["AAPL"]["session_bounds"]
    assert "post" in out["AAPL"]["session_bounds"]


def test_market_status_with_session_bounds_dropped_when_currentTradingPeriod_rolled_to_next_day(
    monkeypatch,
):
    """The KSTL.DE/XETRA regression (owner-reproduced 2026-09-01): once a venue's overnight gap
    is long enough (XETRA has no real extended-hours trading at all), Yahoo's own
    currentTradingPeriod rolls over to describe TOMORROW's session before this PREPRE window
    even ends — the reported regular window hasn't STARTED, let alone finished. Blindly keeping
    it (as the POSTPOST case correctly does) would attach a session window a full day ahead of
    the real intraday bars, producing a chart whose fixed 1D range doesn't overlap the actual
    data at all. session_bounds must be dropped entirely here, not just narrowed to "regular"."""
    monkeypatch.setattr("backend.db.instrument_store.InstrumentStore", _FakeInst)
    p._market_cache.clear()
    monkeypatch.setattr(p, "_yf_batch_quote", lambda syms: {"AAPL": _q("PREPRE")})
    tomorrow_pre = {"start": 1788249600, "end": 1788256800}  # a full day after _WINDOW_START
    tomorrow_regular = {"start": 1788256800, "end": 1788280200}
    tomorrow_post = {"start": 1788280200, "end": 1788294600}
    monkeypatch.setattr(
        p,
        "_fetch_chart",
        lambda s: _chart_payload(pre=tomorrow_pre, regular=tomorrow_regular, post=tomorrow_post),
    )
    chart_calls: list[str] = []
    monkeypatch.setattr(p, "fetch_history_result", lambda *a, **k: chart_calls.append(a) or {})
    # "Now" sits inside the deep overnight gap — well before tomorrow's own pre-market starts.
    still_overnight = tomorrow_pre["start"] - 3600
    out = p.market_status(["AAPL"], with_session_bounds=True, now_epoch=still_overnight)
    assert "session_bounds" not in out["AAPL"]
    assert chart_calls == []  # rejected before ever reaching the real-bars check


def test_market_status_keeps_yesterdays_session_bounds_past_local_midnight(monkeypatch):
    """The user-facing bug behind this whole change (owner-reproduced 2026-09-15: at 2pm SGT
    = 2am ET, ORCL/RKLB's 1D chart drew yesterday's full session but no pre/post dots).
    Yahoo has already rolled currentTradingPeriod to tomorrow's not-yet-started session,
    but the chart payload's tradingPeriods — and the 1D bars themselves — are still
    yesterday's. session_bounds must describe the session the chart is actually showing."""
    monkeypatch.setattr("backend.db.instrument_store.InstrumentStore", _FakeInst)
    p._market_cache.clear()
    monkeypatch.setattr(p, "_yf_batch_quote", lambda syms: {"AAPL": _q("PREPRE")})
    monkeypatch.setattr(
        p, "_fetch_chart", lambda s: _chart_payload(trading_periods=_YESTERDAY, **_TOMORROW)
    )
    monkeypatch.setattr(
        p,
        "fetch_history_result",
        lambda *a, **k: {"points": _bars_at(*[1788163200 + i * 60 for i in range(6)])},
    )
    deep_overnight = _YESTERDAY["post"]["end"] + 4 * 3600  # well before tomorrow's pre
    out = p.market_status(["AAPL"], with_session_bounds=True, now_epoch=deep_overnight)
    assert out["AAPL"]["session_bounds"] == {
        seg: {"start": _iso(win["start"]), "end": _iso(win["end"])}
        for seg, win in _YESTERDAY.items()
    }


def test_market_status_session_bounds_attempted_but_empty_for_closed_symbol_with_no_chart(
    monkeypatch,
):
    """A closed symbol is no longer skipped outright — the chart fetch is attempted (it may
    still have real, usable bounds from earlier today); here the fetch itself comes back
    empty, so session_bounds ends up absent for that reason, not because state == CLOSED."""
    monkeypatch.setattr("backend.db.instrument_store.InstrumentStore", _FakeInst)
    p._market_cache.clear()
    monkeypatch.setattr(p, "_yf_batch_quote", lambda syms: {"AAPL": _q("CLOSED")})
    chart_calls: list[str] = []
    monkeypatch.setattr(p, "_fetch_chart", lambda s: chart_calls.append(s) or None)
    out = p.market_status(["AAPL"], with_session_bounds=True, with_reopens=False)
    assert "session_bounds" not in out["AAPL"]
    assert chart_calls == ["AAPL"]


def test_market_status_with_session_bounds_kept_for_plain_closed_with_real_bars(monkeypatch):
    """The core new behavior (owner request 2026-09-09): the 1D chart's pre/post dots should
    show whenever real extended-hours data exists for today's session, regardless of whether
    the live marketState label happens to be PREPRE/POSTPOST or has already settled into plain
    CLOSED — mirrors the POSTPOST case above but for a symbol that reads CLOSED outright."""
    monkeypatch.setattr("backend.db.instrument_store.InstrumentStore", _FakeInst)
    p._market_cache.clear()
    monkeypatch.setattr(p, "_yf_batch_quote", lambda syms: {"AAPL": _q("CLOSED")})
    monkeypatch.setattr(
        p,
        "_fetch_chart",
        lambda s: _chart_payload(
            pre={"start": 1788163200, "end": 1788183000},
            regular={"start": 1788183000, "end": 1788206400},
            post={"start": 1788206400, "end": 1788220800},
        ),
    )
    monkeypatch.setattr(
        p,
        "fetch_history_result",
        lambda *a, **k: {"points": _bars_at(*[1788163200 + i * 60 for i in range(6)])},
    )
    well_after_close = 1788220800 + 3600  # deep overnight — well after today's post ended
    out = p.market_status(["AAPL"], with_session_bounds=True, now_epoch=well_after_close)
    assert "session_bounds" in out["AAPL"]
    assert "pre" in out["AAPL"]["session_bounds"]
    assert "post" in out["AAPL"]["session_bounds"]


def test_market_status_with_session_bounds_dropped_for_plain_closed_rolled_to_next_day(
    monkeypatch,
):
    """A weekend/holiday CLOSED is still excluded — not by its state label, but because Yahoo's
    reported regular window is the NEXT (not-yet-started) session, caught by the same
    "hasn't ended yet" guard that protects the PREPRE/XETRA rollover case."""
    monkeypatch.setattr("backend.db.instrument_store.InstrumentStore", _FakeInst)
    p._market_cache.clear()
    monkeypatch.setattr(p, "_yf_batch_quote", lambda syms: {"AAPL": _q("CLOSED")})
    next_session_regular = {"start": 1788256800, "end": 1788280200}
    monkeypatch.setattr(p, "_fetch_chart", lambda s: _chart_payload(regular=next_session_regular))
    chart_calls: list[str] = []
    monkeypatch.setattr(p, "fetch_history_result", lambda *a, **k: chart_calls.append(a) or {})
    still_before_next_open = next_session_regular["start"] - 3600
    out = p.market_status(["AAPL"], with_session_bounds=True, now_epoch=still_before_next_open)
    assert "session_bounds" not in out["AAPL"]
    assert chart_calls == []  # rejected before ever reaching the real-bars check


# ── fetch_live batch vs fallback (side-effecting stores stubbed) ─────────────────


def _stub_fetch_live_sideeffects(monkeypatch):
    monkeypatch.setattr("backend.db.instrument_store.InstrumentStore", _FakeInst)
    monkeypatch.setattr(p, "save_fx_cache", lambda *a, **k: None)
    monkeypatch.setattr(p, "load_fx_cache", lambda: {})
    monkeypatch.setattr(p, "_fetch_coingecko_by_id", lambda pairs: ({}, {}, {}))

    class _FakeHist:
        def prior_to_map(self, day=None, days=None):
            return {}

        def record_many(self, *a, **k):
            return None

    monkeypatch.setattr("backend.db.price_history_store.PriceHistoryStore", _FakeHist)
    p._price_cache.clear()
    p._market_cache.clear()


def test_fetch_live_batch_skips_per_ticker(monkeypatch):
    _stub_fetch_live_sideeffects(monkeypatch)
    monkeypatch.setattr(
        p,
        "_yf_batch_quote",
        lambda syms: {
            s: {
                "regularMarketPrice": 100.0,
                "regularMarketPreviousClose": 99.0,
                "marketState": "REGULAR",
            }
            for s in syms
        },
    )
    called: list[str] = []
    monkeypatch.setattr(p, "_fetch_one", lambda s: called.append(s) or (s, 1.0, None))
    r = p.fetch_live(["AAPL", "MSFT"], [])
    assert r["prices"]["AAPL"] == 100.0 and r["prices"]["MSFT"] == 100.0
    assert called == []  # batch covered everything → per-ticker NOT used


def test_fetch_live_falls_back_when_batch_empty(monkeypatch):
    _stub_fetch_live_sideeffects(monkeypatch)
    monkeypatch.setattr(p, "_yf_batch_quote", lambda syms: {})  # batch failed → fall back
    monkeypatch.setattr(p, "_fetch_one", lambda s: (s, 42.0, 40.0))
    r = p.fetch_live(["AAPL"], [])
    assert r["prices"]["AAPL"] == 42.0  # per-ticker path served the price


# ── _trading_day_map / per-instrument prior-close anchor ──────────────────────


class _FixedNow(datetime):
    """`datetime.now()` pinned to 2026-08-10 17:35 UTC — 01:35 SGT Aug 11 / 13:35 EDT Aug 10,
    the exact instant of the PLTR incident, where the UTC calendar date and the NY exchange's
    calendar date disagree."""

    _FIXED = datetime(2026, 8, 10, 17, 35, tzinfo=timezone.utc)

    @classmethod
    def now(cls, tz=None):
        return cls._FIXED if tz is not None else cls._FIXED.replace(tzinfo=None)


def test_trading_day_map_uses_each_symbols_own_cached_exchange_timezone(monkeypatch):
    p._market_cache.clear()
    p._market_cache["PLTR"] = (0.0, {"timezone": "America/New_York"})  # NYSE session still open
    p._market_cache["7203.T"] = (0.0, {"timezone": "Asia/Tokyo"})  # already tomorrow in Tokyo
    monkeypatch.setattr(p, "datetime", _FixedNow)

    result = p._trading_day_map(["PLTR", "7203.T", "UNKNOWN"])

    assert result == {"PLTR": "2026-08-10", "7203.T": "2026-08-11"}
    assert "UNKNOWN" not in result  # no cached timezone → caller falls back to the store default


def test_fetch_live_anchors_prior_close_to_each_tickers_own_exchange_day(monkeypatch):
    """Regression (2026-08-11 PLTR incident): a still-open NYSE session's live tick must be
    recorded, and its prior-close looked up, against PLTR's OWN exchange trading day — not a
    single boundary shared by every ticker (which split one session across two `date` rows
    and made `prior_to_map` anchor to an intraday snapshot from earlier THAT SAME session)."""
    monkeypatch.setattr("backend.db.instrument_store.InstrumentStore", _FakeInst)
    monkeypatch.setattr(p, "save_fx_cache", lambda *a, **k: None)
    monkeypatch.setattr(p, "load_fx_cache", lambda: {})
    monkeypatch.setattr(p, "_fetch_coingecko_by_id", lambda pairs: ({}, {}, {}))
    p._price_cache.clear()
    p._market_cache.clear()

    calls: dict = {}

    class _FakeHist:
        def prior_to_map(self, day=None, days=None):
            calls["prior_to_map_days"] = days
            return {}

        def record_many(self, prices, day=None, source="", days=None):
            calls["record_many_days"] = days

    monkeypatch.setattr("backend.db.price_history_store.PriceHistoryStore", _FakeHist)
    monkeypatch.setattr(
        p,
        "_yf_batch_quote",
        lambda syms: {
            "PLTR": {
                "regularMarketPrice": 177.48,
                "regularMarketPreviousClose": 172.01,
                "marketState": "REGULAR",
                "exchangeTimezoneName": "America/New_York",
            }
        },
    )
    monkeypatch.setattr(p, "_fetch_one", lambda s: (s, None, None))
    monkeypatch.setattr(p, "datetime", _FixedNow)

    p.fetch_live(["PLTR"], [])

    assert calls["prior_to_map_days"] == {"PLTR": "2026-08-10"}
    assert calls["record_many_days"] == {"PLTR": "2026-08-10"}


# ── Extended-hours (pre/post-market) quotes ───────────────────────────────────


def test_extended_hours_from_quote_pre_market():
    q = {
        "preMarketPrice": 318.90,
        "preMarketChange": -0.80,
        "preMarketChangePercent": -0.25024033,
        "preMarketTime": 1788178312,
    }
    ext = p._extended_hours_from_quote(q, "PRE")
    assert ext == {
        "session": "pre",
        "price": 318.9,
        "change_abs": -0.8,
        "change_pct": -0.2502,
        "as_of": datetime.fromtimestamp(1788178312, tz=timezone.utc).isoformat(),
    }


def test_extended_hours_from_quote_post_market():
    q = {
        "postMarketPrice": 320.10,
        "postMarketChange": 0.40,
        "postMarketChangePercent": 0.1251,
        "postMarketTime": 1788178999,
    }
    ext = p._extended_hours_from_quote(q, "POST")
    assert ext["session"] == "post"
    assert ext["price"] == 320.1
    assert ext["change_abs"] == 0.4


def test_extended_hours_from_quote_none_outside_pre_post():
    q = {"preMarketPrice": 318.90, "postMarketPrice": 320.10}
    assert p._extended_hours_from_quote(q, "REGULAR") is None
    assert p._extended_hours_from_quote(q, "CLOSED") is None
    assert p._extended_hours_from_quote(q, None) is None


def test_extended_hours_from_quote_none_when_no_extended_price():
    # PRE state but Yahoo has no pre-market print yet (thin/foreign ticker).
    assert p._extended_hours_from_quote({}, "PRE") is None


def test_extended_hours_from_quote_postpost_still_shown():
    """POSTPOST (deep overnight) is NOT the same as CLOSED — Yahoo's own frozen postMarket*
    fields from the standard close are still real, honestly-timestamped information (owner-
    reproduced live bug, 2026-09-01: MSFT's after-hours figure vanished the instant marketState
    ticked from POST to POSTPOST, even though nothing about the underlying data changed —
    Perplexity's own UI kept showing the identical print all evening)."""
    q = {
        "postMarketPrice": 507.73,
        "postMarketChange": 0.44,
        "postMarketChangePercent": 0.0867,
        "postMarketTime": 1788220792,
        "regularMarketPrice": 507.29,
    }
    ext = p._extended_hours_from_quote(q, "POSTPOST")
    assert ext is not None
    assert ext["session"] == "post"
    assert ext["price"] == 507.73


def test_extended_hours_from_quote_prepre_still_shown():
    q = {
        "preMarketPrice": 318.90,
        "preMarketChange": -0.80,
        "preMarketChangePercent": -0.25,
        "preMarketTime": 1788178312,
        "regularMarketPrice": 319.70,
    }
    ext = p._extended_hours_from_quote(q, "PREPRE")
    assert ext is not None
    assert ext["session"] == "pre"


def test_extended_hours_from_quote_phantom_frozen_at_regular_close_returns_none():
    """The actual guard against a phantom extended session (XETRA's own known case): a
    postMarketPrice that's just a frozen COPY of the regular close (no real extended trade
    ever happened) is suppressed — checked directly against regularMarketPrice, not by how
    long the print has been sitting there (a real print, however old, stays real; a copy of
    the regular close never was)."""
    q = {
        "postMarketPrice": 150.00,
        "postMarketChange": 0.0,
        "postMarketChangePercent": 0.0,
        "postMarketTime": 1788220792,
        "regularMarketPrice": 150.00,
    }
    assert p._extended_hours_from_quote(q, "POST") is None
    assert p._extended_hours_from_quote(q, "POSTPOST") is None


# ── resolve_price_quote: the single read chokepoint (owner ask 2026-08-31) ────────────────


def test_resolve_price_quote_prefers_extended_over_regular():
    """The whole point of the chokepoint: when a live extended quote exists for a ticker,
    it wins — `price` IS the extended print, not a side annotation."""
    quote = p.resolve_price_quote(
        "PLTR",
        prices={"PLTR": 150.0},
        extended={
            "PLTR": {
                "session": "post",
                "price": 152.5,
                "change_abs": 2.5,
                "change_pct": 1.6667,
                "as_of": "2026-08-31T21:00:00+00:00",
            }
        },
        prev_prices={"PLTR": 149.0},
    )
    assert quote.price == 152.5
    assert quote.source == "extended"
    assert quote.is_extended is True
    assert quote.extended_session == "post"
    # `regular_price` always keeps the regular value, even though `price` won with extended —
    # this is the field a caller that must NEVER use an extended print (the daily
    # price-history write) reads instead.
    assert quote.regular_price == 150.0
    assert quote.prev_close == 149.0
    # An EXTENDED quote's change_abs/change_pct compare against TODAY's regular close, not
    # yesterday's prior close (verified directly against Yahoo's own UI: an after-hours
    # change reads price-vs-today's-close — the earlier version of this test asserted
    # price-vs-yesterday's-close, which read a materially different, wrong number). Here
    # Yahoo's own pre-computed change_abs/change_pct (2.5 / 1.6667, already relative to the
    # regular close upstream — see _extended_hours_from_quote) are used directly, verbatim.
    assert quote.change_abs == 2.5
    assert quote.change_pct == 1.6667


def test_resolve_price_quote_extended_falls_back_to_regular_close_when_yahoo_omits_change():
    """Yahoo's own preMarketChange(Percent)/postMarketChange(Percent) can be missing (thin/
    foreign ticker) even though a real extended price exists — falls back to computing
    change against `regular_price` (today's close), never `prev_prices` (yesterday's)."""
    quote = p.resolve_price_quote(
        "PLTR",
        prices={"PLTR": 150.0},
        extended={
            "PLTR": {
                "session": "post",
                "price": 152.5,
                "change_abs": None,
                "change_pct": None,
                "as_of": None,
            }
        },
        prev_prices={"PLTR": 149.0},
    )
    assert quote.change_abs == pytest.approx(152.5 - 150.0)
    assert quote.change_pct == pytest.approx((152.5 / 150.0 - 1) * 100, rel=1e-4)


def test_resolve_price_quote_falls_back_to_regular_when_no_extended():
    quote = p.resolve_price_quote(
        "AAPL", prices={"AAPL": 200.0}, extended={}, prev_prices={"AAPL": 198.0}
    )
    assert quote.price == 200.0
    assert quote.source == "live"
    assert quote.is_extended is False
    assert quote.extended_session is None
    assert quote.regular_price == 200.0
    # A REGULAR quote's change is unrelated to the extended-quote fix above — it always
    # anchors to prev_prices (yesterday's close), the correct, standard convention for
    # "today's regular-session change".
    assert quote.change_pct == pytest.approx((200.0 / 198.0 - 1) * 100, rel=1e-4)


def test_resolve_price_quote_none_when_no_data():
    assert p.resolve_price_quote("AAPL", prices={}, extended={}, prev_prices={}) is None
    assert p.resolve_price_quote("", prices={"AAPL": 1.0}, extended={}) is None


def test_resolve_price_quote_extended_without_prev_close_omits_change():
    """No recorded prior close yet → None, never a fabricated 0%/0.0 (same convention as
    _resolve_prev elsewhere)."""
    quote = p.resolve_price_quote(
        "NEWCO",
        prices={},
        extended={"NEWCO": {"session": "pre", "price": 10.0}},
        prev_prices={},
    )
    assert quote.price == 10.0
    assert quote.is_extended is True
    assert quote.change_abs is None
    assert quote.change_pct is None


def _stub_hist_with_prior(monkeypatch, prior: dict):
    class _FakeHist:
        def prior_to_map(self, day=None, days=None):
            return dict(prior)

        def record_many(self, *a, **k):
            return None

    monkeypatch.setattr("backend.db.price_history_store.PriceHistoryStore", _FakeHist)


def test_fetch_live_pre_market_ignores_trading_day_anchor(monkeypatch):
    """Regression (owner report 2026-08-31, a held US stock): while PRE, regularMarketPrice is simply the
    frozen close of the LAST completed session — the headline must compare it against ITS OWN
    prior close (Yahoo's regularMarketPreviousClose), not the trading-day anchor (which would
    resolve to Friday's own close here, collapsing the change to a false 0%). Also asserts the
    new `extended` quote is surfaced alongside it."""
    monkeypatch.setattr("backend.db.instrument_store.InstrumentStore", _FakeInst)
    monkeypatch.setattr(p, "save_fx_cache", lambda *a, **k: None)
    monkeypatch.setattr(p, "load_fx_cache", lambda: {})
    monkeypatch.setattr(p, "_fetch_coingecko_by_id", lambda pairs: ({}, {}, {}))
    p._price_cache.clear()
    p._market_cache.clear()
    # The buggy trading-day anchor would resolve here to Friday's OWN close — must be ignored.
    _stub_hist_with_prior(monkeypatch, {"AAPL": 319.70})
    monkeypatch.setattr(
        p,
        "_yf_batch_quote",
        lambda syms: {
            "AAPL": {
                "regularMarketPrice": 319.70,
                "regularMarketPreviousClose": 314.58,
                "marketState": "PRE",
                "preMarketPrice": 318.90,
                "preMarketChange": -0.80,
                "preMarketChangePercent": -0.25,
                "preMarketTime": _NOW,
                "regularMarketTime": _NOW - 43200,
                "fullExchangeName": "NasdaqGS",
                "exchangeTimezoneName": "America/New_York",
                "exchangeTimezoneShortName": "EDT",
            }
        },
    )
    monkeypatch.setattr(p, "_fetch_one", lambda s: (s, None, None))

    r = p.fetch_live(["AAPL"], [])

    assert r["prices"]["AAPL"] == 319.70
    assert r["prev_prices"]["AAPL"] == 314.58  # NOT collapsed to 319.70
    # Regression (owner-reproduced live bug, 2026-08-31): the "At close" timestamp must be
    # the REAL regularMarketTime (here 12h old — routine for a frozen PRE-market close), not
    # collapsed to "now" by the generic 6h freshness bar meant for a genuinely LIVE quote.
    assert r["as_of"]["AAPL"] == datetime.fromtimestamp(_NOW - 43200, tz=timezone.utc).isoformat()
    assert r["extended"]["AAPL"]["session"] == "pre"
    assert r["extended"]["AAPL"]["price"] == 318.9
    assert r["extended"]["AAPL"]["change_abs"] == -0.8


def test_fetch_live_regular_session_keeps_the_delayed_open_close_timestamp(monkeypatch):
    """Regression (owner-reproduced 2026-09-23, KSTL.DE at 09:05 CEST): XETRA's feed is 15m
    delayed, so for the open's first minutes Yahoo already says REGULAR while the price and
    regularMarketTime are still yesterday's 17:35 close. The header's "As of" must say THAT —
    stamping it "now" read as if today's open had printed at yesterday's price."""
    monkeypatch.setattr("backend.db.instrument_store.InstrumentStore", _FakeInst)
    monkeypatch.setattr(p, "save_fx_cache", lambda *a, **k: None)
    monkeypatch.setattr(p, "load_fx_cache", lambda: {})
    monkeypatch.setattr(p, "_fetch_coingecko_by_id", lambda pairs: ({}, {}, {}))
    p._price_cache.clear()
    p._market_cache.clear()
    _stub_hist_with_prior(monkeypatch, {})
    yesterday_close = int(datetime.now(timezone.utc).timestamp()) - 55800  # 15.5h ago
    monkeypatch.setattr(
        p,
        "_yf_batch_quote",
        lambda syms: {
            "KSTL.DE": {
                "regularMarketPrice": 27.10,
                "regularMarketPreviousClose": 26.80,
                "marketState": "REGULAR",
                "regularMarketTime": yesterday_close,
                "exchangeDataDelayedBy": 15,
                "fullExchangeName": "XETRA",
                "exchangeTimezoneName": "Europe/Berlin",
                "exchangeTimezoneShortName": "CEST",
            }
        },
    )
    monkeypatch.setattr(p, "_fetch_one", lambda s: (s, None, None))

    r = p.fetch_live(["KSTL.DE"], [])

    assert (
        r["as_of"]["KSTL.DE"]
        == datetime.fromtimestamp(yesterday_close, tz=timezone.utc).isoformat()
    )


def test_fetch_live_regular_session_still_uses_trading_day_anchor(monkeypatch):
    """Companion to the above — the PLTR fix (test_fetch_live_anchors_prior_close_to_each_
    tickers_own_exchange_day) must still fire while the regular session is ACTUALLY open."""
    monkeypatch.setattr("backend.db.instrument_store.InstrumentStore", _FakeInst)
    monkeypatch.setattr(p, "save_fx_cache", lambda *a, **k: None)
    monkeypatch.setattr(p, "load_fx_cache", lambda: {})
    monkeypatch.setattr(p, "_fetch_coingecko_by_id", lambda pairs: ({}, {}, {}))
    p._price_cache.clear()
    p._market_cache.clear()
    _stub_hist_with_prior(monkeypatch, {"PLTR": 172.01})
    monkeypatch.setattr(
        p,
        "_yf_batch_quote",
        lambda syms: {
            "PLTR": {
                "regularMarketPrice": 177.48,
                "regularMarketPreviousClose": 999.99,  # deliberately wrong — must be overridden
                "marketState": "REGULAR",
                "fullExchangeName": "NYSE",
                "exchangeTimezoneName": "America/New_York",
                "exchangeTimezoneShortName": "EDT",
            }
        },
    )
    monkeypatch.setattr(p, "_fetch_one", lambda s: (s, None, None))

    r = p.fetch_live(["PLTR"], [])

    assert r["prev_prices"]["PLTR"] == 172.01  # trading-day anchor still wins while REGULAR
    assert r["extended"].get("PLTR") is None  # no extended quote outside PRE/POST


# ── price_monitor market-hours gate ──────────────────────────────────────────────


def _wl(*values):
    return {
        "config": {"enabled": True},
        "items": [{"type": "ticker", "value": v, "monitor_news": True} for v in values],
    }


def _stub_alert_tickers(monkeypatch, *values):
    """Seed the decoupled AlertStore source (ADR 0014) the poll union is built from."""
    import backend.services.alerts as alerts_mod

    amap = {v.upper(): [{"op": ">=", "value": 1, "note": ""}] for v in values}
    monkeypatch.setattr(alerts_mod, "alerts_by_ticker", lambda: amap)


def test_gate_skips_when_all_markets_closed(monkeypatch):
    from backend.monitoring import price_monitor as pm

    monkeypatch.setattr(pm, "read_watchlist", lambda: _wl("AAPL", "MSFT"))
    monkeypatch.setattr(pm, "write_watchlist", lambda wl: None)
    _stub_alert_tickers(monkeypatch, "AAPL", "MSFT")
    monkeypatch.setattr(pm, "read_rows", lambda: [])
    monkeypatch.setattr(p, "market_status", lambda syms, **k: {s: {"is_open": False} for s in syms})
    calls: list[list[str]] = []
    monkeypatch.setattr(pm, "fetch_live", lambda t, c: calls.append(list(t)))

    fired = asyncio.run(pm.run_price_check())
    assert fired == 0
    assert calls == []  # closed → never fetched


def test_gate_polls_only_open_markets(monkeypatch):
    from backend.monitoring import price_monitor as pm

    monkeypatch.setattr(pm, "read_watchlist", lambda: _wl("AAPL", "MSFT"))
    monkeypatch.setattr(pm, "write_watchlist", lambda wl: None)
    _stub_alert_tickers(monkeypatch, "AAPL", "MSFT")
    monkeypatch.setattr(pm, "read_rows", lambda: [])
    monkeypatch.setattr(
        p,
        "market_status",
        lambda syms, **k: {"AAPL": {"is_open": True}, "MSFT": {"is_open": False}},
    )
    calls: list[list[str]] = []

    def _fl(tickers, ccys):
        calls.append(list(tickers))
        raise RuntimeError("stop after gate")  # short-circuit the post-fetch alert path

    monkeypatch.setattr(pm, "fetch_live", _fl)
    assert asyncio.run(pm.run_price_check()) == 0
    assert calls == [["AAPL"]]  # only the open market was polled


# ── latest_live_prices() / _live_price_cache: the extended-aware "last known" fallback ──────
# (2026-09-01 consistency pass) — see the cache's own module docstring in prices.py for why
# this is separate from PriceHistoryStore's day-keyed series. Covers both the pure cache-read
# side and the real end-to-end write from `_fetch_live_uncached`.


def test_latest_live_prices_empty_when_nothing_cached():
    assert p.latest_live_prices() == {}


def test_latest_live_prices_returns_a_fresh_entry():
    p._live_price_cache["AAPL"] = (202.5, time.monotonic())
    assert p.latest_live_prices() == {"AAPL": 202.5}


def test_latest_live_prices_drops_an_entry_older_than_the_ttl():
    stale_at = time.monotonic() - p._LIVE_PRICE_CACHE_TTL - 1.0
    p._live_price_cache["AAPL"] = (202.5, stale_at)
    assert p.latest_live_prices() == {}


def test_fetch_live_uncached_caches_the_extended_preferred_price(monkeypatch):
    """End-to-end: a real POST-market v7 quote resolves `resolved`/`_live_price_cache` to the
    EXTENDED print, not the regular price — the exact wiring `positions.hydration_context()`
    now depends on for every bare `hydrated()` caller (owner-reproduced 2026-09-01, PLTR)."""
    monkeypatch.setattr("backend.db.instrument_store.InstrumentStore.all", lambda self: [])
    monkeypatch.setattr(
        p,
        "_yf_batch_quote",
        lambda syms: {
            "AAPL": _q(
                "POST",
                regularMarketPrice=200.0,
                regularMarketPreviousClose=195.0,
                regularMarketTime=_NOW - 3600,
                postMarketPrice=202.5,
                postMarketChange=2.5,
                postMarketChangePercent=1.25,
                postMarketTime=_NOW,
            )
        },
    )
    result = p._fetch_live_uncached(["AAPL"], [], False)
    assert result["prices"]["AAPL"] == 200.0  # the raw regular price, unchanged
    assert result["resolved"]["AAPL"] == 202.5  # extended-preferred
    assert p.latest_live_prices()["AAPL"] == 202.5


def test_fetch_live_uncached_falls_back_to_regular_price_with_no_extended_print(monkeypatch):
    monkeypatch.setattr("backend.db.instrument_store.InstrumentStore.all", lambda self: [])
    monkeypatch.setattr(
        p, "_yf_batch_quote", lambda syms: {"AAPL": _q("REGULAR", regularMarketPrice=200.0)}
    )
    result = p._fetch_live_uncached(["AAPL"], [], False)
    assert result["resolved"]["AAPL"] == 200.0
    assert p.latest_live_prices()["AAPL"] == 200.0
