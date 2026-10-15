"""A feature intro is shown once per user, remembered on the server (``seen_intros``), and the
model never sees that UI state."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api.profile import router as profile_router
from backend.services.profile import profile_for_model


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(profile_router)
    return TestClient(app)


def test_a_fresh_profile_has_seen_no_intros(client):
    assert client.get("/api/profile").json()["seen_intros"] == []


def test_seen_intros_round_trip_without_touching_other_fields(client):
    client.put("/api/profile", json={"name": "Test Person"})
    r = client.put("/api/profile", json={"seen_intros": ["assets", "assets", "plan"]})
    assert r.status_code == 200
    body = client.get("/api/profile").json()
    assert body["seen_intros"] == ["assets", "plan"]
    assert body["name"] == "Test Person"


@pytest.mark.parametrize("bad", [["Assets"], ["<script>"], ["x" * 41], [""]])
def test_seen_intros_only_takes_short_keys(client, bad):
    assert client.put("/api/profile", json={"seen_intros": bad}).status_code == 422


def test_seen_intros_never_reach_the_model():
    assert "seen_intros" not in profile_for_model(
        {"name": "Test Person", "seen_intros": ["assets"]}
    )
