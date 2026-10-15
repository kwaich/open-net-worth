"""
Endpoint smoke tests for the two new classification endpoints:

  POST /api/instruments/enrich-classification  → {checked, updated}
  PUT  /api/instruments/{id}/classification    → updated instrument | 404
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api.instruments import router
from backend.db.instrument_store import InstrumentStore


@pytest.fixture
def client(tmp_path, monkeypatch, db_isolated):
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


# ── POST /api/instruments/enrich-classification ──────────────────────────────


def test_enrich_classification_empty_store(client):
    """Empty instrument store → checked=0, updated=0."""
    resp = client.post("/api/instruments/enrich-classification")
    assert resp.status_code == 200
    body = resp.json()
    assert "checked" in body and "updated" in body
    assert body["checked"] == 0
    assert body["updated"] == 0


def test_enrich_classification_fills_region(client, tmp_path, monkeypatch):
    """A bond instrument gets region='Singapore' after enrich."""
    app = FastAPI()
    app.include_router(router)
    c = TestClient(app)

    InstrumentStore().create({"symbol": "SGS", "asset_class": "bond", "quote_currency": "SGD"})
    resp = c.post("/api/instruments/enrich-classification")
    assert resp.status_code == 200
    body = resp.json()
    assert body["updated"] == 1
    inst = InstrumentStore().all()[0]
    assert inst["region"] == "Singapore"
    assert inst["classification_source"] == "heuristic"


def test_enrich_classification_swallows_exception(client, monkeypatch):
    """If the service raises, the endpoint returns error field and 200."""
    import backend.api.instruments as inst_mod

    def _raise():
        raise RuntimeError("boom")

    monkeypatch.setattr(
        inst_mod,
        "enrich_instrument_classification"
        if hasattr(inst_mod, "enrich_instrument_classification")
        else "_noop",
        _raise,
        raising=False,
    )

    # Patch via the service module the route imports at call time
    import backend.services.classification as cls_mod

    monkeypatch.setattr(cls_mod, "enrich_instrument_classification", _raise)

    resp = client.post("/api/instruments/enrich-classification")
    assert resp.status_code == 200
    body = resp.json()
    assert "error" in body


# ── PUT /api/instruments/{id}/classification ──────────────────────────────────


def test_put_classification_sets_manual(client, tmp_path, monkeypatch):
    """Override sets classification_source='manual'."""
    app = FastAPI()
    app.include_router(router)
    c = TestClient(app)

    inst = InstrumentStore().create(
        {"symbol": "AAPL", "asset_class": "equity", "quote_currency": "USD"}
    )
    resp = c.put(
        f"/api/instruments/{inst['id']}/classification",
        json={"sector": "Technology", "region": "United States"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["sector"] == "Technology"
    assert body["region"] == "United States"
    assert body["classification_source"] == "manual"


def test_put_classification_unknown_id_404(client):
    """Unknown id returns 404."""
    resp = client.put(
        "/api/instruments/does-not-exist/classification",
        json={"sector": "Tech"},
    )
    assert resp.status_code == 404


def test_put_classification_partial_update(client, tmp_path, monkeypatch):
    """Supplying only region (no sector) leaves sector unchanged."""
    app = FastAPI()
    app.include_router(router)
    c = TestClient(app)

    inst = InstrumentStore().create(
        {"symbol": "MSFT", "asset_class": "equity", "quote_currency": "USD", "sector": "Technology"}
    )
    resp = c.put(
        f"/api/instruments/{inst['id']}/classification",
        json={"region": "United States"},
    )
    assert resp.status_code == 200
    body = resp.json()
    # sector untouched, region set, source=manual
    assert body["sector"] == "Technology"
    assert body["region"] == "United States"
    assert body["classification_source"] == "manual"
