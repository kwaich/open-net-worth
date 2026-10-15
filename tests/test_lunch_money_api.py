"""REST smoke for the Lunch Money endpoints (GET status / PUT config).

TestClient on a bare app with ONLY the lunch_money router mounted; stores isolated via
db_isolated. Verifies: the access token is write-only (never returned), saving a token
enables the connector + reports has_access_token, and a blank token keeps the saved one.
Fetching + booking transactions is no longer a REST action — there is no more
POST /lunch-money/sync route; it happens only inside a Lucius turn
(lunch_money_fetch_transactions + add_transactions), per RFC connectors-as-data-sources.
"""

from __future__ import annotations

import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


@pytest.fixture
def client(db_isolated, monkeypatch):

    app = FastAPI()
    from backend.api.lunch_money_api import router

    app.include_router(router)
    return TestClient(app)


def test_status_default_not_configured(client):
    r = client.get("/api/lunch-money/status")
    assert r.status_code == 200
    body = r.json()
    assert body["configured"] is False
    assert body["has_access_token"] is False
    assert "access_token" not in body


def test_put_saves_token_and_hides_it(client):
    r = client.put("/api/lunch-money/config", json={"access_token": "lm-tok-abc"})
    assert r.status_code == 200
    body = r.json()
    assert body["has_access_token"] is True
    assert body["enabled"] is True  # a freshly-pasted token enables by default
    assert body["configured"] is True
    assert "access_token" not in body
    assert "lm-tok-abc" not in json.dumps(body)


def test_put_blank_token_keeps_saved(client):
    client.put("/api/lunch-money/config", json={"access_token": "lm-tok-abc"})
    r = client.put("/api/lunch-money/config", json={"enabled": False})
    assert r.status_code == 200
    body = r.json()
    assert body["has_access_token"] is True
    assert body["enabled"] is False


def test_put_explicit_enabled_overrides_default(client):
    r = client.put("/api/lunch-money/config", json={"access_token": "lm-tok-abc", "enabled": False})
    assert r.status_code == 200
    assert r.json()["enabled"] is False


def test_no_sync_route_exists(client):
    """There is no more scheduler-driven / REST-triggered sync — a fetch only happens
    inside a Lucius turn via the lunch_money_fetch_transactions tool."""
    r = client.post("/api/lunch-money/sync")
    assert r.status_code == 404
