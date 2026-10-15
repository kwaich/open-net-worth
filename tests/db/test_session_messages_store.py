"""The append-only message table that replaces the whole-transcript JSON column.

RFC ``.claude/rfcs/2026-08-24-transcript-becomes-a-message-table.md``. ``seq`` is 0-based
and equals what used to be the list index, which is what lets every positional consumer
(failed-turn rollback, checkpoint anchor, fold window) map across unchanged.
"""

from __future__ import annotations

from backend.db.session_store import SessionStore


def _seed(store: SessionStore, session_id: str) -> str:
    """Create the session ROW. Messages carry a FK to it since revision 0053, so appending
    to a session that does not exist is a constraint violation — by design: a message must
    never outlive, or pre-date, its conversation."""
    store.upsert_meta(session_id, {"title": "t"})
    return session_id


def _msgs(n: int, base: int = 0) -> list:
    return [
        {"role": "user" if i % 2 == 0 else "assistant", "content": f"m{base + i}"} for i in range(n)
    ]


def test_append_then_read_round_trips_in_order(db_isolated):
    s = SessionStore()
    _seed(s, "sess-a")
    s.append_messages("sess-a", _msgs(3))
    got = s.get_messages("sess-a")
    assert [m["content"] for m in got] == ["m0", "m1", "m2"]
    assert [m["role"] for m in got] == ["user", "assistant", "user"]
    assert s.message_count("sess-a") == 3


def test_append_only_writes_the_tail(db_isolated):
    """The whole point: a round inserts the N rows it produced, not the whole conversation."""
    s = SessionStore()
    _seed(s, "sess-b")
    s.append_messages("sess-b", _msgs(2))
    s.append_messages("sess-b", _msgs(2, base=2))
    assert [m["content"] for m in s.get_messages("sess-b")] == ["m0", "m1", "m2", "m3"]


def test_block_shaped_content_survives_verbatim(db_isolated):
    """``content`` is the OPAQUE provider shape — a string OR a block list carrying
    tool_use/tool_result/image refs. Nothing here may reinterpret it."""
    s = SessionStore()
    _seed(s, "sess-c")
    blocks = [
        {"type": "text", "text": "hi"},
        {"type": "tool_use", "id": "toolu_1", "name": "get_net_worth", "input": {"a": 1}},
    ]
    s.append_messages("sess-c", [{"role": "assistant", "content": blocks}])
    assert s.get_messages("sess-c")[0]["content"] == blocks


def test_timestamps_are_preserved_and_optional(db_isolated):
    s = SessionStore()
    _seed(s, "sess-d")
    s.append_messages(
        "sess-d",
        [{"role": "user", "content": "a", "ts": 1234}, {"role": "assistant", "content": "b"}],
    )
    got = s.get_messages("sess-d")
    assert got[0]["ts"] == 1234, "a caller-supplied stamp is preserved"
    assert isinstance(got[1].get("ts"), int), (
        "a message with no stamp is stamped AT INSERT — pre-0053 it stayed null, which is "
        "why 15,035 of 16,378 real messages had no creation time at all"
    )


def test_server_side_slicing(db_isolated):
    """`tail=30` used to rehydrate the whole transcript and slice in Python."""
    s = SessionStore()
    _seed(s, "sess-e")
    s.append_messages("sess-e", _msgs(10))
    assert [m["content"] for m in s.get_messages("sess-e", start=7)] == ["m7", "m8", "m9"]
    assert [m["content"] for m in s.get_messages("sess-e", end=3)] == ["m0", "m1", "m2"]
    assert [m["content"] for m in s.get_messages("sess-e", start=4, end=6)] == ["m4", "m5"]


def test_truncate_from_is_the_rollback_primitive(db_isolated):
    """The append-only twin of ``del history[snapshot:]`` — and of /rewind's
    restore-to-anchor, which is the same operation on this table."""
    s = SessionStore()
    _seed(s, "sess-f")
    s.append_messages("sess-f", _msgs(6))
    s.truncate_messages_from("sess-f", 4)
    assert [m["content"] for m in s.get_messages("sess-f")] == ["m0", "m1", "m2", "m3"]
    s.truncate_messages_from("sess-f", 0)
    assert s.get_messages("sess-f") == []


def test_sessions_are_isolated_from_each_other(db_isolated):
    s = SessionStore()
    _seed(s, "sess-g")
    _seed(s, "sess-h")
    s.append_messages("sess-g", _msgs(2))
    s.append_messages("sess-h", _msgs(3))
    s.truncate_messages_from("sess-g", 0)
    assert s.get_messages("sess-g") == []
    assert s.message_count("sess-h") == 3


def test_appending_nothing_is_a_no_op(db_isolated):
    s = SessionStore()
    _seed(s, "sess-i")
    s.append_messages("sess-i", [])
    assert s.get_messages("sess-i") == []


# ── the transcript API, now backed by the table ───────────────────────────────────────


def test_get_transcript_distinguishes_no_session_from_empty(db_isolated):
    """``None`` = no such session; ``[]`` = a session with nothing said. Load-bearing:
    ``assistant._load_session`` branches on it."""
    s = SessionStore()
    assert s.get_transcript("never-existed") is None
    s.upsert_meta("sess-empty", {"title": "x"})
    assert s.get_transcript("sess-empty") == []


def test_set_transcript_round_trips(db_isolated):
    s = SessionStore()
    convo = [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "hello"}]
    s.set_transcript("sess-rt", convo)
    got = s.get_transcript("sess-rt")
    assert [{k: v for k, v in m.items() if k != "ts"} for m in got] == convo
    assert all(isinstance(m["ts"], int) for m in got), "every message is stamped at insert"
    assert (s.get_meta("sess-rt") or {})["message_count"] == 2


def test_set_transcript_only_writes_the_NEW_tail(db_isolated):
    """The whole point of the migration: adding two messages to a long conversation must
    not rewrite the conversation. Proven by watching the INSERTs, not by timing."""
    s = SessionStore()
    convo = [{"role": "user", "content": f"m{i}"} for i in range(50)]
    s.set_transcript("sess-tail", convo)

    inserted: list[int] = []
    real_append = SessionStore.append_messages

    def spy(self, session_id, messages):
        inserted.append(len(messages))
        return real_append(self, session_id, messages)

    SessionStore.append_messages = spy
    try:
        s.set_transcript("sess-tail", convo + [{"role": "assistant", "content": "new"}])
    finally:
        SessionStore.append_messages = real_append

    assert inserted == [1], f"expected ONE new row, wrote {inserted}"
    assert len(s.get_transcript("sess-tail")) == 51


def test_set_transcript_persists_an_IN_PLACE_edit(db_isolated):
    """A turn really does mutate entries in place — ``_abort_stale_pending`` rewrites a
    dangling tool_use. Appending blindly past the old length would silently lose that."""
    s = SessionStore()
    convo = [{"role": "user", "content": "a"}, {"role": "assistant", "content": "b"}]
    s.set_transcript("sess-edit", convo)
    edited = [{"role": "user", "content": "a"}, {"role": "assistant", "content": "B-EDITED"}]
    s.set_transcript("sess-edit", edited)
    got = s.get_transcript("sess-edit")
    assert [{k: v for k, v in m.items() if k != "ts"} for m in got] == edited


def test_set_transcript_handles_a_shorter_list(db_isolated):
    """The failed-turn rollback path: ``del history[snapshot:]`` then save."""
    s = SessionStore()
    _seed(s, "sess-short")
    s.set_transcript("sess-short", [{"role": "user", "content": f"m{i}"} for i in range(6)])
    s.set_transcript("sess-short", [{"role": "user", "content": f"m{i}"} for i in range(2)])
    got = s.get_transcript("sess-short")
    assert [m["content"] for m in got] == ["m0", "m1"]
    assert (s.get_meta("sess-short") or {})["message_count"] == 2


def test_delete_removes_the_messages_too(db_isolated):
    s = SessionStore()
    _seed(s, "sess-del")
    s.set_transcript("sess-del", [{"role": "user", "content": "x"}])
    s.delete("sess-del")
    assert s.get_transcript("sess-del") is None
    assert s.get_messages("sess-del") == [], "messages must not outlive their session"


def test_extra_keys_on_a_message_survive_the_round_trip(db_isolated):
    """A transcript entry is NOT just role/content/ts. The skill host rides its own keys on
    it, and revision 0051 dropped them — 19 real skill-activation receipts on the owner's
    instance — because the schema promoted three fields and discarded the rest."""
    s = SessionStore()
    entry = {
        "role": "assistant",
        "content": "loaded",
        "ts": 99,
        "_onw_skill": {"id": "sk_1", "name": "tax-audit"},
        "_onw_skill_activity_id": "skill:0:user:tax-audit",
    }
    s.set_transcript("sess-extra", [entry])
    assert s.get_transcript("sess-extra") == [entry]  # entry carries its own ts


def test_a_change_to_an_extra_key_alone_is_persisted(db_isolated):
    """The append-only diff compares whole dicts — a change confined to a carried key must
    still re-persist that row."""
    s = SessionStore()
    a = {"role": "assistant", "content": "x", "ts": 1, "_onw_skill_activity_id": "before"}
    s.set_transcript("sess-extra2", [a])
    b = {"role": "assistant", "content": "x", "ts": 1, "_onw_skill_activity_id": "after"}
    s.set_transcript("sess-extra2", [b])
    assert s.get_transcript("sess-extra2") == [b]
