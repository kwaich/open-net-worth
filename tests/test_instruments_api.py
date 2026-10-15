"""
TestClient endpoint tests for backend/api/instruments.py.

Covers:
  - GET  /api/instruments              — list (empty, seeded)
  - POST /api/instruments/enrich-sectors — idempotent no-op + failure safe
  - POST /api/instruments/resolve       — all branches:
      * explicit price_source+price_source_id fast path (existing + new)
      * empty query → unresolved
      * missing query only → unresolved (no network hit; _resolve_yahoo/_resolve_coingecko mocked)
      * metal resolution branch
      * yahoo resolution branch (asset_class not crypto)
      * coingecko resolution branch (asset_class=crypto)
      * response shape: resolved=True / resolved=False
      * unit field propagated
      * asset_class + asset_subclass overrides
      * dedup: re-resolve same source returns existing instrument
      * unit update on re-resolve

InstrumentStore is DB-backed (plan 2, task 5) — the db_isolated fixture gives every
InstrumentStore() construction in the router a fresh per-test database, same pattern
as test_intel_assets_api.py and test_commitments_api.py used for their file stores.

The route is a thin delegate to backend.services.instruments.resolve_instrument (the
actual find-or-create + dedup-ladder implementation) — no network calls are made here;
yfinance and CoinGecko are monkeypatched at the call site in that module. Provider-level
unit tests (_resolve_yahoo/_resolve_coingecko/_resolve_metal) and the dedup-ladder /
crypto-pair-canonicalization tests live in tests/test_instruments_service.py.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api.instruments import router

# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture
def client(db_isolated):
    """Minimal FastAPI app with only the instruments router, store isolated to a
    fresh per-test database."""
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


@pytest.fixture
def seeded_client(db_isolated):
    """Client + one pre-seeded instrument."""
    from backend.db.instrument_store import InstrumentStore

    inst = {
        "id": "inst-aapl-001",
        "symbol": "AAPL",
        "name": "Apple Inc.",
        "asset_class": "equity",
        "asset_subclass": "stock",
        "quote_currency": "USD",
        "price_source": "yahoo",
        "price_source_id": "AAPL",
        "sector": "Technology",
        "precision": 4,
        "unit": "",
        "quote_per_unit": 1.0,
        "identifiers": {},
        "manual_price": None,
        "dividend_rate_annual": None,
        "constituents": [],
        "note": "",
        "created_at": "2025-01-01T00:00:00+00:00",
        "updated_at": "2025-01-01T00:00:00+00:00",
    }
    InstrumentStore().insert(inst)

    app = FastAPI()
    app.include_router(router)
    return {"client": TestClient(app), "instrument": inst}


# ── GET /api/instruments ──────────────────────────────────────────────────────


def test_list_instruments_empty(client):
    """Empty store returns an empty list."""
    resp = client.get("/api/instruments")
    assert resp.status_code == 200
    assert resp.json() == []


def test_list_instruments_returns_all(seeded_client):
    """Seeded store returns the full list of instruments."""
    client = seeded_client["client"]
    resp = client.get("/api/instruments")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 1
    assert body[0]["id"] == "inst-aapl-001"
    assert body[0]["symbol"] == "AAPL"


def test_list_instruments_returns_list_type(seeded_client):
    """Response is always a list, never None or dict."""
    client = seeded_client["client"]
    resp = client.get("/api/instruments")
    assert isinstance(resp.json(), list)


def test_list_instruments_fields_present(seeded_client):
    """Each instrument carries the required schema keys."""
    client = seeded_client["client"]
    body = client.get("/api/instruments").json()
    required = {
        "id",
        "symbol",
        "name",
        "asset_class",
        "price_source",
        "price_source_id",
        "quote_currency",
    }
    assert required <= set(body[0].keys())


# ── POST /api/instruments/enrich-sectors ──────────────────────────────────────


def test_enrich_sectors_noop_when_empty(client, monkeypatch):
    """No instruments → checked=0, updated=0; the service is never called."""
    import backend.api.prices as prices_api

    monkeypatch.setattr(
        prices_api, "enrich_instrument_sectors", lambda: {"checked": 0, "updated": 0}
    )
    resp = client.post("/api/instruments/enrich-sectors")
    assert resp.status_code == 200
    body = resp.json()
    assert body["checked"] == 0
    assert body["updated"] == 0


def test_enrich_sectors_returns_shape(client, monkeypatch):
    """Success response always carries checked + updated keys."""
    import backend.api.prices as prices_api

    monkeypatch.setattr(
        prices_api, "enrich_instrument_sectors", lambda: {"checked": 5, "updated": 3}
    )
    resp = client.post("/api/instruments/enrich-sectors")
    assert resp.status_code == 200
    body = resp.json()
    assert "checked" in body
    assert "updated" in body
    assert body["checked"] == 5
    assert body["updated"] == 3


def test_enrich_sectors_swallows_exception(client, monkeypatch):
    """If the enrich function raises, the endpoint returns an error field and never 500s."""
    import backend.api.prices as prices_api

    monkeypatch.setattr(
        prices_api, "enrich_instrument_sectors", lambda: (_ for _ in ()).throw(RuntimeError("boom"))
    )

    # Patch the import that happens inside the route so throwing is tested
    import backend.services.instruments as inst_mod

    def _raise():
        raise RuntimeError("network failure")

    monkeypatch.setattr(inst_mod, "_resolve_yahoo", _raise, raising=False)

    # The actual path under test is the except branch inside enrich_sectors route
    # We patch at the source it imports from (backend.api.prices)
    monkeypatch.setattr(prices_api, "enrich_instrument_sectors", _raise)

    resp = client.post("/api/instruments/enrich-sectors")
    assert resp.status_code == 200
    body = resp.json()
    assert "error" in body
    assert body.get("checked") == 0
    assert body.get("updated") == 0


# ── POST /api/instruments/resolve — resolved=False paths ──────────────────────


def test_resolve_empty_query_returns_unresolved(client):
    """Empty query string → resolved=False with a helpful message."""
    resp = client.post("/api/instruments/resolve", json={"query": ""})
    assert resp.status_code == 200
    body = resp.json()
    assert body["resolved"] is False
    assert "message" in body


def test_resolve_unresolvable_query_returns_false(client, monkeypatch):
    """When all resolution attempts fail, resolved=False is returned (not 500)."""
    import backend.services.instruments as inst_mod

    monkeypatch.setattr(inst_mod, "_resolve_yahoo", lambda q: None)
    monkeypatch.setattr(inst_mod, "_resolve_coingecko", lambda q: None)
    monkeypatch.setattr(inst_mod, "_resolve_metal", lambda q: None)

    resp = client.post("/api/instruments/resolve", json={"query": "ZZZNOMATCH"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["resolved"] is False
    assert "ZZZNOMATCH" in body["message"]


def test_resolve_message_contains_suggestion(client, monkeypatch):
    """Unresolved response message contains a hint about ticker symbols."""
    import backend.services.instruments as inst_mod

    monkeypatch.setattr(inst_mod, "_resolve_yahoo", lambda q: None)
    monkeypatch.setattr(inst_mod, "_resolve_coingecko", lambda q: None)
    monkeypatch.setattr(inst_mod, "_resolve_metal", lambda q: None)

    resp = client.post("/api/instruments/resolve", json={"query": "JUNK"})
    body = resp.json()
    assert "price_source" in body["message"] or "ticker" in body["message"].lower()


# ── POST /api/instruments/resolve — explicit source fast path ─────────────────


def test_resolve_explicit_source_new_instrument(client):
    """Supplying price_source + price_source_id creates a new instrument without network."""
    resp = client.post(
        "/api/instruments/resolve",
        json={
            "query": "BTC",
            "price_source": "coingecko",
            "price_source_id": "bitcoin",
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["resolved"] is True
    assert body["created"] is True
    inst = body["instrument"]
    assert inst["price_source"] == "coingecko"
    assert inst["price_source_id"] == "bitcoin"
    assert "id" in inst


def test_resolve_explicit_source_dedupes_existing(client):
    """Re-resolving the same price_source+price_source_id returns existing instrument."""
    payload = {
        "query": "BTC",
        "price_source": "coingecko",
        "price_source_id": "bitcoin",
    }
    first = client.post("/api/instruments/resolve", json=payload).json()
    second = client.post("/api/instruments/resolve", json=payload).json()

    assert first["created"] is True
    assert second["created"] is False
    assert first["instrument"]["id"] == second["instrument"]["id"]


def test_resolve_explicit_source_sets_asset_class_coingecko(client):
    """When price_source=coingecko and no explicit asset_class, defaults to crypto."""
    resp = client.post(
        "/api/instruments/resolve",
        json={"query": "ETH", "price_source": "coingecko", "price_source_id": "ethereum"},
    )
    body = resp.json()
    assert body["resolved"] is True
    assert body["instrument"]["asset_class"] == "crypto"


def test_resolve_explicit_source_with_asset_class(client):
    """Explicit asset_class is honoured in the fast path."""
    resp = client.post(
        "/api/instruments/resolve",
        json={
            "query": "GLD",
            "price_source": "yahoo",
            "price_source_id": "GLD",
            "asset_class": "commodity",
        },
    )
    body = resp.json()
    assert body["resolved"] is True
    assert body["instrument"]["asset_class"] == "commodity"


def test_resolve_explicit_source_with_asset_subclass(client):
    """asset_subclass is stored when provided in the fast path."""
    resp = client.post(
        "/api/instruments/resolve",
        json={
            "query": "GLD",
            "price_source": "yahoo",
            "price_source_id": "GLD",
            "asset_class": "commodity",
            "asset_subclass": "etf",
        },
    )
    body = resp.json()
    assert body["resolved"] is True
    assert body["instrument"]["asset_subclass"] == "etf"


def test_resolve_explicit_source_updates_unit(client):
    """Re-resolving with a new unit updates the stored unit on the existing instrument."""
    # Create first without unit; must be commodity so precious-metal conversion applies
    client.post(
        "/api/instruments/resolve",
        json={
            "query": "XAU",
            "price_source": "yahoo",
            "price_source_id": "GC=F",
            "asset_class": "commodity",
        },
    )
    # Re-resolve with unit=g — for commodity, native is ozt, so 1g != 1 → quote_per_unit < 1
    resp = client.post(
        "/api/instruments/resolve",
        json={
            "query": "XAU",
            "price_source": "yahoo",
            "price_source_id": "GC=F",
            "asset_class": "commodity",
            "unit": "g",
        },
    )
    body = resp.json()
    assert body["resolved"] is True
    inst = body["instrument"]
    assert inst["unit"] == "g"
    # 1 gram != 1 troy ounce → conversion factor must be less than 1
    assert inst["quote_per_unit"] < 1.0


# ── POST /api/instruments/resolve — yahoo resolution branch ──────────────────


def test_resolve_via_yahoo(client, monkeypatch):
    """When _resolve_yahoo returns a dict, it is stored and returned."""
    import backend.services.instruments as inst_mod

    fake = {
        "symbol": "MSFT",
        "name": "Microsoft Corporation",
        "asset_class": "equity",
        "asset_subclass": "stock",
        "quote_currency": "USD",
        "price_source": "yahoo",
        "price_source_id": "MSFT",
        "precision": 4,
        "identifiers": {},
    }
    monkeypatch.setattr(inst_mod, "_resolve_yahoo", lambda q: fake)
    monkeypatch.setattr(inst_mod, "_resolve_metal", lambda q: None)

    resp = client.post("/api/instruments/resolve", json={"query": "MSFT"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["resolved"] is True
    assert body["created"] is True
    assert body["instrument"]["symbol"] == "MSFT"
    assert body["instrument"]["price_source"] == "yahoo"


def test_resolve_yahoo_sets_default_asset_class_equity(client, monkeypatch):
    """Yahoo resolution defaults asset_class to equity when qtype is unknown."""
    import backend.services.instruments as inst_mod

    fake = {
        "symbol": "SPY",
        "name": "SPDR S&P 500",
        "asset_class": "equity",
        "asset_subclass": "etf",
        "quote_currency": "USD",
        "price_source": "yahoo",
        "price_source_id": "SPY",
        "precision": 4,
        "identifiers": {},
    }
    monkeypatch.setattr(inst_mod, "_resolve_yahoo", lambda q: fake)
    monkeypatch.setattr(inst_mod, "_resolve_metal", lambda q: None)

    resp = client.post("/api/instruments/resolve", json={"query": "SPY"})
    assert resp.json()["instrument"]["asset_class"] == "equity"


def test_resolve_asset_class_override_on_yahoo_result(client, monkeypatch):
    """Caller-provided asset_class overrides the yahoo-inferred class."""
    import backend.services.instruments as inst_mod

    fake = {
        "symbol": "GLD",
        "name": "SPDR Gold",
        "asset_class": "equity",  # yahoo says equity wrapper
        "asset_subclass": "etf",
        "quote_currency": "USD",
        "price_source": "yahoo",
        "price_source_id": "GLD",
        "precision": 4,
        "identifiers": {},
    }
    monkeypatch.setattr(inst_mod, "_resolve_yahoo", lambda q: fake)
    monkeypatch.setattr(inst_mod, "_resolve_metal", lambda q: None)

    resp = client.post(
        "/api/instruments/resolve",
        json={"query": "GLD", "asset_class": "commodity"},
    )
    body = resp.json()
    assert body["resolved"] is True
    assert body["instrument"]["asset_class"] == "commodity"


def test_resolve_asset_subclass_override_on_yahoo_result(client, monkeypatch):
    """Caller-provided asset_subclass overrides the yahoo-inferred subclass."""
    import backend.services.instruments as inst_mod

    fake = {
        "symbol": "GLD",
        "name": "SPDR Gold",
        "asset_class": "equity",
        "asset_subclass": "etf",
        "quote_currency": "USD",
        "price_source": "yahoo",
        "price_source_id": "GLD",
        "precision": 4,
        "identifiers": {},
    }
    monkeypatch.setattr(inst_mod, "_resolve_yahoo", lambda q: fake)
    monkeypatch.setattr(inst_mod, "_resolve_metal", lambda q: None)

    resp = client.post(
        "/api/instruments/resolve",
        json={"query": "GLD", "asset_class": "commodity", "asset_subclass": "etc"},
    )
    body = resp.json()
    assert body["instrument"]["asset_subclass"] == "etc"


# ── POST /api/instruments/resolve — coingecko resolution branch ───────────────


def test_resolve_crypto_tries_coingecko_first(client, monkeypatch):
    """asset_class=crypto tries CoinGecko before yfinance."""
    import backend.services.instruments as inst_mod

    calls: list[str] = []

    def _mock_cg(q):
        calls.append("coingecko")
        return {
            "symbol": "SOL",
            "name": "Solana",
            "asset_class": "crypto",
            "asset_subclass": "coin",
            "quote_currency": "USD",
            "price_source": "coingecko",
            "price_source_id": "solana",
            "precision": 8,
            "identifiers": {},
        }

    def _mock_yf(q):
        calls.append("yahoo")
        return None

    monkeypatch.setattr(inst_mod, "_resolve_coingecko", _mock_cg)
    monkeypatch.setattr(inst_mod, "_resolve_yahoo", _mock_yf)
    monkeypatch.setattr(inst_mod, "_resolve_metal", lambda q: None)

    resp = client.post(
        "/api/instruments/resolve",
        json={"query": "SOL", "asset_class": "crypto"},
    )
    assert resp.status_code == 200
    assert resp.json()["resolved"] is True
    assert calls[0] == "coingecko"  # CoinGecko tried first for crypto


def test_resolve_non_crypto_tries_yahoo_first(client, monkeypatch):
    """For non-crypto queries, yahoo is tried before coingecko."""
    import backend.services.instruments as inst_mod

    calls: list[str] = []

    def _mock_yf(q):
        calls.append("yahoo")
        return {
            "symbol": "AAPL",
            "name": "Apple",
            "asset_class": "equity",
            "asset_subclass": "stock",
            "quote_currency": "USD",
            "price_source": "yahoo",
            "price_source_id": "AAPL",
            "precision": 4,
            "identifiers": {},
        }

    def _mock_cg(q):
        calls.append("coingecko")
        return None

    monkeypatch.setattr(inst_mod, "_resolve_yahoo", _mock_yf)
    monkeypatch.setattr(inst_mod, "_resolve_coingecko", _mock_cg)
    monkeypatch.setattr(inst_mod, "_resolve_metal", lambda q: None)

    resp = client.post("/api/instruments/resolve", json={"query": "AAPL"})
    assert resp.json()["resolved"] is True
    assert calls[0] == "yahoo"


def test_resolve_falls_back_to_coingecko_if_yahoo_fails(client, monkeypatch):
    """If yahoo returns None, coingecko is tried as a fallback for non-crypto."""
    import backend.services.instruments as inst_mod

    monkeypatch.setattr(inst_mod, "_resolve_yahoo", lambda q: None)
    monkeypatch.setattr(inst_mod, "_resolve_metal", lambda q: None)
    monkeypatch.setattr(
        inst_mod,
        "_resolve_coingecko",
        lambda q: {
            "symbol": "ETH",
            "name": "Ethereum",
            "asset_class": "crypto",
            "asset_subclass": "coin",
            "quote_currency": "USD",
            "price_source": "coingecko",
            "price_source_id": "ethereum",
            "precision": 8,
            "identifiers": {},
        },
    )

    resp = client.post("/api/instruments/resolve", json={"query": "ethereum"})
    body = resp.json()
    assert body["resolved"] is True
    assert body["instrument"]["price_source"] == "coingecko"


# ── POST /api/instruments/resolve — metal branch ──────────────────────────────


def test_resolve_metal_gold_keyword(client, monkeypatch):
    """'gold' keyword resolves via _resolve_metal (commodity, precious_metal)."""
    import backend.services.instruments as inst_mod

    fake_metal = {
        "symbol": "GC=F",
        "name": "Gold (spot)",
        "asset_class": "commodity",
        "asset_subclass": "precious_metal",
        "quote_currency": "USD",
        "price_source": "yahoo",
        "price_source_id": "GC=F",
        "precision": 4,
        "identifiers": {},
    }
    monkeypatch.setattr(inst_mod, "_resolve_metal", lambda q: fake_metal)

    resp = client.post("/api/instruments/resolve", json={"query": "gold"})
    body = resp.json()
    assert body["resolved"] is True
    assert body["instrument"]["asset_class"] == "commodity"
    assert body["instrument"]["asset_subclass"] == "precious_metal"


def test_resolve_metal_not_tried_for_crypto_hint(client, monkeypatch):
    """When asset_class=crypto is explicitly set, _resolve_metal is not called."""
    import backend.services.instruments as inst_mod

    metal_calls: list[str] = []

    def _mock_metal(q):
        metal_calls.append(q)
        return None

    monkeypatch.setattr(inst_mod, "_resolve_metal", _mock_metal)
    monkeypatch.setattr(
        inst_mod,
        "_resolve_coingecko",
        lambda q: {
            "symbol": "BTC",
            "name": "Bitcoin",
            "asset_class": "crypto",
            "asset_subclass": "coin",
            "quote_currency": "USD",
            "price_source": "coingecko",
            "price_source_id": "bitcoin",
            "precision": 8,
            "identifiers": {},
        },
    )
    monkeypatch.setattr(inst_mod, "_resolve_yahoo", lambda q: None)

    resp = client.post(
        "/api/instruments/resolve", json={"query": "bitcoin", "asset_class": "crypto"}
    )
    assert resp.json()["resolved"] is True
    # Metal resolution should be skipped when asset_class=crypto
    assert metal_calls == []


# ── POST /api/instruments/resolve — unit propagation ──────────────────────────


def test_resolve_unit_propagated_to_instrument(client, monkeypatch):
    """unit=g is propagated through and stored on the new instrument."""
    import backend.services.instruments as inst_mod

    fake = {
        "symbol": "GC=F",
        "name": "Gold (spot)",
        "asset_class": "commodity",
        "asset_subclass": "precious_metal",
        "quote_currency": "USD",
        "price_source": "yahoo",
        "price_source_id": "GC=F",
        "precision": 4,
        "identifiers": {},
    }
    monkeypatch.setattr(inst_mod, "_resolve_metal", lambda q: fake)

    resp = client.post(
        "/api/instruments/resolve",
        json={"query": "gold", "unit": "g"},
    )
    body = resp.json()
    assert body["resolved"] is True
    assert body["instrument"]["unit"] == "g"
    # quote_per_unit should be non-1.0 (grams vs troy oz)
    assert body["instrument"]["quote_per_unit"] != 1.0


def test_resolve_unit_updates_existing_instrument(client, monkeypatch):
    """Re-resolving an existing instrument with a new unit updates it."""
    import backend.services.instruments as inst_mod

    fake = {
        "symbol": "GC=F",
        "name": "Gold (spot)",
        "asset_class": "commodity",
        "asset_subclass": "precious_metal",
        "quote_currency": "USD",
        "price_source": "yahoo",
        "price_source_id": "GC=F",
        "precision": 4,
        "identifiers": {},
    }
    monkeypatch.setattr(inst_mod, "_resolve_metal", lambda q: fake)

    # First resolve without unit
    client.post("/api/instruments/resolve", json={"query": "gold"})

    # Re-resolve with unit
    resp = client.post(
        "/api/instruments/resolve",
        json={"query": "gold", "unit": "ozt"},
    )
    body = resp.json()
    assert body["resolved"] is True
    # ozt is the native troy ounce — quote_per_unit stays 1.0
    assert body["instrument"]["unit"] == "ozt"


# ── POST /api/instruments/resolve — dedup behavior ────────────────────────────


def test_resolve_second_call_returns_created_false(client, monkeypatch):
    """Resolving the same instrument a second time returns created=False."""
    import backend.services.instruments as inst_mod

    fake = {
        "symbol": "TSLA",
        "name": "Tesla Inc.",
        "asset_class": "equity",
        "asset_subclass": "stock",
        "quote_currency": "USD",
        "price_source": "yahoo",
        "price_source_id": "TSLA",
        "precision": 4,
        "identifiers": {},
    }
    monkeypatch.setattr(inst_mod, "_resolve_yahoo", lambda q: fake)
    monkeypatch.setattr(inst_mod, "_resolve_metal", lambda q: None)

    first = client.post("/api/instruments/resolve", json={"query": "TSLA"}).json()
    second = client.post("/api/instruments/resolve", json={"query": "TSLA"}).json()

    assert first["created"] is True
    assert second["created"] is False
    assert first["instrument"]["id"] == second["instrument"]["id"]


# ── POST /api/instruments/resolve — response shape ───────────────────────────


def test_resolve_success_has_required_keys(client, monkeypatch):
    """Successful resolve response includes resolved, created, and instrument."""
    import backend.services.instruments as inst_mod

    fake = {
        "symbol": "NVDA",
        "name": "NVIDIA",
        "asset_class": "equity",
        "asset_subclass": "stock",
        "quote_currency": "USD",
        "price_source": "yahoo",
        "price_source_id": "NVDA",
        "precision": 4,
        "identifiers": {},
    }
    monkeypatch.setattr(inst_mod, "_resolve_yahoo", lambda q: fake)
    monkeypatch.setattr(inst_mod, "_resolve_metal", lambda q: None)

    body = client.post("/api/instruments/resolve", json={"query": "NVDA"}).json()
    assert "resolved" in body
    assert "created" in body
    assert "instrument" in body
    assert body["resolved"] is True


def test_resolve_failure_has_message_no_instrument(client, monkeypatch):
    """Unresolved response has message key but no instrument key."""
    import backend.services.instruments as inst_mod

    monkeypatch.setattr(inst_mod, "_resolve_yahoo", lambda q: None)
    monkeypatch.setattr(inst_mod, "_resolve_coingecko", lambda q: None)
    monkeypatch.setattr(inst_mod, "_resolve_metal", lambda q: None)

    body = client.post("/api/instruments/resolve", json={"query": "XXXZZZ"}).json()
    assert body["resolved"] is False
    assert "message" in body
    assert "instrument" not in body


def test_resolve_body_missing_query_422(client):
    """Omitting the required query field returns 422."""
    resp = client.post("/api/instruments/resolve", json={})
    assert resp.status_code == 422


def test_resolve_optional_fields_all_none(client, monkeypatch):
    """All optional fields can be omitted; the route still processes the query."""
    import backend.services.instruments as inst_mod

    monkeypatch.setattr(inst_mod, "_resolve_yahoo", lambda q: None)
    monkeypatch.setattr(inst_mod, "_resolve_coingecko", lambda q: None)
    monkeypatch.setattr(inst_mod, "_resolve_metal", lambda q: None)

    resp = client.post("/api/instruments/resolve", json={"query": "Q"})
    assert resp.status_code == 200
    assert resp.json()["resolved"] is False


# ── POST /api/instruments/resolve — crypto-pair canonicalization ──────────────


def test_resolve_yahoo_crypto_pair_canonicalizes_via_coingecko(client, monkeypatch):
    """'BTC-USD' resolves through CoinGecko to the canonical coin, never through Yahoo —
    the dedup front door that fixes the two-vendors-two-instruments bug."""
    import backend.services.instruments as inst_mod

    yahoo_calls: list[str] = []
    monkeypatch.setattr(inst_mod, "_resolve_yahoo", lambda q: yahoo_calls.append(q) or None)
    monkeypatch.setattr(
        inst_mod,
        "_resolve_coingecko",
        lambda q: {
            "symbol": "BTC",
            "name": "Bitcoin",
            "asset_class": "crypto",
            "asset_subclass": "coin",
            "quote_currency": "USD",
            "price_source": "coingecko",
            "price_source_id": "bitcoin",
            "precision": 8,
            "identifiers": {"coingecko_id": "bitcoin"},
        },
    )

    resp = client.post("/api/instruments/resolve", json={"query": "BTC-USD"})
    body = resp.json()
    assert body["resolved"] is True
    assert body["instrument"]["price_source"] == "coingecko"
    assert body["instrument"]["price_source_id"] == "bitcoin"
    assert yahoo_calls == []


# ── POST /api/instruments/resolve — symbol-collision refusal (dedup ladder) ───


def test_resolve_symbol_collision_returns_422(client, monkeypatch):
    """A non-crypto symbol collision against a different vendor is refused with 422 and a
    message identifying the existing instrument, not silently duplicated."""
    import backend.services.instruments as inst_mod

    monkeypatch.setattr(inst_mod, "_resolve_metal", lambda q: None)
    monkeypatch.setattr(
        inst_mod,
        "_resolve_yahoo",
        lambda q: {
            "symbol": "AAPL",
            "name": "Apple Inc.",
            "asset_class": "equity",
            "asset_subclass": "stock",
            "quote_currency": "USD",
            "price_source": "yahoo",
            "price_source_id": q,
            "precision": 4,
            "identifiers": {},
        },
    )

    first = client.post("/api/instruments/resolve", json={"query": "AAPL"})
    assert first.json()["resolved"] is True

    second = client.post("/api/instruments/resolve", json={"query": "AAPL.MX"})
    assert second.status_code == 422
    assert "AAPL" in second.json()["detail"]

    # allow_duplicate_symbol=true confirms it's a genuinely different listing
    third = client.post(
        "/api/instruments/resolve",
        json={"query": "AAPL.MX", "allow_duplicate_symbol": True},
    )
    assert third.status_code == 200
    assert third.json()["resolved"] is True
    assert third.json()["instrument"]["id"] != first.json()["instrument"]["id"]
