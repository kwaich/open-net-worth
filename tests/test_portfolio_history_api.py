"""
API smoke tests for GET /api/portfolio/value-series (Wave 2 cross-tab sparkline endpoint).

Mirrors test_api_daily_total_value.py: one fixture patches the service to a canned
payload (endpoint-contract smoke); another drives the real service against empty stores
(empty portfolio → 200 + series:[], never 500).

Run:
    .venv/bin/python3 -m pytest tests/test_portfolio_history_api.py -q
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api.portfolio_history_api import router

# ── Canned-service client ──────────────────────────────────────────────────────


@pytest.fixture
def client(monkeypatch):
    """Client hitting a canned portfolio_value_series stub — pure contract smoke."""
    from backend.services import portfolio_history as ph

    def fake_portfolio_value_series(start, end, grain="daily", **kwargs):
        return {
            "series": [
                {"date": "2026-05-23", "value_base": 10000.0, "classes": {}},
                {"date": "2026-05-24", "value_base": 10200.0, "classes": {}},
            ],
            "data_quality": {
                "inception": "2026-01-01",
                "market_since": "2026-01-01",
                "has_unrecorded_risk": False,
                "has_unknown_cost": False,
                "fx_approximate": False,
                "tiers": {},
            },
        }

    monkeypatch.setattr(ph, "portfolio_value_series", fake_portfolio_value_series)

    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


# ── Unpriced-book client (MATH-10 regression) ───────────────────────────────────


@pytest.fixture
def unpriced_client(monkeypatch):
    """Client whose service stub mirrors a fresh instance with zero positions:
    holdings_series still emits one point per calendar day (31 zero-value points),
    but no price has ever been recorded, so data_quality.market_since is None."""
    from backend.services import portfolio_history as ph

    def fake_unpriced_series(start, end, grain="daily", **kwargs):
        return {
            "series": [
                {"date": f"2026-05-{d:02d}", "value_base": 0.0, "classes": {}} for d in range(1, 32)
            ],
            "data_quality": {
                "inception": None,
                "market_since": None,
                "has_unrecorded_risk": False,
                "has_unknown_cost": False,
                "fx_approximate": False,
                "tiers": {},
            },
        }

    monkeypatch.setattr(ph, "portfolio_value_series", fake_unpriced_series)

    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


# ── Empty-portfolio client ────────────────────────────────────────────────────


@pytest.fixture
def real_client(monkeypatch):
    """Client with portfolio_value_series returning an empty series (no holdings)."""
    from backend.services import portfolio_history as ph

    def empty_portfolio_value_series(start, end, grain="daily", **kwargs):
        return {
            "series": [],
            "data_quality": {
                "inception": None,
                "market_since": None,
                "has_unrecorded_risk": False,
                "has_unknown_cost": False,
                "fx_approximate": False,
                "tiers": {},
            },
        }

    monkeypatch.setattr(ph, "portfolio_value_series", empty_portfolio_value_series)

    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


# ── Tests ──────────────────────────────────────────────────────────────────────


class TestPortfolioValueSeriesContract:
    """Endpoint-contract smoke: correct HTTP shape + field types."""

    def test_200_default_params(self, client):
        resp = client.get("/api/portfolio/value-series")
        assert resp.status_code == 200
        body = resp.json()
        assert "series" in body
        assert "inception" in body
        assert "has_real_daily_history" in body
        # Each point must have date and value_base; classes must be stripped.
        for pt in body["series"]:
            assert "date" in pt
            assert "value_base" in pt
            assert "classes" not in pt

    def test_200_explicit_days_and_grain(self, client):
        resp = client.get("/api/portfolio/value-series?days=30&grain=daily")
        assert resp.status_code == 200

    def test_has_real_daily_history_true_when_series_ge_2(self, client):
        resp = client.get("/api/portfolio/value-series")
        body = resp.json()
        assert body["has_real_daily_history"] is True

    def test_422_days_zero(self, client):
        resp = client.get("/api/portfolio/value-series?days=0")
        assert resp.status_code == 422

    def test_422_days_over_365(self, client):
        resp = client.get("/api/portfolio/value-series?days=999")
        assert resp.status_code == 422

    def test_422_invalid_grain(self, client):
        resp = client.get("/api/portfolio/value-series?grain=hourly")
        assert resp.status_code == 422


class TestPortfolioValueSeriesUnpricedBook:
    """MATH-10 regression: 31 zero-value calendar-day points with no priced holding
    (market_since is None) must NOT be reported as real daily history, even though
    len(series) >= 2."""

    def test_has_real_daily_history_false_when_market_since_none(self, unpriced_client):
        resp = unpriced_client.get("/api/portfolio/value-series?days=30")
        assert resp.status_code == 200
        body = resp.json()
        assert len(body["series"]) >= 2
        assert body["has_real_daily_history"] is False


class TestPortfolioValueSeriesEmptyPortfolio:
    """Real service against empty stores: must return 200 with empty series, never 500."""

    def test_200_empty_portfolio(self, real_client):
        resp = real_client.get("/api/portfolio/value-series?days=30")
        assert resp.status_code == 200
        body = resp.json()
        assert "series" in body
        assert isinstance(body["series"], list)
        # Empty portfolio → no series points → has_real_daily_history=False.
        assert body["has_real_daily_history"] is False
