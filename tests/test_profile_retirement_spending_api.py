import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import backend.api.profile as profile_api


def test_profile_api_accepts_explicit_monthly_retirement_spending(monkeypatch):
    stored = {}
    monkeypatch.setattr(profile_api, "read_profile", lambda: {})
    monkeypatch.setattr(profile_api, "write_profile", lambda profile: stored.update(profile))
    monkeypatch.setattr(profile_api, "handle_base_currency_change", lambda *_args: None)
    app = FastAPI()
    app.include_router(profile_api.router)

    response = TestClient(app).put(
        "/api/profile", json={"fire_monthly_retirement_spending": "3500"}
    )

    assert response.status_code == 200
    assert response.json()["profile"]["fire_monthly_retirement_spending"] == 3500.0
    assert stored["fire_monthly_retirement_spending"] == 3500.0


@pytest.mark.parametrize("value", [-1, "NaN", "Infinity"])
def test_profile_api_rejects_invalid_monthly_retirement_spending_without_writing(
    monkeypatch, value
):
    writes = []
    monkeypatch.setattr(profile_api, "read_profile", lambda: {})
    monkeypatch.setattr(profile_api, "write_profile", lambda profile: writes.append(profile))
    monkeypatch.setattr(profile_api, "handle_base_currency_change", lambda *_args: None)
    app = FastAPI()
    app.include_router(profile_api.router)

    response = TestClient(app).put("/api/profile", json={"fire_monthly_retirement_spending": value})

    assert response.status_code == 422
    assert writes == []
