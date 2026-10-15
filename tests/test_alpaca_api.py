"""REST smoke for the Alpaca endpoints (GET status / PUT config).

TestClient on a bare app with ONLY the alpaca router mounted; the store is DB-backed and
isolated via `db_isolated`. The key-pair probe is mocked (NO real network). Verifies: the
key pair is write-only (never returned), a probed key pair enables the connector + reports
has_keys, the environment default is paper, and a bad key pair is rejected at config time.

``POST /api/alpaca/sync`` was REMOVED by RFC ``connectors-as-data-sources`` — fetching
positions/cash is no longer a scheduled poll or a REST action, it happens only inside a
Lucius turn via the ``alpaca_fetch_positions`` tool (see ``tests/test_alpaca_service.py``
for the fetch/normalize coverage, and the connector-fetch-tool tests for the booking path).

``PUT /api/alpaca/trading`` was REMOVED 2026-07-19 with the rest of the trading half — see
``docs/adr/2026-07-19-remove-alpaca-trading-half.md``. Its four tests went with it; the
route's absence is pinned by ``test_trading_route_is_gone`` below.
"""

from __future__ import annotations

import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


@pytest.fixture
def client(db_isolated, monkeypatch):
    for var in ("ALPACA_API_KEY_ID", "ALPACA_API_SECRET_KEY", "ALPACA_ENV", "ALPACA_ENABLED"):
        monkeypatch.delenv(var, raising=False)

    from backend.services.aggregation import alpaca as svc

    # Probe always succeeds — no network.
    monkeypatch.setattr(svc, "fetch_account", lambda base, k, s: {"account_number": "ALP-1"})

    app = FastAPI()
    from backend.api.alpaca_api import router

    app.include_router(router)
    return TestClient(app)


def test_status_default_paper_not_configured(client):
    r = client.get("/api/alpaca/status")
    assert r.status_code == 200
    body = r.json()
    assert body["configured"] is False
    assert body["has_keys"] is False
    assert body["environment"] == "paper"
    assert "api_key_id" not in body
    assert "secret_key" not in body


def test_put_validates_and_hides_keys(client):
    r = client.put(
        "/api/alpaca/config",
        json={"api_key_id": "PKLIVE", "secret_key": "supersecret", "environment": "paper"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["has_keys"] is True
    assert body["enabled"] is True  # a freshly-validated key pair enables by default
    assert body["configured"] is True
    blob = json.dumps(body)
    assert "PKLIVE" not in blob
    assert "supersecret" not in blob


def test_put_half_credential_rejected(client):
    r = client.put("/api/alpaca/config", json={"api_key_id": "PKONLY"})
    assert r.status_code == 422


def test_trading_route_is_gone(client):
    """The removal is the contract: the trading kill-switch endpoint must 404, and the config
    endpoint must not resurrect the flag by accepting it."""
    client.put("/api/alpaca/config", json={"api_key_id": "k", "secret_key": "s"})
    r = client.put(
        "/api/alpaca/trading",
        json={"trading_enabled": True},
        headers={"X-Requested-With": "fetch"},
    )
    assert r.status_code == 404
    body = client.put("/api/alpaca/config", json={"trading_enabled": True}).json()
    assert "trading_enabled" not in body


def test_sync_route_is_gone(client):
    """The removal is the contract: fetching is a Lucius-turn tool call now, never a REST
    action — pin the route's absence the same way the trading route's is pinned above."""
    r = client.post("/api/alpaca/sync")
    assert r.status_code == 404


def test_put_invalid_keys_returns_422(client, monkeypatch):
    import httpx

    from backend.services.aggregation import alpaca as svc

    def _boom(base, k, s):
        resp = httpx.Response(403, request=httpx.Request("GET", "https://x"))
        raise httpx.HTTPStatusError("forbidden", request=resp.request, response=resp)

    monkeypatch.setattr(svc, "fetch_account", _boom)
    r = client.put("/api/alpaca/config", json={"api_key_id": "bad", "secret_key": "bad"})
    assert r.status_code == 422
