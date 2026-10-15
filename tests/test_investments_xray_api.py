"""
API smoke + AI-tool tests for the fees-summary + X-ray surfaces (P2-INV5).

Offline + isolated: a minimal FastAPI app mounts only the investments_xray router; store
paths are monkeypatched into tmp_path so requests never touch live data. Asserts status +
response shape; the exact math is pinned in test_fees_summary.py / test_xray.py.
"""

from __future__ import annotations

import asyncio
import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.ai.tools import TOOL_SCHEMAS, execute_tool
from backend.db.instrument_store import InstrumentStore


@pytest.fixture
def client(tmp_path, monkeypatch, db_isolated):

    from backend.services import ledger as ledger_service
    from backend.services import lots as lots_service
    from backend.services import xray as xray_service

    monkeypatch.setattr(lots_service, "_load_rates", lambda: {"USD": 1.0})
    monkeypatch.setattr(lots_service, "base_currency", lambda: "USD")
    monkeypatch.setattr(xray_service, "base_currency", lambda: "USD")
    # X-ray now values rows via the shared ledger.row_base_value fold, which calls
    # ledger.to_base — stub FX where it actually happens now (not on xray).
    monkeypatch.setattr(
        ledger_service, "to_base", lambda amt, ccy, base, rates=None: round(float(amt), 2)
    )

    from backend.api.investments_xray import router

    app = FastAPI()
    app.include_router(router)
    return TestClient(app), tmp_path


# ── REST smoke ─────────────────────────────────────────────────────────────────


def test_fees_summary_empty_safe(client):
    c, _ = client
    r = c.get("/api/fees-summary")
    assert r.status_code == 200
    body = r.json()
    assert body["total_fees_base"] == 0.0
    assert set(body) >= {
        "base_currency",
        "acquisition_fees_base",
        "disposal_fees_base",
        "total_fees_base",
        "by_instrument",
    }


def test_portfolio_xray_empty_safe(client, monkeypatch):
    c, _ = client
    from backend.services import positions as positions_service

    monkeypatch.setattr(positions_service, "hydrated", lambda *a, **k: [])
    r = c.get("/api/portfolio/xray")
    assert r.status_code == 200
    body = r.json()
    assert body["total_base"] == 0.0
    assert body["by_sector"] == [] and body["by_region"] == []
    assert "unmapped_pct" in body


def test_put_constituents_persists(client):
    c, _ = client
    inst = InstrumentStore().create({"symbol": "VOO", "quote_currency": "USD"})
    r = c.put(
        f"/api/instruments/{inst['id']}/constituents",
        json={"constituents": [{"weight": 0.6, "sector": "Tech", "region": "US"}]},
    )
    assert r.status_code == 200
    assert r.json()["constituents"][0]["sector"] == "Tech"
    assert InstrumentStore().find_by_id(inst["id"])["constituents"][0]["weight"] == 0.6


def test_put_constituents_unknown_id_404(client):
    c, _ = client
    r = c.put(
        "/api/instruments/nope/constituents",
        json={"constituents": [{"weight": 0.5, "sector": "Tech"}]},
    )
    assert r.status_code == 404


def test_put_constituents_bad_weights_422(client):
    c, _ = client
    inst = InstrumentStore().create({"symbol": "VOO", "quote_currency": "USD"})
    r = c.put(
        f"/api/instruments/{inst['id']}/constituents",
        json={"constituents": [{"weight": 0.7, "sector": "A"}, {"weight": 0.6, "sector": "B"}]},
    )
    assert r.status_code == 422


# ── AI tools ─────────────────────────────────────────────────────────────────────


def _call(tool, **inp):
    text, proposed = asyncio.run(execute_tool(tool, inp))
    return json.loads(text), proposed


def test_tools_registered(client):
    names = {t["name"] for t in TOOL_SCHEMAS}
    assert {"get_fees_summary", "get_portfolio_xray", "set_instrument_constituents"} <= names


def test_get_fees_summary_tool(client):
    res, proposed = _call("get_fees_summary")
    assert proposed is None
    assert res["total_fees_base"] == 0.0


def test_get_portfolio_xray_tool(client, monkeypatch):
    from backend.services import positions as positions_service

    monkeypatch.setattr(positions_service, "hydrated", lambda *a, **k: [])
    res, proposed = _call("get_portfolio_xray")
    assert proposed is None
    assert res["total_base"] == 0.0


def test_set_instrument_constituents_writes_no_proposed_change(client):
    inst = InstrumentStore().create({"symbol": "VOO", "quote_currency": "USD"})
    res, proposed = _call(
        "set_instrument_constituents",
        instrument_id=inst["id"],
        constituents=[{"weight": 0.5, "sector": "Tech", "region": "US"}],
    )
    assert proposed is None  # direct config write, NOT approve-tier
    assert res["ok"] is True
    assert InstrumentStore().find_by_id(inst["id"])["constituents"][0]["sector"] == "Tech"


def test_set_instrument_constituents_bad_weights_errors_no_write(client):
    inst = InstrumentStore().create({"symbol": "VOO", "quote_currency": "USD"})
    res, proposed = _call(
        "set_instrument_constituents",
        instrument_id=inst["id"],
        constituents=[{"weight": 0.7, "sector": "A"}, {"weight": 0.6, "sector": "B"}],
    )
    assert proposed is None
    assert "error" in res
    assert InstrumentStore().find_by_id(inst["id"])["constituents"] == []


def test_set_instrument_constituents_unknown_id(client):
    res, proposed = _call("set_instrument_constituents", instrument_id="nope", constituents=[])
    assert "error" in res
