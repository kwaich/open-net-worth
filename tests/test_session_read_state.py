"""Server-side "read" state for conversations (``SessionStore.mark_read``).

The unread marker used to be a per-browser ``localStorage`` stamp, so reading a reply anywhere
else never cleared it. The stamp now lives on the session meta (``last_read_at``) and flows out
through ``GET /api/sessions`` to every client.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import backend.ai.assistant as assistant
import backend.api.chat as chat_mod
from backend.db.session_store import SessionStore

EARLY = "2026-10-03T08:00:00+00:00"
LATE = "2026-10-03T09:00:00+00:00"


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(chat_mod.router)
    return TestClient(app)


def _seed(session_id: str, **kwargs) -> None:
    assistant.upsert_session_meta(session_id, message_count=2, **kwargs)


def _meta(session_id: str) -> dict:
    return SessionStore().get_meta(session_id) or {}


def test_marking_read_stamps_the_session_without_moving_updated_at():
    _seed("s-read", last_turn_status="ok")
    before = _meta("s-read")

    stored = SessionStore().mark_read(["s-read"], at=LATE)

    after = _meta("s-read")
    assert stored == {"s-read": LATE}
    assert after["last_read_at"] == LATE
    # Reading is not activity: `updated_at` sorts the list and is what "changed" means.
    assert after["updated_at"] == before["updated_at"]
    assert after["last_turn_at"] == before["last_turn_at"]


def test_read_stamp_only_moves_forward():
    """Two clients marking the same conversation out of order must not un-read it."""
    _seed("s-mono", last_turn_status="ok")
    store = SessionStore()
    store.mark_read(["s-mono"], at=LATE)

    stored = store.mark_read(["s-mono"], at=EARLY)

    assert stored == {"s-mono": LATE}
    assert _meta("s-mono")["last_read_at"] == LATE


def test_a_session_that_does_not_exist_is_skipped_never_created():
    """The client reports views for still-unsent drafts too; that must not mint a ghost row."""
    stored = SessionStore().mark_read(["never-persisted"], at=LATE)

    assert stored == {}
    assert SessionStore().get_meta("never-persisted") is None
    assert all(e["id"] != "never-persisted" for e in assistant.list_sessions())


def test_a_new_turn_after_the_read_stamp_is_unread_again():
    """The invariant the client relies on: `last_turn_at` (server) moves past `last_read_at`."""
    _seed("s-new", last_turn_status="ok")
    SessionStore().mark_read(["s-new"], at="2026-10-03T00:00:00+00:00")
    _seed("s-new", last_turn_status="ok")  # a later turn ends

    meta = _meta("s-new")
    assert meta["last_turn_at"] > meta["last_read_at"]


def test_mark_read_endpoint_marks_many_and_lists_the_stamp(client):
    _seed("s-a", last_turn_status="ok")
    _seed("s-b", last_turn_status="ok")

    res = client.post("/api/sessions/read", json={"ids": ["s-a", "s-b", "s-draft", "s-a"]})

    assert res.status_code == 200
    stamps = res.json()["last_read_at"]
    assert set(stamps) == {"s-a", "s-b"}  # the draft was skipped, the duplicate collapsed
    listed = {e["id"]: e for e in client.get("/api/sessions").json()}
    assert listed["s-a"]["last_read_at"] == stamps["s-a"]
    assert listed["s-b"]["last_read_at"] == stamps["s-b"]


@pytest.mark.parametrize("ids", [[], ["x"] * 201])
def test_mark_read_endpoint_bounds_the_batch(client, ids):
    assert client.post("/api/sessions/read", json={"ids": ids}).status_code == 422
