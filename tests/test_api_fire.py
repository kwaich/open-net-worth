"""
API smoke tests for GET /api/fire — MATH-29 regression.

Mirrors test_api_daily_total_value.py in structure: a minimal FastAPI app wrapping
just the fire router, with the profile seeded directly via ProfileStore (the router
reads it through services.fire._load_profile(), no service layer to monkeypatch).

Run:
    .venv/bin/python3 -m pytest tests/test_api_fire.py -q
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api.fire import router
from backend.db.profile_store import ProfileStore
from backend.services.fire import recommended_swr


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def test_default_life_expectancy_gives_25_year_duration(client):
    """No fire_life_expectancy set → falls back to the module default (90);
    retire at 65 → duration 25."""
    ProfileStore().set_raw({"retirement_target_age": 65, "fire_withdrawal_rate_pct": 4.0})
    r = client.get("/api/fire", params={"current_nw": 100_000})
    assert r.status_code == 200
    body = r.json()
    assert body["retirement_duration_years"] == pytest.approx(25.0, abs=0.1)
    assert body["recommended_swr_pct"] == 4.5
    assert body["swr_warning"] is False


def test_custom_life_expectancy_is_used_for_duration_and_swr_warning(client):
    """MATH-29 regression: fire_life_expectancy=100, retire at 65, SWR 4% must report
    duration 35 (not 25), recommended_swr 3.75 (not 4.5), and swr_warning True (not
    False) — the probe values from docs/review/money-math-audit.md#math-29."""
    ProfileStore().set_raw(
        {
            "retirement_target_age": 65,
            "fire_withdrawal_rate_pct": 4.0,
            "fire_life_expectancy": 100,
        }
    )
    r = client.get("/api/fire", params={"current_nw": 100_000})
    assert r.status_code == 200
    body = r.json()
    assert body["retirement_duration_years"] == pytest.approx(35.0, abs=0.1)
    assert body["recommended_swr_pct"] == recommended_swr(35)
    assert body["swr_warning"] is True
    assert body["life_expectancy"] == 100


def test_life_expectancy_not_after_retirement_age_falls_back_to_default(client):
    """An invalid fire_life_expectancy (<= retirement_target_age) must not crash —
    it falls back to the default and the echoed life_expectancy reflects what was
    actually used."""
    ProfileStore().set_raw(
        {
            "retirement_target_age": 65,
            "fire_withdrawal_rate_pct": 4.0,
            "fire_life_expectancy": 60,
        }
    )
    r = client.get("/api/fire", params={"current_nw": 100_000})
    assert r.status_code == 200
    body = r.json()
    assert body["retirement_duration_years"] == pytest.approx(25.0, abs=0.1)
    assert body["life_expectancy"] == 90


# ─────────────────────────────────────────────────────────────────────────────
# MATH-28 — a missing date_of_birth degrades age-anchored fields, never age 0
# ─────────────────────────────────────────────────────────────────────────────


def test_missing_date_of_birth_reports_null_age_fields_not_age_zero(client):
    ProfileStore().set_raw(
        {
            "retirement_target_age": 65,
            "fire_withdrawal_rate_pct": 4.0,
            "monthly_net_income": 8_000,
            "monthly_expenses": 5_000,
        }
    )
    r = client.get("/api/fire", params={"current_nw": 100_000})
    assert r.status_code == 200
    body = r.json()
    assert body["current_age"] is None
    assert body["on_track"] is None
    assert all(p["age"] is None for p in body["trajectory"])
    # Age-independent figures are unaffected.
    assert body["fire_number"] > 0
    assert body["years_to_fire"] is not None


def test_known_date_of_birth_still_reports_a_real_age(client):
    from datetime import date

    dob = date(date.today().year - 45, date.today().month, date.today().day)
    ProfileStore().set_raw(
        {
            "retirement_target_age": 65,
            "fire_withdrawal_rate_pct": 4.0,
            "date_of_birth": dob.isoformat(),
        }
    )
    r = client.get("/api/fire", params={"current_nw": 100_000})
    assert r.status_code == 200
    body = r.json()
    assert body["current_age"] == pytest.approx(45.0, abs=0.1)
    assert body["trajectory"][0]["age"] == pytest.approx(45.0, abs=0.1)


# ─────────────────────────────────────────────────────────────────────────────
# MATH-32 — a zero/negative withdrawal rate 422s instead of dividing by zero
# ─────────────────────────────────────────────────────────────────────────────


def test_zero_withdrawal_rate_returns_422(client):
    ProfileStore().set_raw({"retirement_target_age": 65, "fire_withdrawal_rate_pct": 0.0})
    r = client.get("/api/fire", params={"current_nw": 100_000})
    assert r.status_code == 422


def test_negative_withdrawal_rate_returns_422(client):
    ProfileStore().set_raw({"retirement_target_age": 65, "fire_withdrawal_rate_pct": -4.0})
    r = client.get("/api/fire", params={"current_nw": 100_000})
    assert r.status_code == 422
