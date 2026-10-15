"""Crypto holdings should READ as their market symbol (BTC) while still pricing via
the CoinGecko id (bitcoin). Covers the positions row-ticker derivation + the price
router's symbol→id resolution."""

from __future__ import annotations

import concurrent.futures
import threading
from datetime import datetime, timezone

import pytest

from backend.db.price_history_store import PriceHistoryStore
from backend.services import market_data_settings
from backend.services import prices as prices_mod
from backend.services.crypto_market_data import record_crypto_quote
from backend.services.positions import _row_ticker


def test_row_ticker_crypto_uses_symbol():
    assert (
        _row_ticker({"price_source": "coingecko", "symbol": "BTC", "price_source_id": "bitcoin"})
        == "BTC"
    )


def test_row_ticker_yahoo_keeps_price_source_id():
    # Yahoo rows keep price_source_id (the pricing key, incl. exchange suffix); the
    # client strips the suffix for display.
    assert (
        _row_ticker({"price_source": "yahoo", "symbol": "ONWAI", "price_source_id": "ONWAI.DE"})
        == "ONWAI.DE"
    )
    assert (
        _row_ticker({"price_source": "yahoo", "symbol": "NVDA", "price_source_id": "NVDA"})
        == "NVDA"
    )


def test_row_ticker_crypto_falls_back_to_pid_without_symbol():
    assert (
        _row_ticker({"price_source": "coingecko", "symbol": "", "price_source_id": "bitcoin"})
        == "BITCOIN"
    )


class _FakeStore:
    def all(self):
        return [
            {"symbol": "BTC", "price_source": "coingecko", "price_source_id": "bitcoin"},
            {"symbol": "PEPE", "price_source": "coingecko", "price_source_id": "pepe"},
            {"symbol": "ONWAI", "price_source": "yahoo", "price_source_id": "ONWAI.DE"},
        ]


class _SgdBitcoinStore:
    def all(self):
        return [
            {
                "symbol": "BTC",
                "price_source": "coingecko",
                "price_source_id": "bitcoin",
                "quote_currency": "SGD",
            }
        ]


class _MixedCurrencyCryptoStore:
    def all(self):
        return [
            {
                "symbol": "BTC",
                "price_source": "coingecko",
                "price_source_id": "bitcoin",
                "quote_currency": "USD",
            },
            {
                "symbol": "ETH",
                "price_source": "coingecko",
                "price_source_id": "ethereum",
                "quote_currency": "SGD",
            },
            {
                "symbol": "SOL",
                "price_source": "coingecko",
                "price_source_id": "solana",
                "quote_currency": "EUR",
            },
        ]


def test_crypto_routing_resolves_bare_symbol(monkeypatch):
    monkeypatch.setattr("backend.db.instrument_store.InstrumentStore", _FakeStore)
    cg, yf = prices_mod._crypto_routing(["BTC"])
    assert cg == [("BTC", "bitcoin")]
    assert yf == []


def test_crypto_routing_still_resolves_price_source_id(monkeypatch):
    # Backward-compat: a value still stored as the CoinGecko id keeps routing.
    monkeypatch.setattr("backend.db.instrument_store.InstrumentStore", _FakeStore)
    cg, yf = prices_mod._crypto_routing(["bitcoin"])
    assert cg == [("bitcoin", "bitcoin")]
    assert yf == []


def test_non_usd_crypto_live_quote_stays_coingecko_primary_and_currency_correct(
    db_isolated, monkeypatch
):
    prices_mod._price_cache.clear()
    monkeypatch.setattr("backend.db.instrument_store.InstrumentStore", _SgdBitcoinStore)
    monkeypatch.setattr(
        "backend.db.market_data_config_store.MarketDataConfigStore.runtime",
        lambda _self: {"yahoo_primary_for_major_crypto": True},
    )
    calls: list[dict] = []

    current_provider_time = int(datetime.now(timezone.utc).timestamp())

    def coingecko(_path, params, **_kwargs):
        calls.append(params)
        return {
            "bitcoin": {
                "sgd": 88_000,
                "sgd_24h_change": 1,
                "last_updated_at": current_provider_time,
            }
        }

    monkeypatch.setattr(prices_mod.coingecko_client, "get_json", coingecko)
    monkeypatch.setattr(
        prices_mod,
        "_yf_batch_quote",
        lambda symbols: pytest.fail(f"non-USD crypto must not call Yahoo: {symbols}"),
    )

    result = prices_mod.fetch_live(["BTC"], [], False)

    assert calls == [
        {
            "ids": "bitcoin",
            "vs_currencies": "sgd",
            "include_24hr_change": "true",
            "include_last_updated_at": "true",
        }
    ]
    assert result["prices"] == {"BTC": 88_000.0}
    assert result["providers"] == {"BTC": "coingecko"}
    from backend.db.market_series_cache_store import MarketSeriesCacheStore

    assert MarketSeriesCacheStore().get("bitcoin", "SGD", "QUOTE") is not None
    assert MarketSeriesCacheStore().get("bitcoin", "USD", "QUOTE") is None


def test_mixed_currency_crypto_quotes_use_one_coingecko_batch(db_isolated, monkeypatch):
    prices_mod._price_cache.clear()
    monkeypatch.setattr("backend.db.instrument_store.InstrumentStore", _MixedCurrencyCryptoStore)
    monkeypatch.setattr(
        "backend.db.market_data_config_store.MarketDataConfigStore.runtime",
        lambda _self: {"yahoo_primary_for_major_crypto": True},
    )
    calls: list[dict] = []
    provider_time = int(datetime.now(timezone.utc).timestamp())

    def coingecko(_path, params, **_kwargs):
        calls.append(params)
        return {
            "bitcoin": {
                "usd": 68_000,
                "usd_24h_change": 1,
                "last_updated_at": provider_time,
            },
            "ethereum": {
                "sgd": 3_900,
                "sgd_24h_change": -2,
                "last_updated_at": provider_time,
            },
            "solana": {
                "eur": 125,
                "eur_24h_change": 3,
                "last_updated_at": provider_time,
            },
        }

    monkeypatch.setattr(prices_mod.coingecko_client, "get_json", coingecko)
    yahoo_calls: list[list[str]] = []
    monkeypatch.setattr(
        prices_mod, "_yf_batch_quote", lambda symbols: yahoo_calls.append(symbols) or {}
    )

    result = prices_mod.fetch_live(["BTC", "ETH", "SOL"], [], False)

    assert yahoo_calls == [["BTC-USD"]]
    assert len(calls) == 1
    assert calls[0] == {
        "ids": "bitcoin,ethereum,solana",
        "vs_currencies": "eur,sgd,usd",
        "include_24hr_change": "true",
        "include_last_updated_at": "true",
    }
    assert result["prices"] == {"BTC": 68_000.0, "ETH": 3_900.0, "SOL": 125.0}
    assert result["providers"] == {
        "BTC": "coingecko",
        "ETH": "coingecko",
        "SOL": "coingecko",
    }


def test_mapped_pepe_quote_uses_yahoo_alias_but_keeps_canonical_id(db_isolated, monkeypatch):
    prices_mod._price_cache.clear()
    monkeypatch.setattr("backend.db.instrument_store.InstrumentStore", _FakeStore)
    monkeypatch.setattr(
        "backend.db.market_data_config_store.MarketDataConfigStore.runtime",
        lambda _self: {"yahoo_primary_for_major_crypto": True},
    )
    provider_time = int(datetime.now(timezone.utc).timestamp())

    def yahoo(symbols):
        assert symbols == ["PEPE24478-USD"]
        return {
            "PEPE24478-USD": {
                "regularMarketPrice": 0.000012,
                "regularMarketPreviousClose": 0.000011,
                "regularMarketTime": provider_time,
            }
        }

    monkeypatch.setattr(prices_mod, "_yf_batch_quote", yahoo)
    monkeypatch.setattr(
        prices_mod,
        "_fetch_coingecko_by_id",
        lambda pairs: pytest.fail(f"CoinGecko fallback should not run: {pairs}"),
    )

    result = prices_mod.fetch_live(["PEPE"], [], False)

    assert result["prices"] == {"PEPE": 0.000012}
    assert result["providers"] == {"PEPE": "yahoo"}
    assert result["states"] == {"PEPE": "live"}
    assert PriceHistoryStore().latest("pepe")["price"] == 0.000012


def test_rollback_live_quote_falls_back_to_yahoo_once_and_keeps_canonical_id(
    db_isolated, monkeypatch
):
    prices_mod._price_cache.clear()
    monkeypatch.setattr("backend.db.instrument_store.InstrumentStore", _FakeStore)
    monkeypatch.setattr(
        "backend.db.market_data_config_store.MarketDataConfigStore.runtime",
        lambda _self: {"yahoo_primary_for_major_crypto": False},
    )
    calls: list[str] = []
    provider_time = int(datetime.now(timezone.utc).timestamp())
    monkeypatch.setattr(
        prices_mod,
        "_fetch_coingecko_by_id",
        lambda pairs: calls.append("coingecko") or ({}, {}, {}),
    )
    monkeypatch.setattr(
        prices_mod,
        "_yf_batch_quote",
        lambda symbols: (
            calls.append("yahoo")
            or {
                "BTC-USD": {
                    "regularMarketPrice": 68000.0,
                    "regularMarketPreviousClose": 67000.0,
                    "regularMarketTime": provider_time,
                }
            }
        ),
    )

    result = prices_mod.fetch_live(["BTC"], [], False)

    assert calls == ["coingecko", "yahoo"]
    assert result["prices"] == {"BTC": 68000.0}
    assert result["providers"] == {"BTC": "yahoo"}
    assert PriceHistoryStore().latest("bitcoin")["price"] == 68000.0


def test_pepe_yahoo_primary_live_quote_falls_back_to_coingecko_canonical_id(
    db_isolated, monkeypatch
):
    prices_mod._price_cache.clear()
    monkeypatch.setattr("backend.db.instrument_store.InstrumentStore", _FakeStore)
    monkeypatch.setattr(
        "backend.db.market_data_config_store.MarketDataConfigStore.runtime",
        lambda _self: {"yahoo_primary_for_major_crypto": True},
    )
    calls: list[tuple] = []
    monkeypatch.setattr(
        prices_mod,
        "_yf_batch_quote",
        lambda symbols: calls.append(("yahoo", symbols)) or {},
    )
    monkeypatch.setattr(
        prices_mod,
        "_fetch_coingecko_by_id",
        lambda pairs: (
            calls.append(("coingecko", pairs))
            or (
                {"PEPE": 0.000012},
                {"PEPE": 0.000011},
                {"PEPE": datetime.now(timezone.utc).isoformat()},
            )
        ),
    )

    result = prices_mod.fetch_live(["PEPE"], [], False)

    assert calls == [
        ("yahoo", ["PEPE24478-USD"]),
        ("coingecko", [("PEPE", "pepe")]),
    ]
    assert result["providers"]["PEPE"] == "coingecko"
    assert PriceHistoryStore().latest("pepe")["price"] == 0.000012


def test_yahoo_quote_outage_opens_shared_circuit_without_blocking_coingecko(
    db_isolated, monkeypatch
):
    from backend.services import market_data_observability

    events: list[tuple[str, dict]] = []
    monkeypatch.setattr(
        market_data_observability,
        "emit",
        lambda event, **fields: events.append((event, fields)),
    )
    prices_mod._price_cache.clear()
    monkeypatch.setattr("backend.db.instrument_store.InstrumentStore", _FakeStore)
    monkeypatch.setattr(
        "backend.db.market_data_config_store.MarketDataConfigStore.runtime",
        lambda _self: {"yahoo_primary_for_major_crypto": True},
    )
    calls: list[tuple] = []
    monkeypatch.setattr(
        prices_mod,
        "_yf_batch_quote",
        lambda symbols: calls.append(("yahoo", symbols)) or {},
    )

    def coingecko(pairs):
        calls.append(("coingecko", pairs))
        key = pairs[0][0]
        return (
            {key: 68_000.0},
            {key: 67_000.0},
            {key: datetime.now(timezone.utc).isoformat()},
        )

    monkeypatch.setattr(prices_mod, "_fetch_coingecko_by_id", coingecko)

    first = prices_mod.fetch_live(["BTC"], [], False)
    prices_mod._price_cache.clear()
    second = prices_mod.fetch_live(["PEPE"], [], False)

    assert first["providers"]["BTC"] == "coingecko"
    assert second["providers"]["PEPE"] == "coingecko"
    assert calls == [
        ("yahoo", ["BTC-USD"]),
        ("coingecko", [("BTC", "bitcoin")]),
        ("coingecko", [("PEPE", "pepe")]),
    ]
    assert market_data_settings.get_public_status()["yahoo"]["cooldown_until"] is not None
    assert sum(event == "provider_request" for event, _fields in events) == 1
    assert any(
        event == "provider_failure" and fields.get("provider") == "yahoo"
        for event, fields in events
    )
    assert sum(event == "fallback" for event, _fields in events) == 2
    assert [fields["canonical_id"] for event, fields in events if event == "fallback"] == [
        "bitcoin",
        "pepe",
    ]
    assert sum(event == "provider_selected" for event, _fields in events) == 2
    assert any(event == "circuit_blocked" for event, _fields in events)


def test_coingecko_rejects_invalid_quote_values_and_returns_provider_timestamp(monkeypatch):
    provider_time = int(datetime.now(timezone.utc).timestamp())
    monkeypatch.setattr(
        prices_mod.coingecko_client,
        "get_json",
        lambda *args, **kwargs: {
            "bitcoin": {"usd": 68000, "usd_24h_change": 1, "last_updated_at": provider_time},
            "ethereum": {"usd": 0, "last_updated_at": provider_time},
            "solana": {"usd": float("nan"), "last_updated_at": provider_time},
        },
    )

    values, previous, as_of = prices_mod._fetch_coingecko_by_id(
        [("BTC", "bitcoin"), ("ETH", "ethereum"), ("SOL", "solana")]
    )

    assert values == {"BTC": 68000.0}
    assert previous["BTC"] > 0
    assert as_of == {"BTC": datetime.fromtimestamp(provider_time, tz=timezone.utc).isoformat()}


def test_concurrent_coingecko_quote_batches_keep_their_timestamp_maps_isolated(monkeypatch):
    barrier = threading.Barrier(2)
    bitcoin_time = int(datetime.now(timezone.utc).timestamp())
    ethereum_time = bitcoin_time - 1

    def get_json(_path, params, **_kwargs):
        barrier.wait(timeout=5)
        if params["ids"] == "bitcoin":
            return {"bitcoin": {"usd": 68000, "last_updated_at": bitcoin_time}}
        return {"ethereum": {"usd": 3000, "last_updated_at": ethereum_time}}

    monkeypatch.setattr(prices_mod.coingecko_client, "get_json", get_json)
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        bitcoin, ethereum = list(
            executor.map(
                prices_mod._fetch_coingecko_by_id,
                [[("BTC", "bitcoin")], [("ETH", "ethereum")]],
            )
        )

    assert bitcoin == (
        {"BTC": 68000.0},
        {},
        {"BTC": datetime.fromtimestamp(bitcoin_time, tz=timezone.utc).isoformat()},
    )
    assert ethereum == (
        {"ETH": 3000.0},
        {},
        {"ETH": datetime.fromtimestamp(ethereum_time, tz=timezone.utc).isoformat()},
    )


def test_yahoo_live_quote_preserves_fresh_provider_timestamp_and_rejects_invalid_values(
    db_isolated, monkeypatch
):
    prices_mod._price_cache.clear()
    provider_time = int(datetime.now(timezone.utc).timestamp())
    monkeypatch.setattr("backend.db.instrument_store.InstrumentStore", _FakeStore)
    monkeypatch.setattr(
        "backend.db.market_data_config_store.MarketDataConfigStore.runtime",
        lambda _self: {"yahoo_primary_for_major_crypto": True},
    )
    monkeypatch.setattr(
        prices_mod,
        "_yf_batch_quote",
        lambda symbols: {
            "BTC-USD": {
                "regularMarketPrice": 68000.0,
                "regularMarketPreviousClose": 67000.0,
                "regularMarketTime": provider_time,
            }
        },
    )

    result = prices_mod.fetch_live(["BTC"], [], False)

    assert (
        result["as_of"]["BTC"] == datetime.fromtimestamp(provider_time, tz=timezone.utc).isoformat()
    )
    available = market_data_settings.get_public_status()["yahoo"]
    assert available["last_ok_at"] is not None
    assert available["failure_streak"] == 0

    prices_mod._price_cache.clear()
    monkeypatch.setattr(
        prices_mod,
        "_yf_batch_quote",
        lambda symbols: {"BTC-USD": {"regularMarketPrice": 0, "regularMarketPreviousClose": -1}},
    )
    monkeypatch.setattr(prices_mod, "_fetch_coingecko_by_id", lambda pairs: ({}, {}, {}))

    invalid = prices_mod.fetch_live(["BTC"], [], False)

    assert invalid["states"]["BTC"] == "stale"
    assert invalid["providers"]["BTC"] == "yahoo"
    assert PriceHistoryStore().latest("bitcoin")["price"] == 68000.0
    degraded = market_data_settings.get_public_status()["yahoo"]
    assert degraded["last_ok_at"] == available["last_ok_at"]
    assert degraded["failure_streak"] == 1
    assert degraded["cooldown_until"] is not None


@pytest.mark.parametrize("provider_time", [None, 1_700_000_000, 4_102_444_800])
def test_invalid_crypto_provider_time_is_not_reported_live_or_persisted(
    db_isolated, monkeypatch, provider_time
):
    prices_mod._price_cache.clear()
    monkeypatch.setattr("backend.db.instrument_store.InstrumentStore", _FakeStore)
    monkeypatch.setattr(
        "backend.db.market_data_config_store.MarketDataConfigStore.runtime",
        lambda _self: {"yahoo_primary_for_major_crypto": True},
    )
    quote = {"regularMarketPrice": 68000.0, "regularMarketPreviousClose": 67000.0}
    if provider_time is not None:
        quote["regularMarketTime"] = provider_time
    monkeypatch.setattr(prices_mod, "_yf_batch_quote", lambda symbols: {"BTC-USD": quote})
    monkeypatch.setattr(prices_mod, "_fetch_coingecko_by_id", lambda pairs: ({}, {}, {}))

    result = prices_mod.fetch_live(["BTC"], [], False)

    assert result["states"]["BTC"] == "unavailable"
    assert result["as_of"]["BTC"] is None
    from backend.db.market_series_cache_store import MarketSeriesCacheStore

    assert MarketSeriesCacheStore().get("bitcoin", "USD", "QUOTE") is None


def test_stale_crypto_quote_includes_keyed_degraded_reason_in_live_metadata(
    db_isolated, monkeypatch
):
    assert record_crypto_quote(
        "bitcoin", "USD", "yahoo", 68000, 67000, datetime.now(timezone.utc).isoformat()
    )
    prices_mod._price_cache.clear()
    monkeypatch.setattr("backend.db.instrument_store.InstrumentStore", _FakeStore)
    monkeypatch.setattr(
        "backend.db.market_data_config_store.MarketDataConfigStore.runtime",
        lambda _self: {"yahoo_primary_for_major_crypto": True},
    )
    monkeypatch.setattr(prices_mod, "_yf_batch_quote", lambda symbols: {})
    monkeypatch.setattr(prices_mod, "_fetch_coingecko_by_id", lambda pairs: ({}, {}, {}))

    result = prices_mod.fetch_live(["bitcoin"], [], False)

    assert result["states"]["bitcoin"] == "stale"
    assert result["degraded_reasons"] == {"bitcoin": "upstream_unavailable"}
