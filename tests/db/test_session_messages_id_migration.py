"""The ``session_messages`` rebuild chain (0053-0056), driven end to end from a real 0052
database. The final shape is a database-assigned ``id INTEGER PRIMARY KEY`` that both
identifies and orders a message; ``seq`` is gone.

A rebuild-and-copy is the highest-risk migration shape there is — it drops the real table and
swaps a new one in — so this pins the three things that have ACTUALLY gone wrong on this
revision rather than restating its design:

  1. **Every row survives.** An earlier draft built the new table with a ``BigInteger``
     autoincrement primary key and copied via ``INSERT ... SELECT`` omitting ``id``. On SQLite
     only a column declared exactly ``INTEGER PRIMARY KEY`` (the rowid alias) auto-allocates;
     ``BigInteger`` renders ``BIGINT``, an ordinary NOT NULL column, so the copy died on its
     first row with ``NOT NULL constraint failed: session_messages_new.id``. It reproduced
     ONLY with rows present — an empty table copies zero rows and "passes" — which is why it
     reached a real book (140 messages, fail-closed boot gate, restored from backup) before
     anyone saw it. These tests therefore SEED ROWS FIRST.

  2. **Conversation order is preserved.** Ids no longer ascend (they are random hex), so
     ``id`` carries the order and must ascend in each conversation's original order.

  3. **``content``/``extra`` are not re-encoded.** The copy reads through
     ``exec_driver_sql``, which bypasses the typed JSON column and hands back the driver's
     raw TEXT. Passing that back through a ``sa.JSON`` column JSON-encodes an ALREADY-encoded
     string — every message becomes a JSON string wrapping JSON and every NULL ``extra``
     becomes the literal ``'null'``. Per the revision's own comment that is not hypothetical:
     an earlier run did it to 16,378 live messages.

Runs the real ``command.upgrade("202608241938_session_message_extra" -> "202608241942_drop_proactive_sidecar")`` on an isolated tmp-path DB, mirroring
``tests/db/test_derived_data_colocation_migration.py``'s shape.
"""

from __future__ import annotations

import pytest
import sqlalchemy as sa
from alembic import command
from sqlalchemy import create_engine, text

from backend.db.alembic_config import make_config
from tests.db.conftest import patch_all_cutover_sources_missing, sqlite_only

pytestmark = sqlite_only(
    "0053 rebuilds a populated 0052 table with qmark SQL over driver TEXT; a Postgres chain "
    "replays it on an empty table (every other test exercises that), no row ever existed at 0052"
)

# LOCAL, throwaway descriptions of the two tables AS THEY ACTUALLY ARE at the "202608241938_session_message_extra"
# checkpoint — `session_messages` keyed by (session_id, seq), with no `id` column at all.
# Same reasoning as revision 0008's own "LOCAL table descriptions, not backend.db.tables":
# the live `backend.db.tables.session_messages` reflects TODAY's post-0053 code and would
# insert into columns this checkpoint's schema does not have.
_sessions_pre53 = sa.table(
    "sessions",
    sa.column("id", sa.Text),
    sa.column("title", sa.Text),
    sa.column("created_at", sa.Text),
    sa.column("updated_at", sa.Text),
    sa.column("message_count", sa.Integer),
    sa.column("meta", sa.JSON),
)
_session_messages_pre53 = sa.table(
    "session_messages",
    sa.column("session_id", sa.Text),
    sa.column("seq", sa.Integer),
    sa.column("role", sa.Text),
    sa.column("content", sa.JSON),
    sa.column("ts", sa.Integer),
    sa.column("extra", sa.JSON),
)

# Two conversations, deliberately interleaved in INSERT order so "the old order" is a real
# claim and not an artefact of physical row order: session "b" is written before "a", and
# each session's own seqs are written out of order. `content` is a BLOCK LIST on one row —
# the shape that would visibly rot under a double JSON encode.
_SEEDED = [
    ("b", 1, "assistant", "b-one", None),
    ("a", 2, "user", "a-two", {"_onw_skill_activity_id": "act-1"}),
    ("b", 0, "user", "b-zero", None),
    ("a", 0, "user", "a-zero", None),
    ("a", 1, "assistant", [{"type": "text", "text": "a-one"}], None),
]


@pytest.fixture
def at_0052(db_url, monkeypatch, tmp_path):
    """A database migrated to 0052 — the revision immediately before the rebuild."""
    from backend.db import engine as eng

    if db_url.startswith("postgresql"):
        scrub = create_engine(db_url)
        with scrub.begin() as c:
            c.execute(text("DROP SCHEMA public CASCADE"))
            c.execute(text("CREATE SCHEMA public"))
        scrub.dispose()
    monkeypatch.setenv("ONW_DATABASE_URL", db_url)
    patch_all_cutover_sources_missing(monkeypatch, tmp_path)
    eng.dispose_engine()
    command.upgrade(make_config(db_url), "202608241938_session_message_extra")
    yield db_url
    eng.dispose_engine()


def _seed(url: str) -> None:
    engine = create_engine(url)
    with engine.begin() as c:
        for sid in ("a", "b"):
            c.execute(
                _sessions_pre53.insert().values(
                    id=sid,
                    title=f"session {sid}",
                    created_at="2026-08-24T00:00:00Z",
                    updated_at="2026-08-24T00:00:00Z",
                    message_count=0,
                    meta={},
                )
            )
        for sid, seq, role, body, extra in _SEEDED:
            c.execute(
                _session_messages_pre53.insert().values(
                    session_id=sid,
                    seq=seq,
                    role=role,
                    content=body,
                    ts=None,
                    extra=extra,
                )
            )
    engine.dispose()


def _read(url: str):
    """Read back through the TYPED columns — the same path the store uses."""
    from backend.db.tables import session_messages

    engine = create_engine(url)
    with engine.connect() as c:
        rows = c.execute(
            sa.select(
                session_messages.c.session_id,
                session_messages.c.id,
                session_messages.c.role,
                session_messages.c.content,
                session_messages.c.extra,
            ).order_by(session_messages.c.session_id, session_messages.c.id)
        ).fetchall()
    engine.dispose()
    return rows


def test_0053_copies_every_existing_message(at_0052):
    """The regression: with rows present, 0053 completes and loses nothing."""
    _seed(at_0052)

    command.upgrade(make_config(at_0052), "202608241942_drop_proactive_sidecar")

    rows = _read(at_0052)
    assert len(rows) == len(_SEEDED), "every seeded message must survive the rebuild"
    ids = [r.id for r in rows]
    assert all(i for i in ids), "every row must carry an id"
    assert len(set(ids)) == len(ids), "ids must be unique"


def test_0053_preserves_conversation_order(at_0052):
    """Ids ascend in each session's original order — no conversation is reordered."""
    _seed(at_0052)

    command.upgrade(make_config(at_0052), "202608241942_drop_proactive_sidecar")

    rows = _read(at_0052)
    by_session: dict[str, list] = {}
    for r in rows:
        by_session.setdefault(r.session_id, []).append(r)

    assert sorted(by_session) == ["a", "b"]
    for sid, msgs in by_session.items():
        assert [m.id for m in msgs] == sorted(m.id for m in msgs), (
            f"session {sid}: ids must ascend in conversation order"
        )


def test_0053_does_not_re_encode_json_payloads(at_0052):
    """`content`/`extra` come back as the ORIGINAL structures, not JSON-wrapped-in-JSON.

    The copy reads raw driver TEXT; re-inserting that through a typed JSON column would
    double-encode it. A string payload would still *look* fine on a shallow check, so this
    asserts on the block-list row and on a NULL `extra` — the two shapes that rot visibly
    (`'null'` the string is not None).
    """
    _seed(at_0052)

    command.upgrade(make_config(at_0052), "202608241942_drop_proactive_sidecar")

    # Keyed by POSITION within the session, not by id: `id` is a global autoincrement
    # ordering key now, not a per-session 0,1,2 index.
    by_session: dict[str, list] = {}
    for r in _read(at_0052):
        by_session.setdefault(r.session_id, []).append(r)
    got = {
        (sid, pos): (row.content, row.extra)
        for sid, rows in by_session.items()
        for pos, row in enumerate(rows)
    }

    content, extra = got[("a", 1)]
    assert content == [{"type": "text", "text": "a-one"}], (
        "a block list must survive as a list, not as a JSON string wrapping JSON"
    )
    assert extra is None, "a NULL extra must stay None, never the literal string 'null'"

    assert got[("a", 0)][0] == "a-zero"
    assert got[("a", 2)][1] == {"_onw_skill_activity_id": "act-1"}
    assert not isinstance(got[("a", 2)][1], str), "a carried extra dict must not be re-encoded"
