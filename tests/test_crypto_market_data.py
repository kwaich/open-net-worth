"""Provider order, structural validation, and bounded stale crypto history."""

from __future__ import annotations

import concurrent.futures
import threading
from datetime import datetime, timedelta, timezone

import pytest

from backend.db.market_series_cache_store import MarketSeriesCacheStore
from backend.services import market_data_settings
from backend.services.crypto_market_data import (  # noqa: F401
    CRYPTO_YAHOO_ALIASES,
    fetch_crypto_history,
    record_crypto_quote,
    split_major_crypto_pairs,
    stale_crypto_quote,
    validate_series,
)


def _points(*, start: datetime | None = None) -> list[dict]:
    at = start or (datetime.now(timezone.utc) - timedelta(minutes=1))
    return [
        {
            "date": (at + timedelta(minutes=index)).isoformat(),
            "price": 100.0 + index,
            "open": 99.0 + index,
            "high": 102.0 + index,
            "low": 98.0 + index,
            "close": 100.0 + index,
        }
        for index in range(2)
    ]


def _config(monkeypatch, *, yahoo_primary: bool = True) -> None:
    import backend.services.crypto_market_data as subject

    monkeypatch.setattr(
        subject.MarketDataConfigStore,
        "runtime",
        lambda _self: {"yahoo_primary_for_major_crypto": yahoo_primary},
    )


@pytest.fixture(autouse=True)
def _reset_yahoo_health():
    yield


def test_registry_is_explicit_and_never_infers_unknown_symbols():
    assert CRYPTO_YAHOO_ALIASES == {
        "bitcoin": "BTC-USD",
        "ethereum": "ETH-USD",
        "solana": "SOL-USD",
        "ripple": "XRP-USD",
        "dogecoin": "DOGE-USD",
        "pepe": "PEPE24478-USD",
    }
    mapped, coin_gecko_only = split_major_crypto_pairs(
        [("bitcoin", "bitcoin"), ("pepe", "pepe"), ("unknown", "unknown-coin")]
    )
    assert mapped == [("bitcoin", "BTC-USD"), ("pepe", "PEPE24478-USD")]
    assert coin_gecko_only == [("unknown", "unknown-coin")]


def test_pepe_usd_history_prefers_yahoo_alias_and_caches_canonical_id(db_isolated, monkeypatch):
    _config(monkeypatch)
    calls: list[tuple] = []
    expected = _points()

    result = fetch_crypto_history(
        "pepe",
        "1D",
        "USD",
        yahoo_fetch=lambda symbol, timeframe: (
            calls.append(("yahoo", symbol, timeframe)) or expected
        ),
        coingecko_fetch=lambda *args: pytest.fail(f"CoinGecko fallback called: {args}"),
    )

    assert calls == [("yahoo", "PEPE24478-USD", "1D")]
    assert result["provider"] == "yahoo"
    assert result["state"] == "live"
    assert result["degraded_reason"] is None
    assert result["points"] == expected
    assert MarketSeriesCacheStore().get("pepe", "USD", "1D")["provider"] == "yahoo"
    assert MarketSeriesCacheStore().get("PEPE24478-USD", "USD", "1D") is None
    status = market_data_settings.get_public_status()["yahoo"]
    assert status["last_ok_at"] is not None
    assert status["failure_streak"] == 0


def test_pepe_history_yahoo_failure_falls_back_to_coingecko_canonical_id(db_isolated, monkeypatch):
    _config(monkeypatch)
    calls: list[tuple] = []

    result = fetch_crypto_history(
        "pepe",
        "1D",
        "USD",
        yahoo_fetch=lambda symbol, timeframe: calls.append(("yahoo", symbol, timeframe)) or [],
        coingecko_fetch=lambda coin, timeframe, currency: (
            calls.append(("coingecko", coin, timeframe, currency)) or _points()
        ),
    )

    assert calls == [
        ("yahoo", "PEPE24478-USD", "1D"),
        ("coingecko", "pepe", "1D", "usd"),
    ]
    assert result["provider"] == "coingecko"
    assert result["state"] == "live"
    status = market_data_settings.get_public_status()["yahoo"]
    assert status["last_ok_at"] is None
    assert status["failure_streak"] == 1
    assert status["cooldown_until"] is not None


def test_history_emits_provider_failure_fallback_and_selection_events(db_isolated, monkeypatch):
    _config(monkeypatch)
    from backend.services import market_data_observability

    events: list[tuple[str, dict]] = []
    monkeypatch.setattr(
        market_data_observability,
        "emit",
        lambda event, **fields: events.append((event, fields)),
    )

    result = fetch_crypto_history(
        "bitcoin",
        "1D",
        "USD",
        yahoo_fetch=lambda *_: [],
        coingecko_fetch=lambda *_: _points(),
    )

    assert result["provider"] == "coingecko"
    assert (
        "provider_request",
        {"provider": "yahoo", "canonical_id": "bitcoin", "timeframe": "1D"},
    ) in events
    assert any(
        event == "provider_failure" and fields["provider"] == "yahoo" for event, fields in events
    )
    assert any(
        event == "validation_rejected" and fields["provider"] == "yahoo" for event, fields in events
    )
    assert (
        "fallback",
        {"provider": "coingecko", "canonical_id": "bitcoin", "timeframe": "1D"},
    ) in events
    assert any(
        event == "provider_selected" and fields["provider"] == "coingecko"
        for event, fields in events
    )


def test_open_yahoo_circuit_skips_yahoo_but_keeps_coingecko_live(db_isolated, monkeypatch):
    _config(monkeypatch)
    calls: list[str] = []

    first = fetch_crypto_history(
        "bitcoin",
        "1D",
        "USD",
        yahoo_fetch=lambda *_: calls.append("yahoo") or [],
        coingecko_fetch=lambda *_: calls.append("coingecko") or _points(),
    )
    second = fetch_crypto_history(
        "bitcoin",
        "1D",
        "USD",
        yahoo_fetch=lambda *_: pytest.fail("open circuit must not call Yahoo"),
        coingecko_fetch=lambda *_: calls.append("coingecko") or _points(),
    )

    assert first["provider"] == "coingecko"
    assert first["state"] == "live"
    assert second["provider"] == "coingecko"
    assert second["state"] == "live"
    assert calls == ["yahoo", "coingecko", "coingecko"]


def test_open_yahoo_circuit_is_shared_across_timeframes_but_coingecko_stays_live(
    db_isolated, monkeypatch
):
    _config(monkeypatch)
    cached = _points()
    MarketSeriesCacheStore().put(
        "bitcoin",
        "USD",
        "5D",
        {"provider": "yahoo", "as_of": cached[-1]["date"], "points": cached},
    )
    fetch_crypto_history(
        "bitcoin",
        "1D",
        "USD",
        yahoo_fetch=lambda *_: [],
        coingecko_fetch=lambda *_: _points(),
    )

    result = fetch_crypto_history(
        "bitcoin",
        "5D",
        "USD",
        yahoo_fetch=lambda *_: pytest.fail("provider circuit must span timeframes"),
        coingecko_fetch=lambda *_: _points(),
    )

    assert result["provider"] == "coingecko"
    assert result["state"] == "live"


def test_identical_history_requests_join_one_yahoo_and_fallback_attempt(db_isolated, monkeypatch):
    _config(monkeypatch)
    from backend.services import market_data_observability

    events: list[tuple[str, dict]] = []
    monkeypatch.setattr(
        market_data_observability,
        "emit",
        lambda event, **fields: events.append((event, fields)),
    )
    yahoo_started = threading.Event()
    release_yahoo = threading.Event()
    calls = {"yahoo": 0, "coingecko": 0}

    def yahoo(*_args):
        calls["yahoo"] += 1
        yahoo_started.set()
        assert release_yahoo.wait(2)
        return []

    def coingecko(*_args):
        calls["coingecko"] += 1
        return _points()

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(
            fetch_crypto_history,
            "bitcoin",
            "1D",
            "USD",
            yahoo_fetch=yahoo,
            coingecko_fetch=coingecko,
        )
        assert yahoo_started.wait(1)
        second = executor.submit(
            fetch_crypto_history,
            "bitcoin",
            "1D",
            "USD",
            yahoo_fetch=yahoo,
            coingecko_fetch=coingecko,
        )
        release_yahoo.set()
        assert first.result() == second.result()

    assert calls == {"yahoo": 1, "coingecko": 1}
    assert sum(event == "coalesced" for event, _fields in events) == 1


def test_yahoo_history_failure_preserves_the_last_successful_request(db_isolated, monkeypatch):
    _config(monkeypatch)
    fetch_crypto_history(
        "bitcoin",
        "1D",
        "USD",
        yahoo_fetch=lambda *_: _points(),
        coingecko_fetch=lambda *_: pytest.fail("CoinGecko fallback called"),
    )
    last_ok_at = market_data_settings.get_public_status()["yahoo"]["last_ok_at"]

    result = fetch_crypto_history(
        "bitcoin",
        "1D",
        "USD",
        yahoo_fetch=lambda *_: [],
        coingecko_fetch=lambda *_: _points(),
    )

    assert result["provider"] == "coingecko"
    status = market_data_settings.get_public_status()["yahoo"]
    assert status["last_ok_at"] == last_ok_at
    assert status["failure_streak"] == 1
    assert status["cooldown_until"] is not None


def test_rollback_prefers_coingecko_then_uses_yahoo_for_a_mapped_usd_pair(db_isolated, monkeypatch):
    _config(monkeypatch, yahoo_primary=False)
    calls: list[str] = []

    result = fetch_crypto_history(
        "bitcoin",
        "1D",
        "USD",
        yahoo_fetch=lambda symbol, timeframe: calls.append("yahoo") or _points(),
        coingecko_fetch=lambda coin, timeframe, currency: calls.append("coingecko") or [],
    )

    assert calls == ["coingecko", "yahoo"]
    assert result["provider"] == "yahoo"
    assert result["state"] == "live"


def test_rollback_keeps_coingecko_primary_when_it_succeeds(db_isolated, monkeypatch):
    _config(monkeypatch, yahoo_primary=False)
    calls: list[str] = []

    result = fetch_crypto_history(
        "bitcoin",
        "1D",
        "USD",
        yahoo_fetch=lambda *_: pytest.fail("Yahoo must be the fallback"),
        coingecko_fetch=lambda *args: calls.append("coingecko") or _points(),
    )

    assert calls == ["coingecko"]
    assert result["provider"] == "coingecko"


def test_long_tail_and_non_usd_stay_coingecko_primary(db_isolated, monkeypatch):
    _config(monkeypatch)
    calls: list[str] = []

    def coingecko(coin, timeframe, currency):
        calls.append(coin)
        return _points()

    long_tail = fetch_crypto_history(
        "unknown-coin",
        "1D",
        "USD",
        yahoo_fetch=lambda *_: pytest.fail("Yahoo"),
        coingecko_fetch=coingecko,
    )
    non_usd = fetch_crypto_history(
        "bitcoin",
        "1D",
        "SGD",
        yahoo_fetch=lambda *_: pytest.fail("Yahoo"),
        coingecko_fetch=coingecko,
    )

    assert calls == ["unknown-coin", "bitcoin"]
    assert long_tail["provider"] == non_usd["provider"] == "coingecko"


@pytest.mark.parametrize(
    ("timeframe", "age"),
    [("5Y", timedelta(days=6)), ("MAX", timedelta(days=27))],
)
def test_long_horizon_provider_series_accepts_its_native_cadence(timeframe, age):
    start = datetime.now(timezone.utc) - age
    points = [
        {"date": start.isoformat(), "price": 100.0},
        {"date": (start + timedelta(hours=1)).isoformat(), "price": 101.0},
    ]

    assert validate_series(points, timeframe) == points


@pytest.mark.parametrize(
    ("timeframe", "age"),
    [("5Y", timedelta(days=6)), ("MAX", timedelta(days=27))],
)
def test_long_horizon_fresh_retrieval_serves_stale_during_outage(
    db_isolated, monkeypatch, timeframe, age
):
    _config(monkeypatch)
    start = datetime.now(timezone.utc) - age
    points = [
        {"date": start.isoformat(), "price": 100.0},
        {"date": (start + timedelta(hours=1)).isoformat(), "price": 101.0},
    ]
    live = fetch_crypto_history(
        "bitcoin",
        timeframe,
        "USD",
        yahoo_fetch=lambda *_: points,
        coingecko_fetch=lambda *_: pytest.fail("CoinGecko"),
    )

    stale = fetch_crypto_history(
        "bitcoin",
        timeframe,
        "USD",
        yahoo_fetch=lambda *_: [],
        coingecko_fetch=lambda *_: [],
    )

    assert live["state"] == "live"
    assert stale["state"] == "stale"
    assert stale["points"] == points


@pytest.mark.parametrize(
    ("timeframe", "age"),
    [("5Y", timedelta(days=6)), ("MAX", timedelta(days=27))],
)
def test_long_horizon_stale_expires_by_retrieval_age(db_isolated, monkeypatch, timeframe, age):
    _config(monkeypatch)
    from backend.db import market_series_cache_store as cache_module

    now = datetime.now(timezone.utc)
    start = now - age
    points = [
        {"date": start.isoformat(), "price": 100.0},
        {"date": (start + timedelta(hours=1)).isoformat(), "price": 101.0},
    ]
    monkeypatch.setattr(cache_module, "_now", lambda: now - timedelta(hours=73))
    live = fetch_crypto_history(
        "bitcoin",
        timeframe,
        "USD",
        yahoo_fetch=lambda *_: points,
        coingecko_fetch=lambda *_: pytest.fail("CoinGecko"),
    )
    monkeypatch.setattr(cache_module, "_now", lambda: now)

    expired = fetch_crypto_history(
        "bitcoin",
        timeframe,
        "USD",
        yahoo_fetch=lambda *_: [],
        coingecko_fetch=lambda *_: [],
    )

    assert live["state"] == "live"
    assert expired["state"] == "unavailable"
    assert expired["degraded_reason"] == "stale_expired"


def test_both_fail_serves_bounded_persistent_last_good(db_isolated, monkeypatch):
    _config(monkeypatch)
    from backend.services import market_data_observability

    events: list[tuple[str, dict]] = []
    monkeypatch.setattr(
        market_data_observability,
        "emit",
        lambda event, **fields: events.append((event, fields)),
    )
    cached = _points()
    MarketSeriesCacheStore().put(
        "bitcoin",
        "USD",
        "1D",
        {"provider": "yahoo", "as_of": cached[-1]["date"], "points": cached},
    )

    result = fetch_crypto_history(
        "bitcoin", "1D", "USD", yahoo_fetch=lambda *_: [], coingecko_fetch=lambda *_: []
    )

    assert result["provider"] == "yahoo"
    assert result["state"] == "stale"
    assert result["points"] == cached
    assert result["degraded_reason"] == "upstream_unavailable"
    assert any(event == "stale_serve" for event, _fields in events)


def test_expired_last_good_is_unavailable(db_isolated, monkeypatch):
    _config(monkeypatch)
    from backend.db import market_series_cache_store as cache_module
    from backend.services import market_data_observability

    events: list[tuple[str, dict]] = []
    monkeypatch.setattr(
        market_data_observability,
        "emit",
        lambda event, **fields: events.append((event, fields)),
    )
    now = datetime.now(timezone.utc)
    old = _points(start=now - timedelta(hours=7))
    monkeypatch.setattr(cache_module, "_now", lambda: now - timedelta(hours=7))
    MarketSeriesCacheStore().put(
        "bitcoin",
        "USD",
        "1D",
        {"provider": "yahoo", "as_of": old[-1]["date"], "points": old},
    )
    monkeypatch.setattr(cache_module, "_now", lambda: now)

    result = fetch_crypto_history(
        "bitcoin", "1D", "USD", yahoo_fetch=lambda *_: [], coingecko_fetch=lambda *_: []
    )

    assert result == {
        "provider": None,
        "state": "unavailable",
        "as_of": None,
        "degraded_reason": "stale_expired",
        "points": [],
    }
    assert any(
        event == "unavailable" and fields["reason"] == "stale_expired" for event, fields in events
    )


def test_validated_crypto_quote_has_a_bounded_stale_path(db_isolated):
    as_of = datetime.now(timezone.utc).isoformat()

    assert record_crypto_quote("bitcoin", "USD", "yahoo", 68000, 67000, as_of) is True

    stale = stale_crypto_quote("bitcoin", "USD")

    assert stale == {
        "provider": "yahoo",
        "state": "stale",
        "as_of": as_of,
        "degraded_reason": "upstream_unavailable",
        "price": 68000.0,
        "previous_close": 67000.0,
    }


@pytest.mark.parametrize(
    "offset",
    [None, timedelta(minutes=6), timedelta(hours=-7)],
    ids=["missing", "too-far-future", "expired"],
)
def test_crypto_quote_cache_rejects_missing_future_or_expired_provider_timestamps(
    db_isolated, offset
):
    """The offset is resolved HERE, not in the decorator.

    Computing ``datetime.now()`` inside ``parametrize`` freezes it at COLLECTION time, and
    ``record_crypto_quote`` compares against the real clock when the test actually RUNS. With
    ``_MAX_FUTURE_SKEW`` at 5 minutes, a ``now + 6 minutes`` parameter is only far enough in
    the future while the test runs within 60 seconds of collection — true in isolation
    (<1s), never true in the full suite, where this file is reached ~6 minutes in. So it
    passed alone and failed every full run, which reads as flakiness and is not: it was
    deterministic in both directions. Fixed 2026-08-27.
    """
    as_of = None if offset is None else (datetime.now(timezone.utc) + offset).isoformat()

    assert record_crypto_quote("bitcoin", "USD", "yahoo", 68000, 67000, as_of) is False
    assert MarketSeriesCacheStore().get("bitcoin", "USD", "QUOTE") is None


@pytest.mark.parametrize(
    "points",
    [
        [],
        _points()[:1],
        [{"date": "not-a-date", "price": 1}, {"date": "2026-08-12T00:00:00+00:00", "price": 2}],
        [{"date": "2026-08-12T00:00:00+00:00", "price": float("nan")}] * 2,
        [
            {
                "date": "2026-08-12T00:00:00+00:00",
                "price": 100,
                "open": 100,
                "high": 90,
                "low": 95,
                "close": 100,
            },
            {"date": "2026-08-12T00:01:00+00:00", "price": 101},
        ],
    ],
)
def test_validate_series_rejects_malformed_nonfinite_one_point_and_inconsistent_ohlc(points):
    assert validate_series(points, "1D") == []


def test_validate_series_collapses_duplicates_orders_points_and_allows_large_moves():
    now = datetime.now(timezone.utc)
    points = [
        {"date": (now - timedelta(minutes=1)).isoformat(), "price": 1},
        {"date": (now - timedelta(minutes=2)).isoformat(), "price": 1000},
        {"date": (now - timedelta(minutes=1)).isoformat(), "price": 2},
    ]

    assert validate_series(points, "1D") == [
        {"date": (now - timedelta(minutes=2)).isoformat(), "price": 1000.0},
        {"date": (now - timedelta(minutes=1)).isoformat(), "price": 2.0},
    ]
