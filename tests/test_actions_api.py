"""
TestClient smoke tests for backend/api/actions_api.py.

Routes covered:
  GET  /api/actions                  (with and without ?commitment_id filter)
  GET  /api/actions.json
  PATCH /api/actions/{action_id}/status
  POST  /api/action-status           (legacy endpoint)

Isolation: actions are DB-backed now (db.action_store.ActionStore, Wave 5C:
JSON->DB) — db_isolated gives the store a fresh, isolated sqlite file so no
live data is touched; seed via services.actions.write_actions().
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from backend.main import app
from backend.services.actions import write_actions


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def seeded(db_isolated):
    """Seed two actions into the isolated actions table."""
    actions = [
        {"id": 1, "title": "Action One", "status": "pending", "commitment_id": "c-1"},
        {"id": 2, "title": "Action Two", "status": "done", "commitment_id": "c-2"},
    ]
    write_actions(actions)
    return {"actions": actions}


@pytest.fixture
def empty(db_isolated):
    """The isolated actions table, left empty."""
    write_actions([])
    return {}


# ── GET /api/actions ──────────────────────────────────────────────────────────


def test_get_actions_200_returns_list(client, seeded):
    resp = client.get("/api/actions")
    assert resp.status_code == 200
    body = resp.json()
    assert isinstance(body, list)
    assert len(body) == 2


def test_get_actions_all_fields_present(client, seeded):
    resp = client.get("/api/actions")
    assert resp.status_code == 200
    first = resp.json()[0]
    assert first["id"] == 1
    assert first["title"] == "Action One"
    assert first["status"] == "pending"
    assert first["commitment_id"] == "c-1"


def test_get_actions_empty_list_when_no_actions(client, empty):
    resp = client.get("/api/actions")
    assert resp.status_code == 200
    assert resp.json() == []


def test_get_actions_filter_by_commitment_id_returns_match(client, seeded):
    resp = client.get("/api/actions?commitment_id=c-1")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 1
    assert body[0]["commitment_id"] == "c-1"
    assert body[0]["id"] == 1


def test_get_actions_filter_by_commitment_id_no_match_returns_empty(client, seeded):
    resp = client.get("/api/actions?commitment_id=nonexistent")
    assert resp.status_code == 200
    assert resp.json() == []


def test_get_actions_no_filter_returns_all(client, seeded):
    """Omitting commitment_id must return all actions, not filter to None."""
    resp = client.get("/api/actions")
    assert resp.status_code == 200
    assert len(resp.json()) == 2


# ── GET /api/actions.json ─────────────────────────────────────────────────────


def test_get_actions_json_200_returns_list(client, seeded):
    resp = client.get("/api/actions.json")
    assert resp.status_code == 200
    body = resp.json()
    assert isinstance(body, list)
    assert len(body) == 2


def test_get_actions_json_matches_actions_endpoint(client, seeded):
    """Both endpoints should return the same data."""
    r1 = client.get("/api/actions").json()
    r2 = client.get("/api/actions.json").json()
    assert r1 == r2


def test_get_actions_json_empty_list(client, empty):
    resp = client.get("/api/actions.json")
    assert resp.status_code == 200
    assert resp.json() == []


# ── PATCH /api/actions/{action_id}/status ─────────────────────────────────────


def test_patch_action_status_200_ok(client, seeded):
    resp = client.patch("/api/actions/1/status", json={"status": "done"})
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}


def test_patch_action_status_persists_change(client, seeded):
    client.patch("/api/actions/1/status", json={"status": "in_progress"})
    # Read back to verify persistence
    body = client.get("/api/actions").json()
    action_1 = next(a for a in body if a["id"] == 1)
    assert action_1["status"] == "in_progress"


def test_patch_action_status_only_updates_target(client, seeded):
    """Patching action 1 must not alter action 2's status."""
    client.patch("/api/actions/1/status", json={"status": "cancelled"})
    body = client.get("/api/actions").json()
    action_2 = next(a for a in body if a["id"] == 2)
    assert action_2["status"] == "done"


def test_patch_action_status_404_unknown_id(client, seeded):
    resp = client.patch("/api/actions/9999/status", json={"status": "done"})
    assert resp.status_code == 404


def test_patch_action_status_second_action(client, seeded):
    resp = client.patch("/api/actions/2/status", json={"status": "pending"})
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}
    body = client.get("/api/actions").json()
    action_2 = next(a for a in body if a["id"] == 2)
    assert action_2["status"] == "pending"


def test_patch_action_status_missing_body_422(client, seeded):
    """Missing status field → 422 validation error."""
    resp = client.patch("/api/actions/1/status", json={})
    assert resp.status_code == 422


def test_patch_action_status_404_on_empty_store(client, empty):
    resp = client.patch("/api/actions/1/status", json={"status": "done"})
    assert resp.status_code == 404


# ── POST /api/action-status (legacy) ─────────────────────────────────────────


def test_legacy_action_status_200_ok(client, seeded):
    resp = client.post("/api/action-status", json={"id": "1", "status": "done"})
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}


def test_legacy_action_status_persists_change(client, seeded):
    client.post("/api/action-status", json={"id": "2", "status": "cancelled"})
    body = client.get("/api/actions").json()
    action_2 = next(a for a in body if a["id"] == 2)
    assert action_2["status"] == "cancelled"


def test_legacy_action_status_only_updates_target(client, seeded):
    """Legacy endpoint patching action 2 must not alter action 1."""
    client.post("/api/action-status", json={"id": "2", "status": "cancelled"})
    body = client.get("/api/actions").json()
    action_1 = next(a for a in body if a["id"] == 1)
    assert action_1["status"] == "pending"


def test_legacy_action_status_404_unknown_id(client, seeded):
    resp = client.post("/api/action-status", json={"id": "9999", "status": "done"})
    assert resp.status_code == 404


def test_legacy_action_status_accepts_int_id_string(client, seeded):
    """Legacy endpoint casts body['id'] to int — so string "1" must work."""
    resp = client.post("/api/action-status", json={"id": "1", "status": "in_progress"})
    assert resp.status_code == 200


def test_legacy_action_status_404_on_empty_store(client, empty):
    resp = client.post("/api/action-status", json={"id": "1", "status": "done"})
    assert resp.status_code == 404


# ── Cross-endpoint consistency ─────────────────────────────────────────────────


def test_patch_and_legacy_share_same_store(client, seeded):
    """A PATCH via the new endpoint must be visible via the legacy read path and vice versa."""
    client.patch("/api/actions/1/status", json={"status": "done"})
    # legacy endpoint should see the same state
    client.post("/api/action-status", json={"id": "2", "status": "archived"})

    body = client.get("/api/actions.json").json()
    statuses = {a["id"]: a["status"] for a in body}
    assert statuses[1] == "done"
    assert statuses[2] == "archived"


def test_get_actions_filter_multiple_with_same_commitment_id(client, db_isolated):
    """Filter returns ALL matching actions, not just the first."""
    actions = [
        {"id": 1, "title": "A", "status": "pending", "commitment_id": "shared"},
        {"id": 2, "title": "B", "status": "done", "commitment_id": "shared"},
        {"id": 3, "title": "C", "status": "pending", "commitment_id": "other"},
    ]
    write_actions(actions)

    resp = client.get("/api/actions?commitment_id=shared")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 2
    ids = {a["id"] for a in body}
    assert ids == {1, 2}
