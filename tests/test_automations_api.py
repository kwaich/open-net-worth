"""TestClient smoke test for GET /api/automations/activity — the Lucius landing screen's
Activity panel endpoint.

Isolation: automations/automation runs are DB-backed (backend.db.automation_store,
backend.db.automation_run_store) — db_isolated gives both tables a fresh, isolated sqlite
file so no live data is touched; seed via the stores directly.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from backend.db.automation_run_store import AutomationRunStore
from backend.db.automation_store import AutomationStore
from backend.main import app


@pytest.fixture
def client():
    return TestClient(app)


def test_get_activity_200_shape_when_empty(client, db_isolated):
    resp = client.get("/api/automations/activity")
    assert resp.status_code == 200
    assert resp.json() == {"active": [], "scheduled": [], "failed": []}


def test_get_activity_returns_active_and_scheduled_rows(client, db_isolated):
    automations = AutomationStore()
    runs = AutomationRunStore()

    waiting_automation = automations.create({"name": "Needs Review"})
    waiting_run = runs.start(
        {"automation_id": waiting_automation["id"], "session_id": "session_needs_review"}
    )
    runs.transition(waiting_run["id"], {"running"}, "waiting", {})

    scheduled_automation = automations.create({"name": "Weekly Report"})
    future = (datetime.now(timezone.utc) + timedelta(hours=3)).isoformat()
    automations.update_by_id(scheduled_automation["id"], {"next_run_at": future})

    resp = client.get("/api/automations/activity")
    assert resp.status_code == 200
    body = resp.json()

    assert len(body["active"]) == 1
    active_row = body["active"][0]
    assert active_row["automation_id"] == waiting_automation["id"]
    assert active_row["automation_name"] == "Needs Review"
    assert active_row["run_id"] == waiting_run["id"]
    assert active_row["session_id"] == "session_needs_review"
    assert active_row["status"] == "waiting"
    assert "started_at" in active_row

    assert len(body["scheduled"]) == 1
    scheduled_row = body["scheduled"][0]
    assert scheduled_row["automation_id"] == scheduled_automation["id"]
    assert scheduled_row["automation_name"] == "Weekly Report"
    assert scheduled_row["next_run_at"] == future
