"""
Regression tests for inbox dismiss endpoints (POST /api/inbox/signals/{id}/dismiss
and POST /api/inbox/signals/dismiss-all).

Isolated: NotificationLogStore is DB-backed (plan 4d task 2) — db_isolated redirects it to a fresh,
empty database so no live data is touched. No LLM, no network, no full-app lifespan.

Run: .venv/bin/pytest tests/test_inbox_dismiss.py -v
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api.notification_log import router


@pytest.fixture
def client(tmp_path, monkeypatch, db_isolated):
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def _seed_notifications(client: TestClient, titles: list[str]) -> list[str]:
    """Create notification-type signals and return their ids."""
    ids = []
    for title in titles:
        r = client.post(
            "/api/notification-log",
            json={
                "type": "notification",
                "title": title,
                "summary": f"summary for {title}",
            },
        )
        assert r.status_code == 201
        ids.append(r.json()["id"])
    return ids


def test_dismiss_single_marks_read(client):
    """Dismissing a signal marks it read so it drops out of the unread query."""
    (sig_id,) = _seed_notifications(client, ["Hello from Lucius"])

    # Before dismiss: appears in unread list
    items = client.get("/api/notification-log?type=notification&unread_only=true").json()
    assert any(s["id"] == sig_id for s in items)

    # Dismiss
    r = client.post(f"/api/inbox/signals/{sig_id}/dismiss")
    assert r.status_code == 200
    assert r.json()["ok"] is True

    # After dismiss: no longer in unread list
    items = client.get("/api/notification-log?type=notification&unread_only=true").json()
    assert not any(s["id"] == sig_id for s in items)


def test_dismiss_all_clears_inbox(client):
    """dismiss-all marks every notification read."""
    _seed_notifications(client, ["A", "B", "C"])

    # All unread before
    items = client.get("/api/notification-log?type=notification&unread_only=true").json()
    assert len(items) == 3

    r = client.post("/api/inbox/signals/dismiss-all")
    assert r.status_code == 200
    assert r.json()["ok"] is True

    # None unread after
    items = client.get("/api/notification-log?type=notification&unread_only=true").json()
    assert items == []


def test_dismiss_all_empty_store_no_error(client):
    """dismiss-all on an empty store returns ok=True without 500."""
    r = client.post("/api/inbox/signals/dismiss-all")
    assert r.status_code == 200
    assert r.json()["ok"] is True


def test_dismiss_unknown_id_returns_ok_false(client):
    """Dismissing a non-existent id returns ok=False (not 500)."""
    r = client.post("/api/inbox/signals/nonexistent-id/dismiss")
    assert r.status_code == 200
    assert r.json()["ok"] is False


# ── Every inbox KIND is clearable from the inbox (the "Clear all" complaint) ──────
# The Inbox once only dismissed plain signals; intel "alerts" + agent findings showed
# there with no clear control, so clearing left them on screen and looked broken. The
# frontend now dismisses each kind via its own endpoint. Pin that here against the full
# /api/inbox aggregator so a regression trips immediately.


def _inbox_ids(client: TestClient) -> set[str]:
    r = client.get("/api/inbox?include_read=false")
    assert r.status_code == 200
    return {it["id"] for it in r.json()["items"]}
