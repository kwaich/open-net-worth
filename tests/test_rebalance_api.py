"""
API smoke tests for the rebalancing router (P4-RB1).

Offline + isolated: a minimal FastAPI app mounts only the rebalance router; the service is
monkeypatched to return a canned plan so we assert the GET/POST contract + param passthrough
without touching real holdings/FX. A fresh instance (no targets) returns 200, not 500.

Run:
    .venv/bin/python3 -m pytest tests/test_rebalance_api.py -q
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api import rebalance as rb_api
from backend.api.rebalance import router


@pytest.fixture
def client(monkeypatch):
    calls = {}

    def fake_plan(*, new_cash=0.0, mode="sell_to_target"):
        calls["new_cash"] = new_cash
        calls["mode"] = mode
        return {
            "base_currency": "USD",
            "mode": mode,
            "status": "ok",
            "new_cash": new_cash,
            "current_total": 10000.0,
            "target_total": 10000.0 + new_cash,
            "allocations": [],
            "trades": [],
        }

    monkeypatch.setattr(rb_api.rebalance_service, "rebalance_plan", fake_plan)
    app = FastAPI()
    app.include_router(router)
    c = TestClient(app)
    c._calls = calls  # type: ignore[attr-defined]
    return c


def test_get_default(client):
    r = client.get("/api/rebalance")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["mode"] == "sell_to_target"
    assert client._calls == {"new_cash": 0.0, "mode": "sell_to_target"}


def test_get_with_params(client):
    r = client.get("/api/rebalance", params={"new_cash": 5000, "mode": "cash_only"})
    assert r.status_code == 200
    assert r.json()["target_total"] == 15000.0
    assert client._calls == {"new_cash": 5000.0, "mode": "cash_only"}


def test_post_with_body(client):
    r = client.post("/api/rebalance", json={"new_cash": 2500, "mode": "cash_only"})
    assert r.status_code == 200
    assert client._calls == {"new_cash": 2500.0, "mode": "cash_only"}


def test_post_empty_body_uses_defaults(client):
    r = client.post("/api/rebalance", json={})
    assert r.status_code == 200
    assert client._calls == {"new_cash": 0.0, "mode": "sell_to_target"}


# ── Real service, fresh instance (no targets) → 200, never 500 ───────────────


def test_fresh_instance_no_targets_200(monkeypatch):
    monkeypatch.setattr(rb_api.rebalance_service, "base_currency", lambda: "USD")
    monkeypatch.setattr(rb_api.rebalance_service, "_read_profile", lambda: {})
    # No holdings (hydrated returns [] via the empty isolated store).
    app = FastAPI()
    app.include_router(router)
    r = TestClient(app).get("/api/rebalance")
    assert r.status_code == 200
    assert r.json()["status"] in ("no_targets", "empty_book")
