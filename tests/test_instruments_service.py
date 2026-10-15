"""
Unit tests for backend/services/instruments.py — the resolve-instrument implementation
(the low-level provider adapters + the resolve-time dedup ladder / crypto-pair
canonicalization front door).

backend/api/instruments.py's REST route and backend/ai/tools/portfolio.py's
resolve_instrument tool are both thin callers of this module now — their own endpoint/
tool-level behavior is covered in tests/test_instruments_api.py and
tests/test_char_tool_dispatch.py respectively.

No network calls are made; yfinance and CoinGecko are monkeypatched at the call site in
backend.services.instruments. See docs/adr/2026-07-15-instrument-identity-canonicalization-front-door.md.
"""

from __future__ import annotations

import json

import pytest

from backend.core.errors import ValidationError
from backend.db.instrument_store import InstrumentStore


@pytest.fixture
def isolated(tmp_path, monkeypatch, db_isolated):
    """Isolate InstrumentStore (DB-backed, plan 2 task 5) to a fresh per-test database."""
    return tmp_path


# ── _resolve_yahoo unit tests (network mocked via yfinance mock) ───────────────


def test_resolve_yahoo_returns_none_on_no_price(monkeypatch):
    """_resolve_yahoo returns None when yfinance gives no price for the symbol."""
    import sys

    import backend.services.instruments as inst_mod

    class _FakeYF:
        class Ticker:
            def __init__(self, sym):
                pass

            @property
            def fast_info(self):
                return {"lastPrice": None, "regularMarketPrice": None}

    sys.modules["yfinance"] = _FakeYF()
    try:
        result = inst_mod._resolve_yahoo("BADTICKER")
        assert result is None
    finally:
        del sys.modules["yfinance"]


def test_resolve_yahoo_returns_dict_on_valid_price(monkeypatch):
    """_resolve_yahoo returns a complete instrument dict when yfinance provides a price."""
    import sys

    import backend.services.instruments as inst_mod

    class _FakeYF:
        class Ticker:
            def __init__(self, sym):
                self._sym = sym

            @property
            def fast_info(self):
                return {
                    "lastPrice": 189.5,
                    "currency": "USD",
                    "quoteType": "EQUITY",
                }

    # Also stub _fetch_name to avoid network in the name-fetch branch
    monkeypatch.setattr(
        "backend.services.prices._fetch_name",
        lambda sym: ("NASDAQ", "Apple Inc.", "NasdaqGS"),
        raising=False,
    )

    sys.modules["yfinance"] = _FakeYF()
    try:
        result = inst_mod._resolve_yahoo("AAPL")
        assert result is not None
        assert result["price_source"] == "yahoo"
        assert result["price_source_id"] == "AAPL"
        assert result["quote_currency"] == "USD"
        assert result["asset_class"] == "equity"
        assert result["asset_subclass"] == "stock"
        assert result["symbol"] == "AAPL"
    finally:
        del sys.modules["yfinance"]


def test_resolve_yahoo_handles_fetch_name_exception(monkeypatch):
    """_resolve_yahoo still succeeds when _fetch_name raises (uses fallback query as name)."""
    import sys

    import backend.services.instruments as inst_mod

    class _FakeYF:
        class Ticker:
            def __init__(self, sym):
                pass

            @property
            def fast_info(self):
                return {"lastPrice": 50.0, "currency": "SGD", "quoteType": "EQUITY"}

    import backend.services.prices as prices_svc_mod

    monkeypatch.setattr(
        prices_svc_mod,
        "_fetch_name",
        lambda sym: (_ for _ in ()).throw(Exception("name fetch failed")),
    )

    sys.modules["yfinance"] = _FakeYF()
    try:
        result = inst_mod._resolve_yahoo("ONWAI.DE")
        assert result is not None
        # Falls back to query.upper() as name (not the display symbol — full query)
        assert result["name"] == "ONWAI.DE"
        # The display symbol strips the suffix; the price_source_id keeps it
        assert result["symbol"] == "ONWAI"
        assert result["price_source_id"] == "ONWAI.DE"  # suffix kept for pricing
    finally:
        del sys.modules["yfinance"]


def test_resolve_yahoo_returns_none_on_exception(monkeypatch):
    """_resolve_yahoo returns None when yfinance raises any exception (the broad except clause)."""
    import sys

    import backend.services.instruments as inst_mod

    # Inject a fake yfinance module whose Ticker always raises, simulating a crash
    class _FakeYF:
        class Ticker:
            def __init__(self, sym):
                raise RuntimeError("yfinance unavailable")

    sys.modules["yfinance"] = _FakeYF()
    try:
        result = inst_mod._resolve_yahoo("AAPL")
        assert result is None
    finally:
        del sys.modules["yfinance"]


def test_resolve_yahoo_populates_isin_when_available(monkeypatch):
    """_resolve_yahoo opportunistically stamps identifiers['isin'] from yf.Ticker.isin."""
    import sys

    import backend.services.instruments as inst_mod

    class _FakeYF:
        class Ticker:
            def __init__(self, sym):
                pass

            @property
            def fast_info(self):
                return {"lastPrice": 189.5, "currency": "USD", "quoteType": "EQUITY"}

            @property
            def isin(self):
                return "US0378331005"

    monkeypatch.setattr(
        "backend.services.prices._fetch_name",
        lambda sym: (sym, "Apple Inc.", "NasdaqGS"),
        raising=False,
    )

    sys.modules["yfinance"] = _FakeYF()
    try:
        result = inst_mod._resolve_yahoo("AAPL")
        assert result is not None
        assert result["identifiers"]["isin"] == "US0378331005"
        assert result["identifiers"]["mic"] == "NasdaqGS"
    finally:
        del sys.modules["yfinance"]


def test_resolve_yahoo_isin_failure_is_swallowed(monkeypatch):
    """A raising/garbage Ticker.isin never breaks _resolve_yahoo — just no isin identifier."""
    import sys

    import backend.services.instruments as inst_mod

    class _FakeYF:
        class Ticker:
            def __init__(self, sym):
                pass

            @property
            def fast_info(self):
                return {"lastPrice": 10.0, "currency": "USD", "quoteType": "EQUITY"}

            @property
            def isin(self):
                raise RuntimeError("isin lookup failed")

    monkeypatch.setattr(
        "backend.services.prices._fetch_name", lambda sym: (sym, "", ""), raising=False
    )

    sys.modules["yfinance"] = _FakeYF()
    try:
        result = inst_mod._resolve_yahoo("ZZZ")
        assert result is not None
        assert "isin" not in result["identifiers"]
    finally:
        del sys.modules["yfinance"]


# ── _resolve_coingecko unit tests ─────────────────────────────────────────────


def test_resolve_coingecko_returns_none_on_network_error(monkeypatch):
    """_resolve_coingecko returns None when the HTTP request fails."""
    import backend.services.instruments as inst_mod
    from backend.services import coingecko_client

    def _fail(*args, **kwargs):
        raise OSError("network unreachable")

    monkeypatch.setattr(coingecko_client, "_open_request", _fail)

    result = inst_mod._resolve_coingecko("ethereum")
    assert result is None


def test_resolve_coingecko_returns_none_on_empty_coins(monkeypatch):
    """_resolve_coingecko returns None when the search returns an empty coins list."""
    import urllib.request

    import backend.services.instruments as inst_mod

    class _FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            pass

        def read(self):
            return json.dumps({"coins": []}).encode()

    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: _FakeResponse())

    result = inst_mod._resolve_coingecko("zzznomatch")
    assert result is None


def test_resolve_coingecko_picks_highest_market_cap(monkeypatch):
    """When multiple coins match, the one with the lowest market_cap_rank (highest cap) wins."""
    import urllib.request

    import backend.services.instruments as inst_mod

    coins = [
        {"id": "shiba-inu", "name": "Shiba Inu", "symbol": "SHIB", "market_cap_rank": 15},
        {"id": "solana", "name": "Solana", "symbol": "SOL", "market_cap_rank": 5},
        {"id": "some-other", "name": "SomeOther", "symbol": "SOL", "market_cap_rank": 200},
    ]

    class _FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            pass

        def read(self):
            return json.dumps({"coins": coins}).encode()

    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: _FakeResponse())

    result = inst_mod._resolve_coingecko("sol")
    assert result is not None
    assert result["price_source_id"] == "solana"


def test_resolve_coingecko_handles_null_market_cap_rank(monkeypatch):
    """Coins with null market_cap_rank are sorted after ranked ones (treated as 1e9)."""
    import urllib.request

    import backend.services.instruments as inst_mod

    coins = [
        {"id": "unknown-coin", "name": "Unknown", "symbol": "UNK", "market_cap_rank": None},
        {"id": "bitcoin", "name": "Bitcoin", "symbol": "BTC", "market_cap_rank": 1},
    ]

    class _FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            pass

        def read(self):
            return json.dumps({"coins": coins}).encode()

    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: _FakeResponse())

    result = inst_mod._resolve_coingecko("btc")
    assert result is not None
    assert result["price_source_id"] == "bitcoin"


def test_resolve_coingecko_result_shape(monkeypatch):
    """Successful CoinGecko result has all required keys, including identifiers.coingecko_id."""
    import urllib.request

    import backend.services.instruments as inst_mod

    coins = [{"id": "bitcoin", "name": "Bitcoin", "symbol": "BTC", "market_cap_rank": 1}]

    class _FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            pass

        def read(self):
            return json.dumps({"coins": coins}).encode()

    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: _FakeResponse())

    result = inst_mod._resolve_coingecko("bitcoin")
    assert result is not None
    required = {
        "symbol",
        "name",
        "asset_class",
        "asset_subclass",
        "quote_currency",
        "price_source",
        "price_source_id",
        "precision",
        "identifiers",
    }
    assert required <= set(result.keys())
    assert result["asset_class"] == "crypto"
    assert result["price_source"] == "coingecko"
    assert result["symbol"] == "BTC"
    assert result["identifiers"]["coingecko_id"] == "bitcoin"


# ── _resolve_metal unit tests ──────────────────────────────────────────────────


def test_resolve_metal_gold(monkeypatch):
    """'gold' resolves to GC=F via the curated map."""
    import backend.services.instruments as inst_mod

    yahoo_result = {
        "symbol": "GC=F",
        "name": "GC=F",
        "asset_class": "equity",
        "asset_subclass": "future",
        "quote_currency": "USD",
        "price_source": "yahoo",
        "price_source_id": "GC=F",
        "precision": 4,
        "identifiers": {},
    }
    monkeypatch.setattr(inst_mod, "_resolve_yahoo", lambda q: yahoo_result if q == "GC=F" else None)

    result = inst_mod._resolve_metal("gold")
    assert result is not None
    assert result["asset_class"] == "commodity"
    assert result["asset_subclass"] == "precious_metal"
    assert result["name"] == "Gold (spot)"


def test_resolve_metal_case_insensitive(monkeypatch):
    """Metal lookup is case-insensitive."""
    import backend.services.instruments as inst_mod

    yahoo_result = {
        "symbol": "GC=F",
        "name": "GC=F",
        "asset_class": "equity",
        "asset_subclass": "future",
        "quote_currency": "USD",
        "price_source": "yahoo",
        "price_source_id": "GC=F",
        "precision": 4,
        "identifiers": {},
    }
    monkeypatch.setattr(inst_mod, "_resolve_yahoo", lambda q: yahoo_result if q == "GC=F" else None)

    assert inst_mod._resolve_metal("GOLD") is not None
    assert inst_mod._resolve_metal("Gold") is not None


def test_resolve_metal_unknown_returns_none():
    """Non-metal query returns None from _resolve_metal."""
    import backend.services.instruments as inst_mod

    result = inst_mod._resolve_metal("AAPL")
    assert result is None


def test_resolve_metal_xau_alias(monkeypatch):
    """XAU ISO code resolves to gold spot."""
    import backend.services.instruments as inst_mod

    yahoo_result = {
        "symbol": "GC=F",
        "name": "GC=F",
        "asset_class": "equity",
        "asset_subclass": "future",
        "quote_currency": "USD",
        "price_source": "yahoo",
        "price_source_id": "GC=F",
        "precision": 4,
        "identifiers": {},
    }
    monkeypatch.setattr(inst_mod, "_resolve_yahoo", lambda q: yahoo_result if q == "GC=F" else None)

    result = inst_mod._resolve_metal("xau")
    assert result is not None
    assert result["name"] == "Gold (spot)"


def test_resolve_metal_returns_none_when_yahoo_fails(monkeypatch):
    """_resolve_metal returns None if _resolve_yahoo returns None for the spot symbol."""
    import backend.services.instruments as inst_mod

    monkeypatch.setattr(inst_mod, "_resolve_yahoo", lambda q: None)

    result = inst_mod._resolve_metal("gold")
    assert result is None


# ── _split_crypto_pair (Yahoo crypto-pair → canonical coin detection) ──────────


@pytest.mark.parametrize(
    "query,expected",
    [
        ("BTC-USD", "BTC"),
        ("btc-usd", "BTC"),
        ("eth-eur", "ETH"),
        ("SOL-SGD", "SOL"),
        ("DOGE-JPY", "DOGE"),
    ],
)
def test_split_crypto_pair_recognizes_yahoo_pairs(query, expected):
    from backend.services.instruments import _split_crypto_pair

    assert _split_crypto_pair(query) == expected


@pytest.mark.parametrize(
    "query",
    [
        "AAPL",  # no dash at all
        "BRK-B",  # a real equity share-class suffix, not a fiat code
        "GC=F",  # metal futures symbol
        "XAU-USD",  # metal code with a fiat suffix — excluded, not crypto
        "xauusd-usd",  # nonsense, base isn't a recognized 2-10 char token anyway (still filtered)
        "",
    ],
)
def test_split_crypto_pair_rejects_non_pairs(query):
    from backend.services.instruments import _split_crypto_pair

    assert _split_crypto_pair(query) is None


# ── resolve_instrument: crypto-pair canonicalization (the core bug fix) ────────


def test_resolve_btc_usd_pair_canonicalizes_to_coingecko_bitcoin(isolated, monkeypatch):
    """'BTC-USD' must resolve through CoinGecko to the SAME instrument as a bare 'bitcoin'
    query — not mint a separate yahoo-priced BTC-USD instrument (the core bug: two vendors,
    two instruments)."""
    import backend.services.instruments as inst_mod

    def _mock_cg(q):
        assert q == "BTC"  # the fiat suffix must be stripped before the coingecko search
        return {
            "symbol": "BTC",
            "name": "Bitcoin",
            "asset_class": "crypto",
            "asset_subclass": "coin",
            "quote_currency": "USD",
            "price_source": "coingecko",
            "price_source_id": "bitcoin",
            "precision": 8,
            "identifiers": {"coingecko_id": "bitcoin"},
        }

    yahoo_calls: list[str] = []
    monkeypatch.setattr(inst_mod, "_resolve_coingecko", _mock_cg)
    monkeypatch.setattr(inst_mod, "_resolve_yahoo", lambda q: yahoo_calls.append(q) or None)

    inst, created = inst_mod.resolve_instrument("BTC-USD")
    assert created is True
    assert inst["price_source"] == "coingecko"
    assert inst["price_source_id"] == "bitcoin"
    assert yahoo_calls == []  # Yahoo must never be tried for a recognized crypto pair

    # A different fiat pairing of the SAME coin must dedupe to the identical instrument.
    inst2, created2 = inst_mod.resolve_instrument("btc-sgd")
    assert created2 is False
    assert inst2["id"] == inst["id"]
    assert len(InstrumentStore().all()) == 1


def test_resolve_crypto_pair_falls_back_when_coingecko_unrecognized(isolated, monkeypatch):
    """If CoinGecko doesn't recognize the base symbol, the pair falls through to normal
    resolution (unchanged legacy behavior) instead of being force-treated as crypto."""
    import backend.services.instruments as inst_mod

    monkeypatch.setattr(inst_mod, "_resolve_coingecko", lambda q: None)
    monkeypatch.setattr(inst_mod, "_resolve_metal", lambda q: None)
    fake_yahoo = {
        "symbol": "ZZZ",
        "name": "ZZZ Corp",
        "asset_class": "equity",
        "asset_subclass": "stock",
        "quote_currency": "USD",
        "price_source": "yahoo",
        "price_source_id": "ZZZ-USD",
        "precision": 4,
        "identifiers": {},
    }
    monkeypatch.setattr(inst_mod, "_resolve_yahoo", lambda q: fake_yahoo)

    inst, created = inst_mod.resolve_instrument("ZZZ-USD")
    assert created is True
    assert inst["price_source"] == "yahoo"


def test_resolve_metal_dash_fiat_query_not_routed_to_coingecko(isolated, monkeypatch):
    """A dash-suffixed metal-like query ('XAU-USD') must never be pre-emptively treated as
    a crypto pair — metal codes are excluded from crypto-pair detection, so CoinGecko is
    never queried for the STRIPPED base 'XAU' (which would wrongly imply "this is a coin").
    The normal yahoo-then-coingecko fallback (querying the FULL string) is unaffected."""
    import backend.services.instruments as inst_mod

    cg_calls: list[str] = []
    monkeypatch.setattr(inst_mod, "_resolve_coingecko", lambda q: cg_calls.append(q) or None)
    monkeypatch.setattr(inst_mod, "_resolve_metal", lambda q: None)
    monkeypatch.setattr(inst_mod, "_resolve_yahoo", lambda q: None)

    inst_mod.resolve_instrument("XAU-USD")
    assert "XAU" not in cg_calls  # the crypto-pair front door must not have fired


# ── find_existing: the resolution ladder ────────────────────────────────────────


def _candidate(**overrides) -> dict:
    base = {
        "symbol": "AAPL",
        "name": "Apple Inc.",
        "asset_class": "equity",
        "asset_subclass": "stock",
        "quote_currency": "USD",
        "price_source": "yahoo",
        "price_source_id": "AAPL",
        "precision": 4,
        "identifiers": {},
    }
    base.update(overrides)
    return base


def test_find_existing_exact_source_match(isolated):
    from backend.services.instruments import find_existing

    store = InstrumentStore()
    existing = store.create(_candidate())
    match = find_existing(_candidate(name="Apple (re-resolved)"), store)
    assert match is not None
    assert match["id"] == existing["id"]


def test_find_existing_identifier_match_isin(isolated):
    """A different vendor/price_source_id but the SAME isin dedupes via the identifiers bag."""
    from backend.services.instruments import find_existing

    store = InstrumentStore()
    existing = store.create(
        _candidate(identifiers={"isin": "US0378331005"}, price_source_id="AAPL")
    )
    candidate = _candidate(
        price_source="yahoo",
        price_source_id="AAPL.MX",  # different vendor listing, same company
        identifiers={"isin": "US0378331005"},
    )
    match = find_existing(candidate, store)
    assert match is not None
    assert match["id"] == existing["id"]


def test_find_existing_identifier_match_coingecko_id(isolated):
    from backend.services.instruments import find_existing

    store = InstrumentStore()
    existing = store.create(
        _candidate(
            symbol="BTC",
            asset_class="crypto",
            price_source="coingecko",
            price_source_id="bitcoin",
            identifiers={"coingecko_id": "bitcoin"},
        )
    )
    candidate = _candidate(
        symbol="BTC",
        asset_class="crypto",
        price_source="manual",
        price_source_id="",
        identifiers={"coingecko_id": "bitcoin"},
    )
    match = find_existing(candidate, store)
    assert match is not None
    assert match["id"] == existing["id"]


def test_find_existing_crypto_symbol_collision_silently_reused(isolated):
    """A symbol+class collision on crypto is treated as the same coin — no refusal."""
    from backend.services.instruments import find_existing

    store = InstrumentStore()
    existing = store.create(
        _candidate(
            symbol="SOL",
            asset_class="crypto",
            price_source="coingecko",
            price_source_id="solana",
        )
    )
    candidate = _candidate(
        symbol="SOL",
        asset_class="crypto",
        price_source="yahoo",  # different vendor, no shared identifier
        price_source_id="SOL-USD",
        identifiers={},
    )
    match = find_existing(candidate, store)
    assert match is not None
    assert match["id"] == existing["id"]


def test_find_existing_non_crypto_symbol_collision_refused(isolated):
    """A non-crypto symbol collision across vendors is ambiguous — refused by default."""
    from backend.services.instruments import find_existing

    store = InstrumentStore()
    existing = store.create(_candidate())  # yahoo:AAPL
    candidate = _candidate(price_source="yahoo", price_source_id="AAPL.L")  # different listing

    with pytest.raises(ValidationError) as exc_info:
        find_existing(candidate, store)
    assert exc_info.value.context["existing_instrument_id"] == existing["id"]
    assert exc_info.value.context["existing_symbol"] == "AAPL"


def test_find_existing_non_crypto_collision_override_creates_distinct(isolated):
    """allow_duplicate_symbol=True suppresses the refusal — caller proceeds to create."""
    from backend.services.instruments import find_existing

    store = InstrumentStore()
    store.create(_candidate())
    candidate = _candidate(price_source="yahoo", price_source_id="AAPL.L")

    match = find_existing(candidate, store, allow_duplicate_symbol=True)
    assert match is None  # signals "genuinely new" to the caller


def test_find_existing_no_collision_returns_none(isolated):
    from backend.services.instruments import find_existing

    store = InstrumentStore()
    store.create(_candidate())
    candidate = _candidate(symbol="MSFT", price_source_id="MSFT")

    assert find_existing(candidate, store) is None


# ── resolve_instrument: manual instruments with no external id ─────────────────


def test_resolve_manual_no_id_dedupes_same_symbol(isolated):
    """Two manual resolves for the same symbol+class dedupe instead of silently duplicating
    (the exact-key dedupe can never fire on an empty price_source_id)."""
    import backend.services.instruments as inst_mod

    inst1, created1 = inst_mod.resolve_instrument(
        "My Startup Equity", asset_class="equity", price_source="manual", quote_currency="USD"
    )
    assert created1 is True
    assert inst1["price_source"] == "manual"
    assert inst1["price_source_id"] == ""

    with pytest.raises(ValidationError) as exc_info:
        inst_mod.resolve_instrument(
            "My Startup Equity",
            asset_class="equity",
            price_source="manual",
            quote_currency="USD",
        )
    assert exc_info.value.context["existing_instrument_id"] == inst1["id"]
    assert len(InstrumentStore().all()) == 1


def test_resolve_manual_no_id_allow_duplicate_symbol_creates_distinct(isolated):
    """allow_duplicate_symbol=true lets the user explicitly mint a second, distinct manual
    instrument with the same display symbol (e.g. two different private holdings both
    called "Seed Round")."""
    import backend.services.instruments as inst_mod

    inst1, _ = inst_mod.resolve_instrument(
        "Seed Round", asset_class="equity", price_source="manual", quote_currency="USD"
    )
    inst2, created2 = inst_mod.resolve_instrument(
        "Seed Round",
        asset_class="equity",
        price_source="manual",
        allow_duplicate_symbol=True,
        quote_currency="USD",
    )
    assert created2 is True
    assert inst2["id"] != inst1["id"]
    assert len(InstrumentStore().all()) == 2


def test_resolve_manual_with_explicit_id_still_exact_dedupes(isolated):
    """A manual instrument WITH an explicit price_source_id is unaffected — exact-key
    dedupe only, no ladder/refusal (the caller pinned an authoritative id)."""
    import backend.services.instruments as inst_mod

    inst1, created1 = inst_mod.resolve_instrument(
        "Private Fund A", price_source="manual", price_source_id="priv:fund-a", quote_currency="USD"
    )
    inst2, created2 = inst_mod.resolve_instrument(
        "Private Fund A (dup symbol ok)",
        price_source="manual",
        price_source_id="priv:fund-a",
        quote_currency="USD",
    )
    assert created1 is True
    assert created2 is False
    assert inst1["id"] == inst2["id"]


# ── resolve_instrument: explicit source stays a pure dedupe (no ladder) ────────


def test_resolve_explicit_source_symbol_collision_not_refused(isolated, monkeypatch):
    """An explicit price_source+price_source_id is authoritative — a symbol collision with
    an existing DIFFERENT instrument is allowed silently (a legitimate cross-exchange
    listing), unlike the auto-resolve/manual-no-id ladder paths."""
    import backend.services.instruments as inst_mod

    # A pin still FETCHES the entry's facts (see _pin_facts) — keep the mint offline.
    monkeypatch.setattr(
        inst_mod, "_resolve_yahoo", lambda pid: {"quote_currency": "USD", "name": f"Fake {pid}"}
    )
    inst1, created1 = inst_mod.resolve_instrument(
        "AAPL", asset_class="equity", price_source="yahoo", price_source_id="AAPL"
    )
    inst2, created2 = inst_mod.resolve_instrument(
        "AAPL", asset_class="equity", price_source="yahoo", price_source_id="AAPL.MX"
    )
    assert created1 is True
    assert created2 is True
    assert inst1["id"] != inst2["id"]


# ── resolve_by_ticker: /quote/$symbol's lookup ──────────────────────────────────


def test_resolve_by_ticker_exact_match_skips_the_ladder(isolated, monkeypatch):
    """An already-known instrument resolves via the exact price_source_id match —
    resolve_instrument (the fuzzy/provider-search ladder) must not be touched."""
    import backend.services.instruments as inst_mod

    monkeypatch.setattr(
        inst_mod, "_resolve_yahoo", lambda pid: {"quote_currency": "USD", "name": f"Fake {pid}"}
    )
    existing, _ = inst_mod.resolve_instrument(
        "ONWAI", asset_class="equity", price_source="yahoo", price_source_id="ONWAI.DE"
    )

    def _boom(*_a, **_k):
        raise AssertionError("resolve_instrument must not be called for an exact match")

    monkeypatch.setattr(inst_mod, "resolve_instrument", _boom)
    inst, created = inst_mod.resolve_by_ticker("ONWAI.DE")
    assert inst["id"] == existing["id"]
    assert created is False


def test_resolve_by_ticker_case_insensitive(isolated, monkeypatch):
    import backend.services.instruments as inst_mod

    monkeypatch.setattr(
        inst_mod, "_resolve_yahoo", lambda pid: {"quote_currency": "USD", "name": f"Fake {pid}"}
    )
    existing, _ = inst_mod.resolve_instrument(
        "ONWAI", asset_class="equity", price_source="yahoo", price_source_id="ONWAI.DE"
    )
    inst, created = inst_mod.resolve_by_ticker("onwai.de")
    assert inst["id"] == existing["id"]
    assert created is False


def test_resolve_by_ticker_falls_through_for_new_ticker(isolated, monkeypatch):
    """A ticker not yet in the store falls through to resolve_instrument's
    find-or-create ladder."""
    import backend.services.instruments as inst_mod

    sentinel = {"id": "test-inst-new", "price_source_id": "NEW.US"}
    monkeypatch.setattr(inst_mod, "resolve_instrument", lambda q, **_kw: (sentinel, True))
    inst, created = inst_mod.resolve_by_ticker("NEWTICK")
    assert inst is sentinel
    assert created is True


def test_resolve_by_ticker_distinguishes_same_bare_symbol(isolated, monkeypatch):
    """Regression for the real "oklo" collision — two distinct instruments can share a
    bare symbol; resolve_by_ticker must return the exact one asked for."""
    import backend.services.instruments as inst_mod

    monkeypatch.setattr(
        inst_mod, "_resolve_yahoo", lambda pid: {"quote_currency": "USD", "name": f"Fake {pid}"}
    )
    stock, _ = inst_mod.resolve_instrument(
        "OKLO", asset_class="equity", price_source="yahoo", price_source_id="OKLO"
    )
    wrapper, _ = inst_mod.resolve_instrument(
        "OKLO",
        asset_class="crypto",
        price_source="coingecko",
        price_source_id="oklo-inc-robinhood-token",
    )

    assert inst_mod.resolve_by_ticker("OKLO")[0]["id"] == stock["id"]
    assert inst_mod.resolve_by_ticker("oklo-inc-robinhood-token")[0]["id"] == wrapper["id"]


def test_resolve_by_ticker_empty_string_returns_none(isolated):
    import backend.services.instruments as inst_mod

    inst, created = inst_mod.resolve_by_ticker("")
    assert inst is None
    assert created is False


def test_resolve_by_ticker_matches_held_crypto_display_symbol_without_the_ladder(
    isolated, monkeypatch
):
    """A held crypto row's `ticker` is its DISPLAY symbol ("BTC"), not its CoinGecko
    price_source_id ("bitcoin") — services.positions._row_ticker substitutes it on
    purpose. resolve_by_ticker must still resolve "BTC" to the existing instrument
    WITHOUT touching resolve_instrument's network-capable ladder, via the general
    unique-bare-symbol fallback (not a crypto-specific rule)."""
    import backend.services.instruments as inst_mod

    existing, _ = inst_mod.resolve_instrument(
        "BTC", asset_class="crypto", price_source="coingecko", price_source_id="bitcoin"
    )
    assert existing["symbol"] == "BTC"  # sanity: mirrors how real holdings are seeded

    def _boom(*_a, **_k):
        raise AssertionError("resolve_instrument (the network-capable ladder) must not run")

    monkeypatch.setattr(inst_mod, "resolve_instrument", _boom)
    inst, created = inst_mod.resolve_by_ticker("BTC")
    assert inst["id"] == existing["id"]
    assert created is False
    assert inst_mod.resolve_by_ticker("btc")[0]["id"] == existing["id"]  # case-insensitive


def test_resolve_by_ticker_matches_unique_bare_symbol_for_any_asset_class(isolated, monkeypatch):
    """The bare-symbol fallback isn't crypto-only — regression for a real gap found post-
    implementation: commitments/fundingValue.ts prefers a position's bare `symbol` ("ONWAI")
    over its qualified `ticker` ("ONWAI.DE") when building an AssetLink, so an EQUITY holding
    can just as easily reach resolve_by_ticker with a bare symbol."""
    import backend.services.instruments as inst_mod

    monkeypatch.setattr(
        inst_mod, "_resolve_yahoo", lambda pid: {"quote_currency": "USD", "name": f"Fake {pid}"}
    )
    existing, _ = inst_mod.resolve_instrument(
        "ONWAI", asset_class="equity", price_source="yahoo", price_source_id="ONWAI.DE"
    )

    def _boom(*_a, **_k):
        raise AssertionError("resolve_instrument (the network-capable ladder) must not run")

    monkeypatch.setattr(inst_mod, "resolve_instrument", _boom)
    inst, created = inst_mod.resolve_by_ticker("ONWAI")
    assert inst["id"] == existing["id"]
    assert created is False


def test_resolve_by_ticker_ambiguous_bare_symbol_falls_through_to_the_ladder(isolated, monkeypatch):
    """When a bare symbol is shared by more than one instrument (any class) and neither one's
    price_source_id equals the query — so the exact-match step above can't disambiguate it
    either — the fallback must NOT guess: it falls through to the full ladder instead of
    blindly picking one. (The real "oklo" pair doesn't hit this path at all: the stock's own
    price_source_id IS "OKLO", so the exact match above resolves it correctly and never even
    reaches the bare-symbol fallback — see the exact-match test above for that case.)"""
    import backend.services.instruments as inst_mod

    monkeypatch.setattr(
        inst_mod, "_resolve_yahoo", lambda pid: {"quote_currency": "USD", "name": f"Fake {pid}"}
    )
    inst_mod.resolve_instrument(
        "DUP", asset_class="equity", price_source="yahoo", price_source_id="DUP.US"
    )
    inst_mod.resolve_instrument(
        "DUP", asset_class="crypto", price_source="coingecko", price_source_id="dup-coin"
    )
    monkeypatch.setattr(inst_mod, "resolve_instrument", lambda q, **_kw: ({"id": "sentinel"}, True))
    inst, created = inst_mod.resolve_by_ticker("DUP")
    assert inst == {"id": "sentinel"}
    assert created is True


# ── Integration: live provider tests (skipped in CI) ─────────────────────────


@pytest.mark.integration
def test_resolve_yahoo_aapl_live():
    """Live yfinance call resolves AAPL to an equity instrument. Requires network."""
    from backend.services.instruments import _resolve_yahoo

    result = _resolve_yahoo("AAPL")
    assert result is not None
    assert result["price_source"] == "yahoo"
    assert result["asset_class"] == "equity"
    assert result["quote_currency"] == "USD"


@pytest.mark.integration
def test_resolve_coingecko_bitcoin_live():
    """Live CoinGecko call resolves 'bitcoin' to crypto. Requires network."""
    from backend.services.instruments import _resolve_coingecko

    result = _resolve_coingecko("bitcoin")
    assert result is not None
    assert result["price_source"] == "coingecko"
    assert result["price_source_id"] == "bitcoin"
    assert result["asset_class"] == "crypto"


@pytest.mark.integration
def test_resolve_btc_usd_pair_live():
    """Live: 'BTC-USD' canonicalizes to the same CoinGecko bitcoin instrument as 'bitcoin'."""
    from backend.services.instruments import resolve_instrument

    inst, _ = resolve_instrument("BTC-USD")
    assert inst is not None
    assert inst["price_source"] == "coingecko"
    assert inst["price_source_id"] == "bitcoin"
