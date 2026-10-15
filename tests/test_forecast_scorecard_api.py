"""
API smoke tests for the forecast-vs-actuals scorecard router (P3-SI1).

Offline + isolated, mirroring tests/test_returns_api.py: a minimal FastAPI app mounts only the
scorecard router (NOT backend.main). One client patches the SERVICE entrypoint to a canned
payload (pure endpoint contract: status + shape + enum validation), another drives the REAL
service with the reused inputs monkeypatched (empty data → 200 with forecastable=false, never
500).

Run:
    .venv/bin/python3 -m pytest tests/test_forecast_scorecard_api.py -q
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api.forecast_scorecard import router


@pytest.fixture
def client(monkeypatch):
    """Client whose route calls a CANNED service payload (the endpoint-contract smoke)."""
    from backend.services import forecast_scorecard as scorecard_service

    captured = {}

    def fake_scorecard(window="3M", today=None):
        captured["window"] = window
        return {
            "window": window,
            "base_currency": "SGD",
            "forecastable": True,
            "projected_monthly_net": 2000.0,
            "projected_monthly_income": 5000.0,
            "projected_monthly_expenses": 3000.0,
            "periods": [],
            "cashflow_accuracy": {
                "mean_abs_error": 100.0,
                "mean_abs_pct_error": 5.0,
                "bias": -20.0,
                "periods_scored": 3,
            },
            "net_worth_accuracy": {
                "mean_abs_error": 50.0,
                "mean_abs_pct_error": 0.1,
                "bias": 10.0,
                "periods_scored": 3,
            },
            "data_quality": {
                "has_baseline": True,
                "periods_available": 3,
                "net_worth_anchored": True,
            },
        }

    monkeypatch.setattr(scorecard_service, "scorecard", fake_scorecard)
    app = FastAPI()
    app.include_router(router)
    return TestClient(app), captured


def test_scorecard_default_window_is_3m(client):
    c, captured = client
    r = c.get("/api/forecast-scorecard")
    assert r.status_code == 200
    assert captured["window"] == "3M"
    body = r.json()
    assert {
        "window",
        "base_currency",
        "forecastable",
        "projected_monthly_net",
        "periods",
        "cashflow_accuracy",
        "net_worth_accuracy",
        "data_quality",
    } <= set(body)


@pytest.mark.parametrize("window", ["1M", "3M", "6M", "1Y"])
def test_scorecard_each_window_enum_200(client, window):
    c, captured = client
    r = c.get("/api/forecast-scorecard", params={"window": window})
    assert r.status_code == 200
    assert captured["window"] == window
    assert r.json()["window"] == window


def test_scorecard_bad_window_422(client):
    c, _ = client
    r = c.get("/api/forecast-scorecard", params={"window": "5Y"})
    assert r.status_code == 422


# ── Real-service path (empty data → 200 with forecastable=false, never 500) ──────


@pytest.fixture
def real_client(monkeypatch):
    """Client over the REAL service with reused inputs isolated (empty book)."""
    from backend.services import cashflow as cashflow_service
    from backend.services import forecast_scorecard as scorecard_service
    from backend.services import ledger as ledger_service

    monkeypatch.setattr(ledger_service, "base_currency", lambda: "USD")
    monkeypatch.setattr(scorecard_service, "load_fx_cache", lambda: {"USD": 1.0})
    monkeypatch.setattr(cashflow_service, "_rolling_avg_income", lambda base, *_a, **_k: None)
    monkeypatch.setattr(cashflow_service, "_rolling_avg_expenses", lambda base, *_a, **_k: None)
    monkeypatch.setattr(ledger_service, "net_worth_history", lambda *a, **k: [])
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def test_scorecard_empty_data_200_not_forecastable(real_client):
    r = real_client.get("/api/forecast-scorecard", params={"window": "1Y"})
    assert r.status_code == 200
    body = r.json()
    assert body["forecastable"] is False
    assert body["periods"] == []
    assert body["cashflow_accuracy"]["periods_scored"] == 0
