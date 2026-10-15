"""
Tests for risk_concentration_guideline and risk_stress_drop_pct profile fields.

Covers:
  - GET /api/profile returns both fields with their defaults (20.0 / 30.0)
  - PUT /api/profile round-trips explicit values
  - Values below 1 are clamped to 1
  - Values above 100 are clamped to 100
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api.profile import router


@pytest.fixture
def client():
    """TestClient wired to the DB-backed profile store (no live data touched) — profile is
    a ``ProfileStore`` row now (Wave 5, JSON->DB); tests/conftest.py's autouse per-test
    table clear already gives every test a fresh row."""
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def test_defaults_present(client):
    """GET /api/profile returns the new fields with their documented defaults."""
    r = client.get("/api/profile")
    assert r.status_code == 200
    data = r.json()
    assert data["risk_concentration_guideline"] == 20.0
    assert data["risk_stress_drop_pct"] == 30.0


def test_round_trip(client):
    """PUT /api/profile accepts and persists explicit values."""
    r = client.put(
        "/api/profile",
        json={"risk_concentration_guideline": 15.0, "risk_stress_drop_pct": 25.0},
    )
    assert r.status_code == 200
    profile = r.json()["profile"]
    assert profile["risk_concentration_guideline"] == 15.0
    assert profile["risk_stress_drop_pct"] == 25.0

    # Verify persistence via a subsequent GET.
    r2 = client.get("/api/profile")
    assert r2.status_code == 200
    data = r2.json()
    assert data["risk_concentration_guideline"] == 15.0
    assert data["risk_stress_drop_pct"] == 25.0


def test_clamp_below_min(client):
    """Values <= 0 are clamped to 1."""
    r = client.put(
        "/api/profile",
        json={"risk_concentration_guideline": 0, "risk_stress_drop_pct": -5},
    )
    assert r.status_code == 200
    profile = r.json()["profile"]
    assert profile["risk_concentration_guideline"] == 1.0
    assert profile["risk_stress_drop_pct"] == 1.0


def test_clamp_above_max(client):
    """Values > 100 are clamped to 100."""
    r = client.put(
        "/api/profile",
        json={"risk_concentration_guideline": 150, "risk_stress_drop_pct": 999},
    )
    assert r.status_code == 200
    profile = r.json()["profile"]
    assert profile["risk_concentration_guideline"] == 100.0
    assert profile["risk_stress_drop_pct"] == 100.0


def test_null_resets_to_none(client):
    """Passing null preserves None (field is Optional)."""
    r = client.put(
        "/api/profile",
        json={"risk_concentration_guideline": None, "risk_stress_drop_pct": None},
    )
    assert r.status_code == 200


def test_preferred_benchmark_defaults_to_spy_and_round_trips(client):
    assert client.get("/api/profile").json()["preferred_benchmark"] == "SPY"
    assert client.put("/api/profile", json={"preferred_benchmark": "VWRA"}).status_code == 200
    assert client.get("/api/profile").json()["preferred_benchmark"] == "VWRA"


def test_preferred_benchmark_outside_the_roster_is_refused(client):
    assert client.put("/api/profile", json={"preferred_benchmark": "NOPE"}).status_code == 422


def test_preferred_benchmark_reader_falls_back_on_a_stale_value():
    from backend.services.risk_metrics import preferred_benchmark

    assert preferred_benchmark({"preferred_benchmark": "QQQ"}) == "QQQ"
    assert preferred_benchmark({"preferred_benchmark": "GONE"}) == "SPY"
    assert preferred_benchmark({}) == "SPY"
