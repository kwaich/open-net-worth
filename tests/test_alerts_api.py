"""/api/alerts endpoint smoke — status + shape (ADR 0014)."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


@pytest.fixture
def client(db_isolated):
    from backend.api.alerts import router

    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def test_crud_roundtrip(client):
    # Empty to start.
    assert client.get("/api/alerts").json() == {"alerts": []}

    # Create.
    r = client.post(
        "/api/alerts", json={"ticker": "aapl", "op": ">=", "value": 150, "note": "trim"}
    )
    assert r.status_code == 201
    alert = r.json()
    assert alert["ticker"] == "AAPL" and alert["value"] == 150.0 and alert["id"]

    # List + ticker filter.
    assert len(client.get("/api/alerts").json()["alerts"]) == 1
    assert len(client.get("/api/alerts", params={"ticker": "AAPL"}).json()["alerts"]) == 1
    assert client.get("/api/alerts", params={"ticker": "ZZZZ"}).json()["alerts"] == []

    # Patch.
    r = client.patch(f"/api/alerts/{alert['id']}", json={"value": 175})
    assert r.status_code == 200 and r.json()["value"] == 175.0

    # Delete.
    assert client.delete(f"/api/alerts/{alert['id']}").json() == {
        "id": alert["id"],
        "deleted": True,
    }
    assert client.get("/api/alerts").json() == {"alerts": []}


def test_patch_missing_404(client):
    assert client.patch("/api/alerts/ghost", json={"value": 1}).status_code == 404


def test_delete_missing_404(client):
    assert client.delete("/api/alerts/ghost").status_code == 404


def test_bad_op_422(client):
    # Literal op validation happens at the pydantic layer → 422.
    assert (
        client.post("/api/alerts", json={"ticker": "AAPL", "op": "==", "value": 1}).status_code
        == 422
    )


def test_missing_required_422(client):
    assert client.post("/api/alerts", json={"ticker": "AAPL", "op": ">="}).status_code == 422


def test_oversize_note_422(client):
    # note flows verbatim into the fired-alert LLM prompt — it's length-capped (defense-in-depth).
    body = {"ticker": "AAPL", "op": ">=", "value": 1, "note": "x" * 3000}
    assert client.post("/api/alerts", json=body).status_code == 422


def test_create_defaults_and_accepts_cooldown_fields(client):
    r = client.post("/api/alerts", json={"ticker": "AAPL", "op": ">=", "value": 150})
    body = r.json()
    assert body["on_trigger"] == "cooldown"
    assert body["cooldown_minutes"] == 1440
    assert body["enabled"] is True

    r = client.post(
        "/api/alerts",
        json={
            "ticker": "MSFT",
            "op": ">=",
            "value": 300,
            "on_trigger": "disable",
            "cooldown_minutes": 0,
        },
    )
    body = r.json()
    assert body["on_trigger"] == "disable"
    assert body["cooldown_minutes"] == 0


def test_cooldown_minutes_out_of_range_422(client):
    body = {"ticker": "AAPL", "op": ">=", "value": 1, "cooldown_minutes": -1}
    assert client.post("/api/alerts", json=body).status_code == 422
    body = {"ticker": "AAPL", "op": ">=", "value": 1, "cooldown_minutes": 999999}
    assert client.post("/api/alerts", json=body).status_code == 422


def test_patch_re_enables_a_disabled_alert(client):
    r = client.post(
        "/api/alerts", json={"ticker": "AAPL", "op": ">=", "value": 150, "on_trigger": "disable"}
    )
    alert_id = r.json()["id"]
    client.patch(f"/api/alerts/{alert_id}", json={"enabled": False})
    r = client.patch(f"/api/alerts/{alert_id}", json={"enabled": True})
    assert r.status_code == 200 and r.json()["enabled"] is True


def test_patch_changes_on_trigger_and_cooldown_minutes(client):
    r = client.post("/api/alerts", json={"ticker": "AAPL", "op": ">=", "value": 150})
    alert_id = r.json()["id"]
    r = client.patch(
        f"/api/alerts/{alert_id}", json={"on_trigger": "disable", "cooldown_minutes": 60}
    )
    assert r.json()["on_trigger"] == "disable"
    assert r.json()["cooldown_minutes"] == 60


# ── name / kind / channels ─────────────────────────────────────────────────────────────


def test_create_with_name_kind_channels_round_trips(client):
    r = client.post(
        "/api/alerts",
        json={
            "ticker": "AAPL",
            "op": ">=",
            "value": 5,
            "name": "Stop-loss zone",
            "kind": "move",
            "channels": ["email"],
        },
    )
    assert r.status_code == 201
    body = r.json()
    assert body["name"] == "Stop-loss zone"
    assert body["kind"] == "move"
    assert body["channels"] == ["email"]


def test_create_without_channels_starts_from_the_price_alert_default(db_isolated):
    from backend.api.alerts import router
    from backend.api.watchlist import router as watchlist_router

    app = FastAPI()
    app.include_router(router)
    app.include_router(watchlist_router)
    client = TestClient(app)
    body = {"ticker": "AAPL", "op": ">=", "value": 150}
    assert client.post("/api/alerts", json=body).json()["channels"] == []

    r = client.patch(
        "/api/watchlist/config", json={"notify_routes": {"price_alerts": ["telegram"]}}
    )
    assert r.status_code == 200
    created = client.post("/api/alerts", json=body).json()
    assert created["channels"] == ["telegram"]

    # The default only seeds a NEW alert — changing it later never touches this one.
    client.patch("/api/watchlist/config", json={"notify_routes": {"price_alerts": []}})
    alerts = client.get("/api/alerts?ticker=AAPL").json()["alerts"]
    assert [a["channels"] for a in alerts if a["id"] == created["id"]] == [["telegram"]]


def test_patch_channels(client):
    alert_id = client.post(
        "/api/alerts", json={"ticker": "AAPL", "op": ">=", "value": 150, "channels": ["email"]}
    ).json()["id"]
    r = client.patch(f"/api/alerts/{alert_id}", json={"channels": ["telegram", "email"]})
    assert r.status_code == 200
    assert r.json()["channels"] == ["telegram", "email"]


def test_patch_name(client):
    r = client.post("/api/alerts", json={"ticker": "AAPL", "op": ">=", "value": 150})
    alert_id = r.json()["id"]
    r = client.patch(f"/api/alerts/{alert_id}", json={"name": "Stop-loss zone"})
    assert r.status_code == 200
    assert r.json()["name"] == "Stop-loss zone"


def test_move_value_out_of_range_422(client):
    body = {"ticker": "AAPL", "op": ">=", "value": 150, "kind": "move"}
    assert client.post("/api/alerts", json=body).status_code == 422


def test_patch_kind_to_move_revalidates_value_422(client):
    r = client.post("/api/alerts", json={"ticker": "AAPL", "op": ">=", "value": 150})
    alert_id = r.json()["id"]
    r = client.patch(f"/api/alerts/{alert_id}", json={"kind": "move"})
    assert r.status_code == 422


def test_bad_kind_422(client):
    body = {"ticker": "AAPL", "op": ">=", "value": 1, "kind": "percent"}
    assert client.post("/api/alerts", json=body).status_code == 422


def test_unknown_channel_422(client):
    body = {"ticker": "AAPL", "op": ">=", "value": 1, "channels": ["sms"]}
    assert client.post("/api/alerts", json=body).status_code == 422
