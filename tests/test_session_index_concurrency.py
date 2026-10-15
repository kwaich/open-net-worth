"""Regression: the session index must not lose conversations.

WAVE 5E (JSON->DB): sessions now live one row per session (backend/db/session_store.py) —
meta/transcript/fold/proactive are columns on that SAME row, replacing the old per-file
(`sessions/<id>.json`) + shared `sessions/index.json` listing. The former lost-update race
(the hourly background agents rewriting the index concurrently with the user's chat saves,
under an unlocked non-atomic read-modify-write) is now closed at the DB layer: "one sessions
table row-update is atomic" (assistant.py's own module comment) replaces both the old
process-local lock and the cross-process file lock. These tests pin:
  1. concurrent upserts never drop an entry (now a DB-transaction property), and
  2. two sequential upserts both land intact (no torn/partial write).

The OLD recovery scan ("a conversation whose file exists on disk but is missing from the
index is re-listed") is gone by construction now — a session's meta and its transcript are
columns on the SAME row, so that drift cannot happen (see list_sessions()'s own docstring).
The two tests that pinned that file-recovery mechanism are flagged/skipped below rather than
adapted — there is nothing left in the new architecture for them to exercise.
"""

from __future__ import annotations

import json
import threading

import pytest

import backend.ai.assistant as A


def test_concurrent_upserts_no_lost_update(db_isolated):
    n = 50

    def write(i: int) -> None:
        A.upsert_session_meta(f"s{i}", title=f"t{i}", message_count=1)

    threads = [threading.Thread(target=write, args=(i,)) for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    ids = {e["id"] for e in A._read_index()}
    missing = [f"s{i}" for i in range(n) if f"s{i}" not in ids]
    assert not missing, f"lost-update race dropped {len(missing)} conversations: {missing[:5]}"


@pytest.mark.skip(
    reason="Pinned the pre-migration file-recovery scan (a session whose per-file transcript "
    "existed but had fallen out of index.json got re-listed). Sessions are one DB row now "
    "(meta+transcript on the SAME row, backend/db/session_store.py) — that drift is "
    "structurally impossible, per list_sessions()'s own docstring ('That drift cannot happen "
    "any more'). No DB-shaped equivalent to re-target this at. Flagged for owner: delete "
    "outright, this is not a seeding fix or a production bug."
)
def test_recovery_relists_ondisk_session(tmp_path, monkeypatch):
    sdir = _isolate(tmp_path, monkeypatch)  # noqa: F821 — helper removed in Wave 5; test skipped, owner to delete
    # A conversation file on disk that is NOT in the index (delisted by a past race).
    (sdir / "c1.json").write_text(
        json.dumps([{"role": "user", "content": "what is my net worth?"}]), encoding="utf-8"
    )
    sessions = A.list_sessions()
    rec = {s["id"]: s for s in sessions}
    assert "c1" in rec, "on-disk conversation missing from the index was not recovered"
    assert rec["c1"]["title"] == "what is my net worth?"
    assert rec["c1"]["message_count"] == 1
    # Recovery is persisted so it sticks.
    assert "c1" in {e["id"] for e in A._read_index()}


@pytest.mark.skip(
    reason="Pinned the pre-migration file-recovery scan's filtering (an orphaned "
    "scheduled.json / empty.json on disk must not surface). That scan is gone (see "
    "test_recovery_relists_ondisk_session above) — the 'scheduled must not leak' property "
    "is independently covered, via the real (non-recovery) path, by "
    "test_list_sessions_excludes_scheduled_when_in_index and "
    "test_list_sessions_excludes_all_internal_ids below. Flagged for owner, not deleted."
)
def test_recovery_skips_scheduled_and_empty(tmp_path, monkeypatch):
    sdir = _isolate(tmp_path, monkeypatch)  # noqa: F821 — helper removed in Wave 5; test skipped, owner to delete
    # The agents' internal session must NOT surface as a user conversation.
    (sdir / "scheduled.json").write_text(
        json.dumps([{"role": "user", "content": "agent turn"}]), encoding="utf-8"
    )
    # An empty file is not a real conversation.
    (sdir / "empty.json").write_text(json.dumps([]), encoding="utf-8")
    ids = {s["id"] for s in A.list_sessions()}
    assert "scheduled" not in ids
    assert "empty" not in ids


def test_write_index_is_atomic_no_partial(db_isolated):
    """Adapted: the old assertion read the shared index.json back off disk to confirm an
    atomic write never left it torn. There is no such file any more — each upsert is its own
    DB transaction (SessionStore._upsert) — so the DB-shaped equivalent is simply that both
    sequential upserts land intact with nothing lost or corrupted in between."""
    A.upsert_session_meta("a", title="A", message_count=2)
    A.upsert_session_meta("b", title="B", message_count=3)
    ids = {e["id"] for e in A._read_index()}
    assert ids == {"a", "b"}


def test_list_sessions_excludes_scheduled_when_in_index(db_isolated):
    """list_sessions() must not return the 'scheduled' session even when it is present
    in the index (written there by upsert_session_meta during a background run).  The
    index entry is NOT deleted — agents still use it — but the LISTING hides it."""
    # Simulate what the background scheduler does: upsert the 'scheduled' session.
    A.upsert_session_meta("scheduled", message_count=5)
    # Also add a real user conversation so we can confirm it is still listed.
    A.upsert_session_meta("user-abc", title="My chat", message_count=3)

    sessions = A.list_sessions()
    ids = {s["id"] for s in sessions}

    assert "scheduled" not in ids, "'scheduled' internal session leaked into the listing"
    assert "user-abc" in ids, "real user session was incorrectly hidden"

    # The raw index still holds the 'scheduled' entry so agents' state is intact.
    raw_ids = {e["id"] for e in A._read_index()}
    assert "scheduled" in raw_ids, "scheduled entry was deleted from the index (must be preserved)"


def test_list_sessions_excludes_all_internal_ids(db_isolated):
    """Every id in _INTERNAL_SESSION_IDS is excluded from the listing."""
    for sid in A._INTERNAL_SESSION_IDS:
        A.upsert_session_meta(sid, message_count=1)
    A.upsert_session_meta("user-xyz", title="User session", message_count=2)

    ids = {s["id"] for s in A.list_sessions()}
    for sid in A._INTERNAL_SESSION_IDS:
        assert sid not in ids, f"internal session '{sid}' leaked into the listing"
    assert "user-xyz" in ids


def test_deleted_conversation_does_not_resurrect(db_isolated):
    """A deleted conversation must NOT be re-listed.

    GIVEN a session exists (one row: meta + transcript),
    WHEN it is deleted via clear_session (one atomic DELETE of that row — see
    assistant._delete_session_row / db.session_store.SessionStore.delete),
    THEN list_sessions() does NOT return it — there is no separate on-disk transcript left
    for a (now fully retired) recovery loop to revive it from.
    """
    sid = "to-be-deleted"

    # Create the session row as a real session would have (meta + a real transcript).
    A.save_session_durable(sid, [{"role": "user", "content": "delete me"}])
    A.upsert_session_meta(sid, title="Delete me", message_count=1)

    # Confirm it is listed before deletion.
    assert sid in {s["id"] for s in A.list_sessions()}, "session not found before deletion"

    # Delete via the real delete path (clears in-memory SESSIONS dict, deletes the DB row).
    A.clear_session(sid)

    # The session must not reappear in the listing.
    ids = {s["id"] for s in A.list_sessions()}
    assert sid not in ids, "deleted conversation was resurrected"

    # The index must also not contain it.
    assert sid not in {e["id"] for e in A._read_index()}, "deleted session re-entered the index"
