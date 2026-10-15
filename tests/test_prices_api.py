"""TestClient endpoint tests for GET /api/prices.

Covers the sync->async conversion (see ADR/commit: get_prices used to be a plain `def` route,
so FastAPI dispatched it through Starlette's shared sync-route thread pool — the same pool every
other sync `def` endpoint, including cheap CPU-bound ones like /api/forecast-scorecard, queues
through. A slow, real Yahoo/CoinGecko network call there could starve unrelated requests. Now
it's `async def` + `asyncio.to_thread`, which runs on asyncio's own default executor instead.
"""

from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient

import backend.api.prices as prices_api
from backend.main import app


@pytest.fixture
def client():
    return TestClient(app)


def test_get_prices_is_async_not_a_shared_pool_sync_route():
    """Regression: must stay `async def` so it never shares Starlette's sync thread pool with
    unrelated cheap endpoints — reverting to `def` reintroduces the queueing bug."""
    assert asyncio.iscoroutinefunction(prices_api.get_prices)


def test_get_prices_passes_through_fetch_live_result(client, monkeypatch):
    monkeypatch.setattr(
        prices_api,
        "fetch_live",
        lambda tickers, currencies, include_names=False, *, allow_stale=False: {
            "prices": {"AAPL": 200.0},
            "prev_prices": {"AAPL": 195.0},
            "fx": {"USD": 1.0},
        },
    )
    resp = client.get("/api/prices?tickers=AAPL&fx=USD")
    assert resp.status_code == 200
    body = resp.json()
    assert body["prices"]["AAPL"] == 200.0
    assert body["fx"]["USD"] == 1.0


def test_get_prices_parses_ticker_and_currency_lists(client, monkeypatch):
    captured = {}

    def _capture(tickers, currencies, include_names=False, *, allow_stale=False):
        captured["tickers"] = tickers
        captured["currencies"] = currencies
        captured["include_names"] = include_names
        captured["allow_stale"] = allow_stale
        return {"prices": {}, "prev_prices": {}, "fx": {}}

    monkeypatch.setattr(prices_api, "fetch_live", _capture)
    resp = client.get("/api/prices?tickers=AAPL, MSFT&fx=USD, SGD&include_names=true")
    assert resp.status_code == 200
    assert captured["tickers"] == ["AAPL", "MSFT"]
    assert captured["currencies"] == ["USD", "SGD"]
    assert captured["include_names"] is True
    # The interactive endpoint must never wait on a throttled provider: last-known
    # quotes now, refresh in the background (ADR 2026-08-12-provider-fetches-never-
    # block-interactive-requests).
    assert captured["allow_stale"] is True


def test_get_prices_fetch_failure_is_not_500(client, monkeypatch):
    def _boom(tickers, currencies, include_names=False, *, allow_stale=False):
        raise RuntimeError("yahoo down")

    monkeypatch.setattr(prices_api, "fetch_live", _boom)
    resp = client.get("/api/prices?tickers=AAPL&fx=USD")
    assert resp.status_code == 200
    assert resp.json()["error"] == "yahoo down"
