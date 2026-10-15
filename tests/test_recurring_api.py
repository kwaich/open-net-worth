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


def test_declare_then_delete_a_stream(client):
    c, _ = client
    r = c.post(
        "/api/recurring/declared",
        json={
            "label": "Raise",
            "direction": "in",
            "amount": 19000,
            "cadence": "monthly",
            "anchor_date": "2026-09-26",
        },
    )
    assert r.status_code == 200, r.text
    sid = r.json()["stream"]["id"]
    assert sid.startswith("rst_")

    # A declared stream joins the projection under `key` (the same field a DETECTED
    # stream uses) so every consumer stays single-path.
    assert any(s["key"] == sid for s in c.get("/api/recurring").json()["streams"])

    assert c.delete(f"/api/recurring/declared/{sid}").status_code == 200
    assert c.delete(f"/api/recurring/declared/{sid}").status_code == 404


def test_declare_rejects_a_bad_field_with_a_plain_prefixed_message(client):
    c, _ = client
    r = c.post(
        "/api/recurring/declared",
        json={
            "label": "x",
            "direction": "in",
            "amount": 10,
            "cadence": "hourly",
            "anchor_date": "2026-09-26",
        },
    )
    assert r.status_code == 422
    assert r.json()["detail"].startswith("cadence:")


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

    made = _payload(
        await execute_tool(
            "declare_recurring_stream",
            {
                "label": "Gym",
                "direction": "out",
                "amount": 60,
                "cadence": "monthly",
                "anchor_date": "2026-08-01",
            },
        )
    )
    assert made["ok"] is True
    sid = made["stream"]["id"]

    gone = _payload(await execute_tool("remove_recurring_streams", {"stream_ids": [sid]}))
    assert gone["ok"] is True and gone["deleted"] == 1

    missing = _payload(await execute_tool("remove_recurring_streams", {"stream_ids": [sid]}))
    assert missing["ok"] is False and "error" in missing


@pytest.mark.asyncio
async def test_ai_declare_surfaces_validation_as_an_error_not_an_exception(
    tmp_path, monkeypatch, db_isolated
):
    import backend.ai.tools.recurring as tools_recurring
    from backend.ai.tools import execute_tool

    store = RecurringStreamStore()
    monkeypatch.setattr(tools_recurring, "RecurringStreamStore", lambda: store)
    out = _payload(
        await execute_tool(
            "declare_recurring_stream",
            {
                "label": "x",
                "direction": "in",
                "amount": 0,
                "cadence": "monthly",
                "anchor_date": "2026-08-01",
            },
        )
    )
    assert "error" in out and out["error"].startswith("amount:")


@pytest.mark.asyncio
async def test_a_negative_amount_is_coerced_not_rejected(tmp_path, monkeypatch, db_isolated):
    """`direction` already carries the sign, so a negative amount is an obvious restatement
    of the same intent, not an error — abs() it (the codebase's standing coercion rule for
    money inputs) rather than bouncing the model over a sign convention."""
    import backend.ai.tools.recurring as tools_recurring
    from backend.ai.tools import execute_tool

    store = RecurringStreamStore()
    monkeypatch.setattr(tools_recurring, "RecurringStreamStore", lambda: store)
    out = _payload(
        await execute_tool(
            "declare_recurring_stream",
            {
                "label": "Rent",
                "direction": "out",
                "amount": -2150,
                "cadence": "monthly",
                "anchor_date": "2026-08-01",
            },
        )
    )
    assert out["ok"] is True
    assert out["stream"]["amount"] == 2150.0
