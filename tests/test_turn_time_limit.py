"""Turn time limit: the owner-set ceiling on one Lucius turn (Settings → AI Models).

Unset = the env default; 0 = no limit; the API only accepts the offered choices.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api.models import router as models_router
from backend.core.config import get_settings
from backend.db import app_config_store


@pytest.fixture
def client(db_isolated):
    app = FastAPI()
    app.include_router(models_router)
    return TestClient(app)


def test_unset_limit_is_the_env_default(db_isolated):
    assert app_config_store.turn_time_limit_s() == get_settings().turn_timeout_s


def test_no_limit_is_stored_as_zero_and_clearing_restores_the_default(db_isolated):
    store = app_config_store.AppConfigStore()
    store.set_turn_time_limit_s(0)
    assert app_config_store.turn_time_limit_s() == 0
    store.set_turn_time_limit_s(None)
    assert app_config_store.turn_time_limit_s() == get_settings().turn_timeout_s


def test_api_round_trips_a_choice(client):
    body = client.get("/api/model-config/turn-time-limit").json()
    assert body["seconds"] == body["default_seconds"]
    assert body["choices"][-1] == 0
    assert body["default_seconds"] in body["choices"]

    put = client.put("/api/model-config/turn-time-limit", json={"seconds": 3600})
    assert put.status_code == 200 and put.json()["seconds"] == 3600

    unlimited = client.put("/api/model-config/turn-time-limit", json={"seconds": 0})
    assert unlimited.json()["seconds"] == 0
    assert client.get("/api/model-config/turn-time-limit").json()["seconds"] == 0


def test_api_refuses_an_unoffered_value(client):
    assert client.put("/api/model-config/turn-time-limit", json={"seconds": 7}).status_code == 422
    assert client.put("/api/model-config/turn-time-limit", json={"seconds": -1}).status_code == 422


def test_writes_are_demo_locked():
    from backend.api.demo_lockdown import is_demo_locked

    assert is_demo_locked("PUT", "/api/model-config/turn-time-limit")
    assert not is_demo_locked("GET", "/api/model-config/turn-time-limit")
