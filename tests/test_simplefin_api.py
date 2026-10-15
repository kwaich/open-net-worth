"""REST smoke for the SimpleFIN endpoints (GET status / PUT config).

TestClient on a bare app with ONLY the simplefin router mounted; stores isolated via
db_isolated. ``claim_setup_token`` is mocked (NO real network). Verifies: the access URL
is write-only (never returned), claiming a setup token enables the connector + reports
has_access_url, a blank/omitted token keeps the saved creds, and an invalid token is
refused with a clean 422. Fetching transactions is no longer a REST action (RFC
connectors-as-data-sources) — it happens only inside a Lucius turn
(``simplefin_fetch_transactions`` + ``add_transactions``); see
tests/test_tools_aggregation.py for the fetch-tool coverage.
"""

from __future__ import annotations

import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


@pytest.fixture
def client(db_isolated, monkeypatch):

    # Mock the protocol so no network is touched.
    from backend.services.aggregation import simplefin as svc

    monkeypatch.setattr(svc, "claim_setup_token", lambda token: "https://u:p@bridge.example/sf")

    app = FastAPI()
    from backend.api.simplefin_api import router

    app.include_router(router)
    return TestClient(app)


def test_status_default_not_configured(client):
    r = client.get("/api/simplefin/status")
    assert r.status_code == 200
    body = r.json()
    assert body["configured"] is False
    assert body["has_access_url"] is False
    assert "access_url" not in body


def test_put_claims_token_and_hides_url(client):
    r = client.put("/api/simplefin/config", json={"setup_token": "c2V0dXA="})
    assert r.status_code == 200
    body = r.json()
    assert body["has_access_url"] is True
    assert body["enabled"] is True  # a fresh claim enables by default
    assert body["configured"] is True
    # The access URL must never be returned.
    assert "access_url" not in body
    assert "bridge" not in json.dumps(body)


def test_put_blank_token_keeps_saved(client):
    client.put("/api/simplefin/config", json={"setup_token": "c2V0dXA="})
    # A blank/omitted token keeps the saved creds, just flips enabled off.
    r = client.put("/api/simplefin/config", json={"enabled": False})
    assert r.status_code == 200
    body = r.json()
    assert body["has_access_url"] is True
    assert body["enabled"] is False


def test_put_explicit_enabled_overrides_default(client):
    r = client.put("/api/simplefin/config", json={"setup_token": "c2V0dXA=", "enabled": False})
    assert r.status_code == 200
    assert r.json()["enabled"] is False


def test_put_invalid_token_returns_422(client, monkeypatch):
    from backend.core.errors import ValidationError
    from backend.services.aggregation import simplefin as svc

    def _raise(token):
        raise ValidationError("setup token is not valid base64")

    monkeypatch.setattr(svc, "claim_setup_token", _raise)
    r = client.put("/api/simplefin/config", json={"setup_token": "bad"})
    assert r.status_code == 422
