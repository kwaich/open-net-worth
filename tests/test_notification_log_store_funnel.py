"""
Regression tests for the notification-log surface cleanups (overlap audit N2/N3).

N2 — `api/notification_log.py` routes through `NotificationLogStore` (not raw json.dump), so
a created notification funnels through the same store write path (and thus the checkpoint
write-hook) and the notification schema lives in one place. Covered: POST → store sees it,
GET filters, mark-read, clear-all.

N3 — the `get_notifications` tool no longer advertises a `price_alert` filter that can
never match (price alerts go to IntelStoriesStore, not NotificationLogStore). Covered: the
advertised type enum is a subset of the types NotificationLogStore can actually contain.

NotificationLogStore is DB-backed (plan 4d task 2; renamed from SignalsStore at revision
0044); db_isolated redirects it to a throwaway database. No LLM, no network.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def isolated_signals(db_isolated):
    # NotificationLogStore is DB-backed now (plan 4d task 2) — db_isolated redirects it.
    return db_isolated


@pytest.fixture
def client(isolated_signals):
    from fastapi import FastAPI

    from backend.api.notification_log import router

    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


# ── N2: the API route funnels through NotificationLogStore ──────────────────────


def test_create_signal_goes_through_store(client):
    from backend.db import NotificationLogStore

    resp = client.post("/api/notification-log", json={"title": "Fed decision", "type": "macro"})
    assert resp.status_code == 201
    created = resp.json()
    assert created["title"] == "Fed decision"
    assert created["type"] == "macro"
    assert created["read"] is False
    assert "id" in created and "created_at" in created

    # The notification is visible through the store itself — proving the route wrote via
    # NotificationLogStore, not a parallel raw-json path.
    in_store = NotificationLogStore().all()
    assert len(in_store) == 1
    assert in_store[0]["id"] == created["id"]


def test_get_signals_filters_by_type_and_unread(client):
    client.post("/api/notification-log", json={"title": "A", "type": "news"})
    client.post("/api/notification-log", json={"title": "B", "type": "macro"})

    assert len(client.get("/api/notification-log").json()) == 2
    macro = client.get("/api/notification-log", params={"type": "macro"}).json()
    assert [s["title"] for s in macro] == ["B"]
    assert len(client.get("/api/notification-log", params={"unread_only": True}).json()) == 2


def test_mark_read_and_unread_count(client):
    created = client.post("/api/notification-log", json={"title": "X", "type": "news"}).json()
    assert client.get("/api/notification-log/unread_count").json()["count"] == 1

    patched = client.patch(f"/api/notification-log/{created['id']}", json={"read": True}).json()
    assert patched["read"] is True
    assert client.get("/api/notification-log/unread_count").json()["count"] == 0


def test_clear_and_delete(client):
    a = client.post("/api/notification-log", json={"title": "A", "type": "news"}).json()
    client.post("/api/notification-log", json={"title": "B", "type": "news"})

    assert client.delete(f"/api/notification-log/{a['id']}").json() == {"ok": True}
    assert len(client.get("/api/notification-log").json()) == 1

    assert client.delete("/api/notification-log").json() == {"ok": True}
    assert client.get("/api/notification-log").json() == []


# ── N3: the advertised type enum matches what NotificationLogStore can contain ──


def test_get_signals_enum_has_no_unmatchable_type():
    """price_alert was advertised but never written to NotificationLogStore (alerts go to
    IntelStoriesStore), so the filter always returned empty. It must be gone, and the
    advertised enum must be a subset of the types NotificationLogStore can actually hold."""
    from backend.ai.tools import TOOL_SCHEMAS

    schema = next(t for t in TOOL_SCHEMAS if t["name"] == "get_notifications")
    advertised = set(schema["input_schema"]["properties"]["type"]["enum"])

    # The types NotificationLogStore can hold: the create-endpoint default + the monitoring
    # engine post (news/macro/opportunity) and the Notifier's in-app channel (notification).
    can_contain = {"news", "macro", "opportunity", "notification"}

    assert "price_alert" not in advertised
    assert advertised <= can_contain, f"advertised {advertised - can_contain} cannot match"
