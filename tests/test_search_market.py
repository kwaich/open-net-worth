"""Unit tests for search_market (prices.py) — offline, all HTTP monkeypatched.

Covers:
- equity query → correct shape including price_source_id
- crypto query → correct shape
- metal query ("gold") → commodity candidate with price_source_id="GC=F"
- empty/no-match query → []
- ranking: exact symbol match sorted first
- deduplication by (price_source, price_source_id)
- cache returns same object within TTL
- one @pytest.mark.integration test that hits real providers
"""

from __future__ import annotations

import logging

import pytest

import backend.services.prices as prices_mod
from backend.services import coingecko_client
from backend.services.prices import (
    _coingecko_candidates,
    _match_rank,
    _metal_candidates,
    _yahoo_equity_candidates,
    search_market,
)

# ── helpers ───────────────────────────────────────────────────────────────────


def _mock_yahoo_search(monkeypatch, quotes: list[dict]):
    """Yahoo search now rides yfinance's managed session, not bare urllib."""
    import importlib

    class _Resp:
        def json(self):
            return {"quotes": quotes}

        def close(self):
            pass

    class _FakeYfData:
        def get(self, url, params=None, timeout=30):
            return _Resp()

    monkeypatch.setattr(importlib.import_module("yfinance.data"), "YfData", _FakeYfData)


def _make_cg_resp(coins: list[dict]):
    """Return a fake urllib response context manager for CoinGecko /search."""
    import json

    class _Resp:
        def __init__(self):
            self._data = json.dumps({"coins": coins}).encode()

        def read(self, size=-1):
            return self._data

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    return _Resp()


# ── _yahoo_equity_candidates ──────────────────────────────────────────────────


def test_yahoo_equity_candidates_shape(monkeypatch):
    """Returns candidates with all required keys for EQUITY/ETF quotes."""
    _mock_yahoo_search(
        monkeypatch,
        [
            {
                "symbol": "AAPL",
                "quoteType": "EQUITY",
                "shortname": "Apple Inc.",
                "exchDisp": "NASDAQ",
            }
        ],
    )
    results = _yahoo_equity_candidates("apple", 8)
    assert len(results) == 1
    r = results[0]
    assert r["symbol"] == "AAPL"
    assert r["name"] == "Apple Inc."
    assert r["asset_class"] == "equity"
    assert r["exchange"] == "NASDAQ"
    assert r["price_source"] == "yahoo"
    assert r["price_source_id"] == "AAPL"


def test_yahoo_equity_candidates_uses_longname_fallback(monkeypatch):
    """Falls back to longname when shortname is absent."""
    _mock_yahoo_search(
        monkeypatch,
        [
            {
                "symbol": "TSM",
                "quoteType": "EQUITY",
                "longname": "Taiwan Semiconductor",
                "exchange": "NYQ",
            }
        ],
    )
    results = _yahoo_equity_candidates("tsm", 8)
    assert results[0]["name"] == "Taiwan Semiconductor"
    assert results[0]["exchange"] == "NYQ"


def test_yahoo_equity_candidates_filters_non_equity(monkeypatch):
    """MUTUALFUND / INDEX quotes are excluded from candidates."""
    _mock_yahoo_search(
        monkeypatch,
        [
            {"symbol": "AXISZZX", "quoteType": "MUTUALFUND", "shortname": "Private Co"},
            {"symbol": "^GSPC", "quoteType": "INDEX", "shortname": "S&P 500"},
            {"symbol": "SPY", "quoteType": "ETF", "shortname": "SPDR S&P 500 ETF"},
        ],
    )
    results = _yahoo_equity_candidates("spy", 8)
    assert len(results) == 1
    assert results[0]["symbol"] == "SPY"


def test_yahoo_equity_candidates_returns_empty_on_http_error(monkeypatch):
    """Returns [] when HTTP raises — never propagates."""

    import importlib

    class _FakeYfData:
        def get(self, url, params=None, timeout=30):
            raise OSError("network down")

    monkeypatch.setattr(importlib.import_module("yfinance.data"), "YfData", _FakeYfData)
    assert _yahoo_equity_candidates("apple", 8) == []


# ── _coingecko_candidates ─────────────────────────────────────────────────────


def test_coingecko_candidates_shape(monkeypatch):
    """Returns candidates with all required keys, sorted by market_cap_rank."""
    monkeypatch.setattr(
        coingecko_client,
        "_open_request",
        lambda req, timeout=10: _make_cg_resp(
            [
                {"id": "bitcoin", "symbol": "btc", "name": "Bitcoin", "market_cap_rank": 1},
                {"id": "bitcoin-sv", "symbol": "bsv", "name": "Bitcoin SV", "market_cap_rank": 50},
            ]
        ),
    )
    results = _coingecko_candidates("bitcoin", 8)
    assert len(results) == 2
    r = results[0]
    assert r["symbol"] == "BTC"
    assert r["name"] == "Bitcoin"
    assert r["asset_class"] == "crypto"
    assert r["exchange"] == ""
    assert r["price_source"] == "coingecko"
    assert r["price_source_id"] == "bitcoin"


def test_coingecko_candidates_none_rank_sorts_last(monkeypatch):
    """Coins with no market_cap_rank (None) sort after ranked coins."""
    monkeypatch.setattr(
        coingecko_client,
        "_open_request",
        lambda req, timeout=10: _make_cg_resp(
            [
                {"id": "obscure-coin", "symbol": "obs", "name": "Obscure", "market_cap_rank": None},
                {"id": "ethereum", "symbol": "eth", "name": "Ethereum", "market_cap_rank": 2},
            ]
        ),
    )
    results = _coingecko_candidates("eth", 8)
    assert results[0]["price_source_id"] == "ethereum"
    assert results[1]["price_source_id"] == "obscure-coin"


def test_coingecko_candidates_respects_limit(monkeypatch):
    """Returns at most n candidates."""
    coins = [
        {"id": f"coin-{i}", "symbol": f"C{i}", "name": f"Coin {i}", "market_cap_rank": i + 1}
        for i in range(10)
    ]
    monkeypatch.setattr(
        coingecko_client,
        "_open_request",
        lambda req, timeout=10: _make_cg_resp(coins),
    )
    results = _coingecko_candidates("coin", 3)
    assert len(results) == 3


def test_coingecko_candidates_returns_empty_on_error(monkeypatch):
    monkeypatch.setattr(
        coingecko_client,
        "_open_request",
        lambda req, timeout=10: (_ for _ in ()).throw(OSError()),
    )
    assert _coingecko_candidates("btc", 8) == []


# ── _metal_candidates ─────────────────────────────────────────────────────────


def test_metal_candidates_gold(monkeypatch):
    """'gold' matches the Gold (spot) entry → commodity candidate with price_source_id=GC=F."""
    results = _metal_candidates("gold")
    assert len(results) == 1
    r = results[0]
    assert r["asset_class"] == "commodity"
    assert r["exchange"] == "Metal"
    assert r["price_source"] == "yahoo"
    assert r["price_source_id"] == "GC=F"
    assert r["symbol"] == "GOLD"
    assert "Gold" in r["name"]


def test_metal_candidates_silver_alias(monkeypatch):
    """'xag' alias for silver → silver candidate."""
    results = _metal_candidates("xag")
    assert len(results) == 1
    assert results[0]["price_source_id"] == "SI=F"


def test_metal_candidates_partial_name_match():
    """Partial match against display name: 'plat' matches 'Platinum (spot)'."""
    results = _metal_candidates("plat")
    assert any(r["price_source_id"] == "PL=F" for r in results)


def test_metal_candidates_no_match():
    """Non-metal query returns []."""
    assert _metal_candidates("apple") == []


def test_metal_candidates_dedupes_by_yahoo_symbol():
    """'gold' and 'xau' both map to GC=F — only one candidate emitted."""
    results_gold = _metal_candidates("gold")
    results_xau = _metal_candidates("xau")
    # Each individual call returns exactly one entry (no dup within a single query)
    assert len(results_gold) == 1
    assert len(results_xau) == 1
    # Both resolve to the same futures symbol
    assert results_gold[0]["price_source_id"] == results_xau[0]["price_source_id"] == "GC=F"


# ── _match_rank ───────────────────────────────────────────────────────────────


def test_match_rank_exact_symbol():
    c = {"symbol": "AAPL", "name": "Apple Inc."}
    assert _match_rank("AAPL", c) == 0
    assert _match_rank("aapl", c) == 0  # case-insensitive


def test_match_rank_exact_name():
    c = {"symbol": "AAPL", "name": "Apple Inc."}
    assert _match_rank("Apple Inc.", c) == 0


def test_match_rank_symbol_prefix():
    c = {"symbol": "AAPL", "name": "Apple Inc."}
    assert _match_rank("AA", c) == 1


def test_match_rank_name_prefix():
    c = {"symbol": "AAPL", "name": "Apple Inc."}
    assert _match_rank("Apple", c) == 2


def test_match_rank_substring():
    c = {"symbol": "AAPL", "name": "Apple Inc."}
    assert _match_rank("pple", c) == 3


def test_match_rank_no_match():
    c = {"symbol": "AAPL", "name": "Apple Inc."}
    assert _match_rank("Google", c) == 4


# ── search_market ─────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def clear_market_cache():
    """Clear the search_market TTL cache before every test."""
    from backend.services.prices import _market_search_cache

    _market_search_cache.clear()
    yield
    _market_search_cache.clear()


def _patch_helpers(monkeypatch, equity=None, crypto=None, metal=None):
    """Patch the three provider helpers directly."""
    monkeypatch.setattr(prices_mod, "_yahoo_equity_candidates", lambda q, n: equity or [])
    monkeypatch.setattr(prices_mod, "_coingecko_candidates", lambda q, n: crypto or [])
    monkeypatch.setattr(prices_mod, "_metal_candidates", lambda q: metal or [])


def test_search_market_empty_query(monkeypatch):
    _patch_helpers(monkeypatch)
    assert search_market("") == []
    assert search_market("   ") == []


def test_search_market_no_match(monkeypatch):
    _patch_helpers(monkeypatch)
    assert search_market("zzznomatch") == []


def test_search_market_equity_shape(monkeypatch):
    """Equity query returns a list with the right shape incl. price_source_id."""
    eq = [
        {
            "symbol": "AAPL",
            "name": "Apple Inc.",
            "asset_class": "equity",
            "exchange": "NASDAQ",
            "price_source": "yahoo",
            "price_source_id": "AAPL",
        }
    ]
    _patch_helpers(monkeypatch, equity=eq)
    results = search_market("AAPL", limit=8)
    assert len(results) == 1
    r = results[0]
    assert r["symbol"] == "AAPL"
    assert r["name"] == "Apple Inc."
    assert r["asset_class"] == "equity"
    assert r["exchange"] == "NASDAQ"
    assert r["price_source"] == "yahoo"
    assert r["price_source_id"] == "AAPL"


def test_search_market_crypto_shape(monkeypatch):
    """Crypto query returns a list with the right shape."""
    crypto = [
        {
            "symbol": "BTC",
            "name": "Bitcoin",
            "asset_class": "crypto",
            "exchange": "",
            "price_source": "coingecko",
            "price_source_id": "bitcoin",
        }
    ]
    _patch_helpers(monkeypatch, crypto=crypto)
    results = search_market("bitcoin", limit=8)
    assert len(results) == 1
    r = results[0]
    assert r["asset_class"] == "crypto"
    assert r["price_source"] == "coingecko"
    assert r["price_source_id"] == "bitcoin"


def test_search_market_metal_commodity(monkeypatch):
    """Metal query returns commodity candidate with price_source_id=GC=F."""
    metal = [
        {
            "symbol": "GOLD",
            "name": "Gold (spot)",
            "asset_class": "commodity",
            "exchange": "Metal",
            "price_source": "yahoo",
            "price_source_id": "GC=F",
        }
    ]
    _patch_helpers(monkeypatch, metal=metal)
    results = search_market("gold", limit=8)
    assert len(results) == 1
    assert results[0]["asset_class"] == "commodity"
    assert results[0]["price_source_id"] == "GC=F"


def test_search_market_ranking_exact_symbol_first(monkeypatch):
    """Exact symbol match sorts before prefix/substring matches."""
    candidates_eq = [
        {
            "symbol": "BTC",
            "name": "Bitcoin",
            "asset_class": "equity",
            "exchange": "",
            "price_source": "yahoo",
            "price_source_id": "BTC",
        },
        {
            "symbol": "BTCX",
            "name": "Bitcoin Extended",
            "asset_class": "equity",
            "exchange": "",
            "price_source": "yahoo",
            "price_source_id": "BTCX",
        },
    ]
    _patch_helpers(monkeypatch, equity=candidates_eq)
    results = search_market("btc", limit=8)
    assert results[0]["symbol"] == "BTC"
    assert results[1]["symbol"] == "BTCX"


def test_search_market_dedup_same_source_id(monkeypatch):
    """Candidates with identical (price_source, price_source_id) are deduplicated."""
    dup = {
        "symbol": "ETH",
        "name": "Ethereum",
        "asset_class": "equity",
        "exchange": "",
        "price_source": "yahoo",
        "price_source_id": "ETH",
    }
    _patch_helpers(monkeypatch, equity=[dup, dup])
    results = search_market("eth", limit=8)
    assert len(results) == 1


def test_search_market_limit_respected(monkeypatch):
    """Returns at most `limit` results."""
    eq = [
        {
            "symbol": f"SYM{i}",
            "name": f"Company {i}",
            "asset_class": "equity",
            "exchange": "",
            "price_source": "yahoo",
            "price_source_id": f"SYM{i}",
        }
        for i in range(10)
    ]
    _patch_helpers(monkeypatch, equity=eq)
    assert len(search_market("sym", limit=5)) == 5


def test_search_market_cache_hits_without_requery(monkeypatch):
    """Second call with same (query, limit) is served from cache (no provider re-call),
    and returns an equal-but-independent list so a caller mutating it can't corrupt the cache."""
    eq = [
        {
            "symbol": "AAPL",
            "name": "Apple Inc.",
            "asset_class": "equity",
            "exchange": "NASDAQ",
            "price_source": "yahoo",
            "price_source_id": "AAPL",
        }
    ]
    call_count = 0

    def _mock_yahoo(q, n):
        nonlocal call_count
        call_count += 1
        return eq

    monkeypatch.setattr(prices_mod, "_yahoo_equity_candidates", _mock_yahoo)
    monkeypatch.setattr(prices_mod, "_coingecko_candidates", lambda q, n: [])
    monkeypatch.setattr(prices_mod, "_metal_candidates", lambda q: [])

    r1 = search_market("apple", limit=8)
    r2 = search_market("apple", limit=8)
    assert call_count == 1  # second call served from cache, providers not re-invoked
    assert r1 == r2  # equal content
    assert r1 is not r2  # but a defensive copy — mutating one can't corrupt the cache
    r1.append({"symbol": "JUNK"})
    assert search_market("apple", limit=8) == r2  # cache unaffected by the mutation above
    assert call_count == 1  # provider only called once


def test_search_market_cache_key_includes_limit(monkeypatch):
    """Different limits use separate cache entries."""
    eq = [
        {
            "symbol": f"X{i}",
            "name": f"X {i}",
            "asset_class": "equity",
            "exchange": "",
            "price_source": "yahoo",
            "price_source_id": f"X{i}",
        }
        for i in range(5)
    ]
    _patch_helpers(monkeypatch, equity=eq)
    r3 = search_market("x", limit=3)
    r5 = search_market("x", limit=5)
    assert len(r3) == 3
    assert len(r5) == 5


def test_search_market_provider_error_returns_partial(monkeypatch):
    """If one provider raises, the others still contribute results."""
    eq = [
        {
            "symbol": "AAPL",
            "name": "Apple Inc.",
            "asset_class": "equity",
            "exchange": "NASDAQ",
            "price_source": "yahoo",
            "price_source_id": "AAPL",
        }
    ]
    monkeypatch.setattr(prices_mod, "_yahoo_equity_candidates", lambda q, n: eq)
    monkeypatch.setattr(
        prices_mod,
        "_coingecko_candidates",
        lambda q, n: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    monkeypatch.setattr(prices_mod, "_metal_candidates", lambda q: [])
    # Should NOT raise; should return equity results
    results = search_market("apple", limit=8)
    assert len(results) >= 1
    assert results[0]["symbol"] == "AAPL"


def test_search_market_merges_all_asset_classes(monkeypatch):
    """Results from equity, crypto, and metal providers are all present in output."""
    eq = [
        {
            "symbol": "AAPL",
            "name": "Apple",
            "asset_class": "equity",
            "exchange": "NASDAQ",
            "price_source": "yahoo",
            "price_source_id": "AAPL",
        }
    ]
    crypto = [
        {
            "symbol": "AAVE",
            "name": "Aave",
            "asset_class": "crypto",
            "exchange": "",
            "price_source": "coingecko",
            "price_source_id": "aave",
        }
    ]
    metal = [
        {
            "symbol": "GOLD",
            "name": "Gold (spot)",
            "asset_class": "commodity",
            "exchange": "Metal",
            "price_source": "yahoo",
            "price_source_id": "GC=F",
        }
    ]
    _patch_helpers(monkeypatch, equity=eq, crypto=crypto, metal=metal)
    results = search_market("a", limit=8)
    classes = {r["asset_class"] for r in results}
    assert "equity" in classes
    assert "crypto" in classes
    assert "commodity" in classes


# ── integration test (requires network) ──────────────────────────────────────


@pytest.mark.integration
def test_search_market_real_aapl():
    """Integration: search 'AAPL' returns at least one equity result with correct shape."""
    from backend.services.prices import _market_search_cache

    _market_search_cache.clear()
    results = search_market("AAPL", limit=5)
    assert len(results) > 0
    r = results[0]
    required_keys = {"symbol", "name", "asset_class", "exchange", "price_source", "price_source_id"}
    assert required_keys <= set(r.keys())
    assert r["asset_class"] == "equity"
    assert r["price_source"] == "yahoo"


@pytest.mark.integration
def test_search_market_real_bitcoin():
    """Integration: search 'bitcoin' returns at least one crypto result."""
    from backend.services.prices import _market_search_cache

    _market_search_cache.clear()
    results = search_market("bitcoin", limit=5)
    assert any(r["asset_class"] == "crypto" for r in results)


@pytest.mark.integration
def test_search_market_real_gold():
    """Integration: search 'gold' returns at least one commodity result."""
    from backend.services.prices import _market_search_cache

    _market_search_cache.clear()
    results = search_market("gold", limit=5)
    assert any(r["asset_class"] == "commodity" for r in results)


# ── transport: Yahoo search rides yfinance's managed session ─────────────────
# A python.org macOS Python whose ``Install Certificates.command`` was never run has an
# EMPTY stdlib trust store: every bare ``urllib.request.urlopen`` HTTPS call dies with
# CERTIFICATE_VERIFY_FAILED while yfinance's curl_cffi session (certifi bundle, browser
# TLS fingerprint) keeps working — so quotes and charts were fine and only the palette's
# search came back empty, silently (owner's friend, 2026-09-21).


def _install_fake_yfdata(monkeypatch, quotes: list[dict]):
    import importlib

    class _FakeResponse:
        def json(self):
            return {"quotes": quotes}

        def close(self):
            pass

    class _FakeYfData:
        def get(self, url, params=None, timeout=30):
            assert "query2.finance.yahoo.com/v1/finance/search" in url
            return _FakeResponse()

    monkeypatch.setattr(importlib.import_module("yfinance.data"), "YfData", _FakeYfData)


def test_yahoo_equity_candidates_uses_yfinance_session_not_bare_urllib(monkeypatch):
    """Bare urllib is dead (empty trust store); the yfinance session must still answer."""

    def _cert_verify_failed(*a, **k):
        raise OSError("[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed")

    monkeypatch.setattr(prices_mod.urllib.request, "urlopen", _cert_verify_failed)
    _install_fake_yfdata(
        monkeypatch,
        [{"symbol": "PLTR", "shortname": "Palantir Technologies Inc.", "quoteType": "EQUITY"}],
    )
    assert [c["symbol"] for c in _yahoo_equity_candidates("palantir", 8)] == ["PLTR"]


def test_yahoo_equity_candidates_failure_is_logged_at_warning(monkeypatch, caplog):
    """A dead provider must be visible in app.log (INFO level) — DEBUG hid it for days."""
    import importlib

    class _FakeYfData:
        def get(self, url, params=None, timeout=30):
            raise OSError("[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed")

    monkeypatch.setattr(importlib.import_module("yfinance.data"), "YfData", _FakeYfData)
    with caplog.at_level(logging.WARNING, logger="backend.services.prices"):
        assert _yahoo_equity_candidates("palantir", 8) == []
    assert any(
        r.levelno == logging.WARNING and "CERTIFICATE_VERIFY_FAILED" in r.getMessage()
        for r in caplog.records
    )
