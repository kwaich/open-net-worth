"""A conversation's change stamp — what lets a returning client skip a transcript re-read.

The client re-reads the transcript only when this stamp moved since its last fetch, so the
one property that matters is: EVERY change to what the transcript renders moves it. A stamp
that stays still across a real change hides that change from the client.
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

import backend.ai.assistant as assistant
import backend.api.chat as chat_mod
from backend.db.session_store import SessionStore


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(chat_mod.router)
    return TestClient(app)


def _seed(sid: str) -> SessionStore:
    s = SessionStore()
    s.upsert_meta(sid, {"title": "t"})
    return s


def _msgs(*texts: str) -> list:
    return [
        {"role": "user" if i % 2 == 0 else "assistant", "content": t} for i, t in enumerate(texts)
    ]


def test_an_append_moves_the_stamp(db_isolated):
    s = _seed("rev-append")
    s.append_messages("rev-append", _msgs("hi"))
    before = assistant.session_revision("rev-append")
    s.append_messages("rev-append", _msgs("more"))
    assert assistant.session_revision("rev-append") != before


def test_an_in_place_edit_moves_the_stamp_even_at_the_same_count(db_isolated):
    """`set_transcript` rewrites a changed message as truncate + re-append — same count,
    new ids. A count-only stamp would miss exactly this (a resolved card, an aborted
    tool_use rewritten in place)."""
    s = _seed("rev-edit")
    s.set_transcript("rev-edit", _msgs("q", "pending card"))
    before = assistant.session_revision("rev-edit")
    s.set_transcript("rev-edit", _msgs("q", "card answered"))
    assert s.message_count("rev-edit") == 2
    assert assistant.session_revision("rev-edit") != before


def test_a_rollback_that_only_truncates_moves_the_stamp(db_isolated):
    s = _seed("rev-trunc")
    s.append_messages("rev-trunc", _msgs("a", "b", "c"))
    before = assistant.session_revision("rev-trunc")
    s.truncate_messages_from("rev-trunc", 2)
    assert assistant.session_revision("rev-trunc") != before


def test_a_turn_ending_moves_the_stamp_without_a_new_message(db_isolated):
    """A failed or paused turn renders from the meta (`last_turn_*`) too."""
    s = _seed("rev-turn")
    s.append_messages("rev-turn", _msgs("hi"))
    before = assistant.session_revision("rev-turn")
    assistant.upsert_session_meta("rev-turn", last_turn_status="error", last_turn_error="boom")
    assert assistant.session_revision("rev-turn") != before


def test_a_session_never_written_reads_as_unknown(db_isolated):
    """Empty means "unknown" — the client must re-read, never treat it as unchanged."""
    _seed("rev-legacy")
    assert assistant.session_revision("rev-legacy") == ""


def test_nothing_changed_means_the_same_stamp(db_isolated):
    s = _seed("rev-still")
    s.append_messages("rev-still", _msgs("hi", "hello"))
    assert assistant.session_revision("rev-still") == assistant.session_revision("rev-still")


def test_messages_and_revision_endpoints_agree(db_isolated):
    s = _seed("rev-api")
    s.append_messages("rev-api", _msgs("hi", "hello"))
    client = _client()
    stamp = client.get("/api/sessions/rev-api/revision").json()["revision"]
    assert client.get("/api/sessions/rev-api/messages?tail=30").json()["revision"] == stamp
    s.append_messages("rev-api", _msgs("again"))
    assert client.get("/api/sessions/rev-api/revision").json()["revision"] != stamp
