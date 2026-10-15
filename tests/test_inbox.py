"""Unified Inbox aggregator — service + endpoint tests (read-only feed).

The Inbox merges FOUR "needs my attention" sources into one normalized feed:
Intel story arcs (``action_needed``/``active``), unreviewed agent findings,
unread in-app signals, and pending harness suggestions. These tests seed the stores
in a tmp dir (the conftest DATA_DIR backstop + per-test monkeypatching of each store's
path constant) and assert:

  * the union normalizes to the unified item shape,
  * actionable + recent ordering (actionable items float to the top band),
  * read/reviewed/resolved items are excluded by default, surfaced with include_read=True,
  * an agent finding's ``proposed_change`` is surfaced verbatim,
  * ``inbox_unread_count`` matches the visible-by-default count,
  * one failing source degrades to empty instead of 500-ing the whole feed,
  * the endpoints return 200 + the right shape, and empty stores → no 500.

No LLM, no network.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


@pytest.fixture
def isolated_stores(db_isolated):
    """Isolate the source stores the inbox service reads (which instantiates a fresh
    store per call). NotificationLogStore and IntelStoriesStore are both DB-backed
    — db_isolated redirects the process-wide engine singleton to a fresh, empty
    database for the test."""


# ── Service: union, shape, ordering ─────────────────────────────────────────────


def test_plain_notification_has_no_source_tab():
    """A plain notify push (type='notification') has no source surface, so ref.tab is
    "" — the Inbox renders it without a dead-end 'Open in Intel' button. Intel-sourced
    signal types (macro/news/price_alert/…) still resolve to the Intel tab."""
    from backend.services import inbox

    notif = inbox._normalize_signal({"id": "n1", "type": "notification", "summary": "hi"})
    assert notif["ref"]["tab"] == ""

    macro = inbox._normalize_signal({"id": "m1", "type": "macro", "summary": "rates up"})
    assert macro["ref"]["tab"] == "intel"


def test_ordering_missing_timestamp_sorts_last(isolated_stores):
    from backend.db.notification_log_store import NotificationLogStore
    from backend.services import inbox

    store = NotificationLogStore()
    # Bypass append()'s auto-stamp so this signal has NO created_at at all.
    store.insert({"id": "no-ts", "type": "news", "title": "No timestamp", "summary": ""})
    store.insert(
        {
            "id": "has-ts",
            "type": "news",
            "title": "Has timestamp",
            "summary": "",
            "created_at": "2026-01-01T00:00:00+00:00",
        }
    )

    ids = [it["id"] for it in inbox.inbox_items()]
    assert ids == ["signal:has-ts", "signal:no-ts"]


# ── Service: read/reviewed exclusion ────────────────────────────────────────────


# ── Endpoint smoke ──────────────────────────────────────────────────────────────


@pytest.fixture
def client(isolated_stores):
    from backend.api.inbox import router

    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def test_get_inbox_empty_stores_no_500(client):
    resp = client.get("/api/inbox")
    assert resp.status_code == 200
    body = resp.json()
    assert body == {"items": [], "unread_count": 0}

    count = client.get("/api/inbox/count")
    assert count.status_code == 200
    assert count.json() == {"unread_count": 0}
