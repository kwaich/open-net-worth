"""TestClient smoke for /api/recurring + the AI tool surface
(.claude/rfcs/2026-07-27-cashflow-stream-model.md)."""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from backend.db.recurring_stream_store import RecurringStreamStore


def _payload(result):
    """execute_tool → (json_text, proposed_change); tests care about the decoded payload."""
    text, _proposed = result
    return json.loads(text)


@pytest.fixture()
def client(tmp_path, monkeypatch, db_isolated):
    """RecurringStreamStore is DB-backed (plan 4a task 3) — db_isolated gives this test
    its own fresh, migrated database."""
    from backend.api import recurring as api_recurring
    from backend.services import recurring_baseline

    store = RecurringStreamStore()
    monkeypatch.setattr(api_recurring, "RecurringStreamStore", lambda: store)
    monkeypatch.setattr(recurring_baseline, "RecurringStreamStore", lambda: store)

    from backend.main import app

    # A BARE TestClient, like every other API test here — never `with TestClient(app)`.
    # The context-manager form runs the app's lifespan/startup, which reconfigures global
    # logging; that leaked into every later test file asserting on log records (10 of them
    # failed only in a full-suite run, and passed in isolation).
    yield TestClient(app), store


def test_get_recurring_returns_the_classification_shape(client):
    c, _ = client
    r = c.get("/api/recurring")
    assert r.status_code == 200
    body = r.json()
    for key in (
        "streams",
        "candidates",
        "variable_in",
        "variable_out",
        "one_offs",
        "base_currency",
    ):
        assert key in body, key


def test_declared_stream_endpoints_are_gone(client):
    c, _ = client
    body = {"label": "Raise", "direction": "in", "amount": 19000, "cadence": "monthly"}
    assert c.post("/api/recurring/declared", json=body).status_code in (404, 405)
    assert c.patch("/api/recurring/declared/rst_x", json=body).status_code in (404, 405)
    assert c.delete("/api/recurring/declared/rst_x").status_code in (404, 405)


def test_a_dormant_declared_row_is_not_returned(client):
    c, store = client
    store.insert(
        {
            "type": "declared",
            "id": "rst_old",
            "direction": "in",
            "label": "Salary",
            "amount": 5400,
            "cadence": "monthly",
            "anchor_date": "2026-09-01",
        }
    )
    assert c.get("/api/recurring").json()["streams"] == []


def test_status_confirm_dismiss_reset_roundtrip(client):
    c, store = client
    assert c.post("/api/recurring/rs_x/status", json={"status": "dismiss"}).status_code == 200
    assert store.overlays()["rs_x"]["status"] == "dismissed"
    assert c.post("/api/recurring/rs_x/status", json={"status": "confirm"}).status_code == 200
    assert store.overlays()["rs_x"]["status"] == "confirmed"
    assert c.post("/api/recurring/rs_x/status", json={"status": "reset"}).status_code == 200
    assert store.overlays() == {}


def test_status_rejects_an_unknown_action(client):
    c, _ = client
    r = c.post("/api/recurring/rs_x/status", json={"status": "maybe"})
    assert r.status_code == 422
    assert r.json()["detail"].startswith("status:")


# ── AI tools ──────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_ai_tools_read_and_write(tmp_path, monkeypatch, db_isolated):
    from backend.ai.tools import execute_tool
    from backend.api import recurring as api_recurring
    from backend.services import recurring_baseline

    store = RecurringStreamStore()
    monkeypatch.setattr(recurring_baseline, "RecurringStreamStore", lambda: store)
    monkeypatch.setattr(api_recurring, "RecurringStreamStore", lambda: store)
    import backend.ai.tools.recurring as tools_recurring

    monkeypatch.setattr(tools_recurring, "RecurringStreamStore", lambda: store)

    # execute_tool returns (json_text, proposed_change) — the registry's handler contract.
    read = _payload(await execute_tool("get_recurring_streams", {}))
    assert "streams" in read and "showing" in read and "total" in read

    dismissed = _payload(
        await execute_tool("set_recurring_status", {"key": "sig_x", "status": "dismiss"})
    )
    assert dismissed["ok"] is True and dismissed["status"] == "dismissed"
    assert store.overlays()["sig_x"]["status"] == "dismissed"

    reset = _payload(
        await execute_tool("set_recurring_status", {"key": "sig_x", "status": "reset"})
    )
    assert reset["ok"] is True and reset["reset"] is True
    assert store.overlays() == {}
