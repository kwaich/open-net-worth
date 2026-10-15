"""REST smoke for the YNAB endpoints (GET status / GET budgets / PUT config).

TestClient on a bare app with ONLY the ynab router mounted; stores isolated via
db_isolated. Budget list/resolve is mocked (NO real network). Verifies: the access token
is write-only (never returned), saving a token enables the connector + reports
has_access_token, the budgets picker lists what the token can see, picking a budget is
live-validated before it is saved, a blank budget_id clears the pick, and a blank token
keeps the saved one. Fetching + booking transactions is no longer a REST action (RFC
connectors-as-data-sources) — see tests/test_connector_fetch_tools.py for
ynab_fetch_transactions.
"""

from __future__ import annotations

import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


@pytest.fixture
def client(db_isolated, monkeypatch):

    from backend.services.aggregation import ynab as svc

    monkeypatch.setattr(
        svc,
        "list_budgets",
        lambda store: [
            {"id": "b1", "name": "Personal", "currency": "USD"},
            {"id": "b2", "name": "Family", "currency": "USD"},
        ],
    )

    app = FastAPI()
    from backend.api.ynab_api import router

    app.include_router(router)
    return TestClient(app)


def test_status_default_not_configured(client):
    r = client.get("/api/ynab/status")
    assert r.status_code == 200
    body = r.json()
    assert body["configured"] is False
    assert body["has_access_token"] is False
    assert body["budget_id"] is None
    assert "access_token" not in body


def test_put_saves_token_and_hides_it(client):
    r = client.put("/api/ynab/config", json={"access_token": "ynab-tok-abc"})
    assert r.status_code == 200
    body = r.json()
    assert body["has_access_token"] is True
    assert body["enabled"] is True  # a freshly-pasted token enables by default
    assert body["configured"] is True
    assert "access_token" not in body
    assert "ynab-tok-abc" not in json.dumps(body)


def test_put_blank_token_keeps_saved(client):
    client.put("/api/ynab/config", json={"access_token": "ynab-tok-abc"})
    r = client.put("/api/ynab/config", json={"enabled": False})
    assert r.status_code == 200
    body = r.json()
    assert body["has_access_token"] is True
    assert body["enabled"] is False


def test_put_explicit_enabled_overrides_default(client):
    r = client.put("/api/ynab/config", json={"access_token": "ynab-tok-abc", "enabled": False})
    assert r.status_code == 200
    assert r.json()["enabled"] is False


# ── budgets picker + selection ──────────────────────────────────────────────────────


def test_budgets_lists_what_the_token_can_see(client):
    client.put("/api/ynab/config", json={"access_token": "ynab-tok-abc"})
    r = client.get("/api/ynab/budgets")
    assert r.status_code == 200
    assert r.json()["budgets"] == [
        {"id": "b1", "name": "Personal", "currency": "USD"},
        {"id": "b2", "name": "Family", "currency": "USD"},
    ]


def test_put_picks_and_validates_a_budget(client):
    client.put("/api/ynab/config", json={"access_token": "ynab-tok-abc"})
    r = client.put("/api/ynab/config", json={"budget_id": "b2"})
    assert r.status_code == 200
    body = r.json()
    assert body["budget_id"] == "b2"
    assert body["budget_name"] == "Family"
    assert body["budget_currency"] == "USD"


def test_put_rejects_an_unknown_budget_id(client):
    client.put("/api/ynab/config", json={"access_token": "ynab-tok-abc"})
    r = client.put("/api/ynab/config", json={"budget_id": "does-not-exist"})
    assert r.status_code == 422
    assert "does-not-exist" in r.json()["detail"]


def test_put_validates_a_budget_against_a_token_set_in_the_same_call(client):
    """A token and a budget pick arriving in the SAME PUT must be validated against the
    token that request just set, not a stale previously-saved one (regression: the token
    must be persisted before the budget is resolved)."""
    r = client.put("/api/ynab/config", json={"access_token": "fresh-token", "budget_id": "b1"})
    assert r.status_code == 200
    body = r.json()
    assert body["has_access_token"] is True
    assert body["budget_id"] == "b1"
    assert body["budget_name"] == "Personal"


def test_put_blank_budget_id_clears_the_pick(client):
    client.put("/api/ynab/config", json={"access_token": "ynab-tok-abc"})
    client.put("/api/ynab/config", json={"budget_id": "b1"})
    r = client.put("/api/ynab/config", json={"budget_id": ""})
    assert r.status_code == 200
    body = r.json()
    assert body["budget_id"] is None
    assert body["budget_name"] is None


def test_budgets_requires_a_token(db_isolated, monkeypatch):
    """Unmocked list_budgets: no token saved -> _safe_get's own ValidationError, before any
    network call is attempted."""
    app = FastAPI()
    from backend.api.ynab_api import router

    app.include_router(router)
    r = TestClient(app).get("/api/ynab/budgets")
    assert r.status_code == 422


def test_budgets_degrades_cleanly_when_list_budgets_raises_something_unexpected(
    client, monkeypatch
):
    """Defense in depth: even if list_budgets somehow lets a non-OpenNetWorthError escape
    (list_budgets itself is now hardened to convert shape/JSON failures into a typed
    ExternalServiceError — see test_ynab_service.py — but this route has no fetch-tool
    safety net under it), the route must still answer with a clean, sanitized error instead
    of an unhandled 500."""
    from backend.services.aggregation import ynab as svc

    client.put("/api/ynab/config", json={"access_token": "ynab-tok-abc"})

    def _boom(store):
        raise AttributeError("'str' object has no attribute 'get'")

    monkeypatch.setattr(svc, "list_budgets", _boom)
    r = client.get("/api/ynab/budgets")
    assert r.status_code == 502
    assert "AttributeError" in r.json()["detail"]
