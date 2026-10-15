"""API smoke tests for the IBKR connector REST endpoints."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path, monkeypatch):
    """A TestClient with a fresh temp-dir data store and auth disabled."""
    monkeypatch.setenv("AUTH_DISABLED", "true")
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    import importlib

    import backend.paths as paths

    importlib.reload(paths)
    monkeypatch.setattr("backend.paths.IBKR_CONFIG_JSON", tmp_path / "ibkr_config.json")
    # Also point the db import to the tmp path.
    monkeypatch.setattr(
        "backend.db.ibkr_config_store.IbkrConfigStore._path",
        tmp_path / "ibkr_config.json",
        raising=False,
    )

    from backend.main import app

    return TestClient(app)


@pytest.fixture
def client_simple(tmp_path, monkeypatch):
    """Minimal client fixture — just disables auth."""
    monkeypatch.setenv("AUTH_DISABLED", "true")
    from backend.main import app

    return TestClient(app)


def test_ibkr_status_returns_200(client_simple):
    r = client_simple.get("/api/ibkr/status")
    assert r.status_code == 200
    data = r.json()
    assert "configured" in data
    assert "has_token" in data
    assert "enabled" in data
    assert "trading_enabled" not in data
    # No sync-stats bookkeeping left (RFC connectors-as-data-sources) — a fetch tool's
    # own result carries counts; this store tracks only status/last_synced_at/last_error.
    assert "stats" not in data
    # Token must NOT appear.
    assert "flex_token" not in data


def test_ibkr_config_put_saves_query_id(tmp_path, monkeypatch):
    """PUT /api/ibkr/config stores flex_query_id and returns public status."""
    monkeypatch.setenv("AUTH_DISABLED", "true")
    from backend.db.ibkr_config_store import IbkrConfigStore
    from backend.main import app

    # ibkr_config is DB-backed now (Wave 5) — conftest's autouse Tier-2 table clear
    # already isolates it per test, no path kwarg needed.
    store = IbkrConfigStore()
    monkeypatch.setattr("backend.api.ibkr_api.IbkrConfigStore", lambda: store)

    client = TestClient(app)
    r = client.put(
        "/api/ibkr/config",
        json={"flex_query_id": "987654", "enabled": False},
    )
    assert r.status_code == 200
    data = r.json()
    assert data["flex_query_id"] == "987654"
    assert data["has_token"] is False


def test_trading_route_is_gone(tmp_path, monkeypatch):
    """`PUT /api/ibkr/trading` must not exist, and the surviving config route must not
    accept the flag as a smuggled-in alternative writer. See
    docs/adr/2026-07-20-remove-ibkr-dead-trading-flag.md.

    Asserted against the ROUTING TABLE, not an HTTP status. The original version of this
    test asserted 404 and failed on a correct removal: this app answers 405 for ANY
    unmatched PUT under an existing prefix (`PUT /api/ibkr/definitely-not-a-route` is 405
    too), so the status code says nothing about whether THIS route is gone. Checking
    `app.routes` tests the actual claim and cannot be confused by unrelated routing."""
    monkeypatch.setenv("AUTH_DISABLED", "true")
    from backend.db.ibkr_config_store import IbkrConfigStore
    from backend.main import app

    # ibkr_config is DB-backed now (Wave 5) — conftest's autouse Tier-2 table clear
    # already isolates it per test, no path kwarg needed.
    store = IbkrConfigStore()
    monkeypatch.setattr("backend.api.ibkr_api.IbkrConfigStore", lambda: store)

    client = TestClient(app)
    assert not [r for r in app.routes if getattr(r, "path", "") == "/api/ibkr/trading"]

    body = client.put(
        "/api/ibkr/config",
        json={"flex_token": "T", "flex_query_id": "Q", "trading_enabled": True},
    ).json()
    assert "trading_enabled" not in body
    assert "trading_enabled" not in store.get()


def test_order_wire_calls_are_gone():
    """The Flex service module must expose no order entrypoint — its only wire calls are
    the read-only SendRequest/GetStatement reporting pair."""
    from backend.services.aggregation import ibkr as ibkr_service

    for name in ("place_order", "cancel_order", "submit_ibkr_order", "cancel_ibkr_order"):
        assert not hasattr(ibkr_service, name)


def test_ibkr_sync_route_is_gone():
    """`POST /api/ibkr/sync` is retired (RFC connectors-as-data-sources) — fetching is now
    the model's own call to `ibkr_fetch_activity`, never a REST action. Asserted against
    the routing table for the same reason test_trading_route_is_gone is: this app answers
    405 for any unmatched method under an existing prefix, so a status code alone would not
    prove the route is gone."""
    from backend.main import app

    assert not [
        r
        for r in app.routes
        if getattr(r, "path", "") == "/api/ibkr/sync" and "POST" in getattr(r, "methods", set())
    ]


def test_ibkr_config_enable_without_credentials_422(monkeypatch):
    """Enabling an unconfigured store → 422."""
    monkeypatch.setenv("AUTH_DISABLED", "true")
    from backend.main import app

    client = TestClient(app)
    r = client.put("/api/ibkr/config", json={"enabled": True})
    assert r.status_code == 422
