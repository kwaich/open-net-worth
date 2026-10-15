"""SqlQueryLogStore — the audit ring for Lucius's read-only SQL access (table
``sql_query_log``, revision 0050). Proves the append/read round trip, newest-first ordering,
the rolling cap, the SQL-text clip, and — the point of the store — that the statement itself
is retained verbatim rather than reduced to a length, which is where it deliberately parts
company with ``tool_call_log``.
"""

from __future__ import annotations

from sqlalchemy import inspect, select

from backend.db.sql_query_log_store import (
    MAX_SQL_CHARS,
    MAX_SQL_QUERIES,
    SqlQueryLogStore,
)
from backend.db.tables import sql_query_log


def _store(db_engine) -> SqlQueryLogStore:
    return SqlQueryLogStore(engine=db_engine)


# ── Table shape ──────────────────────────────────────────────────────────────────────


def test_sql_query_log_table_registered_and_shaped():
    from backend.db import tables as t

    assert "sql_query_log" in t.metadata.tables
    table = t.metadata.tables["sql_query_log"]
    assert {c.name for c in table.c} == {"seq", "id", "payload"}
    assert [c.name for c in table.primary_key.columns] == ["id"]
    # Tier-2: an operational record of what was asked of the database, never book-scoped.
    assert "book_id" not in {c.name for c in table.c}


def test_sql_query_log_schema_creates_on_both_dialects(db_engine):
    insp = inspect(db_engine)  # SQLite here, Postgres in CI
    assert insp.has_table("sql_query_log")


# ── Round trip ───────────────────────────────────────────────────────────────────────


def test_append_returns_the_coerced_row_and_persists_it(db_engine):
    store = _store(db_engine)
    row = store.append(
        {
            "sql": "SELECT 1",
            "ok": True,
            "row_count": 1,
            "elapsed_ms": 1.2345,
            "origin": "web",
            "session_id": "sess-1",
            "caveats": ["careful"],
        }
    )
    assert row["id"].startswith("sqlq")
    assert row["sql"] == "SELECT 1"
    assert row["ok"] is True
    assert row["row_count"] == 1
    assert row["elapsed_ms"] == 1.234 or row["elapsed_ms"] == 1.235  # rounded to 3dp
    assert row["origin"] == "web"
    assert row["caveats"] == ["careful"]
    assert row["error"] is None
    assert row["ts"]

    with db_engine.connect() as conn:
        stored = list(conn.execute(select(sql_query_log)))
    assert len(stored) == 1
    assert stored[0].payload["sql"] == "SELECT 1"


def test_the_statement_is_kept_verbatim_not_reduced_to_a_length(db_engine):
    """The whole reason this ring exists separately from ``tool_call_log``."""
    store = _store(db_engine)
    sql = "SELECT payee_id, COUNT(*) FROM transactions WHERE deleted = 0 GROUP BY payee_id"
    store.append({"sql": sql, "ok": True, "row_count": 12})
    assert store.recent()[0]["sql"] == sql


def test_a_refusal_is_logged_too_with_its_error(db_engine):
    store = _store(db_engine)
    store.append({"sql": "DROP TABLE accounts", "ok": False, "error": "read-only: DROP refused"})
    got = store.recent()[0]
    assert got["ok"] is False
    assert got["error"] == "read-only: DROP refused"
    assert got["row_count"] == 0


def test_recent_is_newest_first(db_engine):
    store = _store(db_engine)
    for i in range(5):
        store.append({"sql": f"SELECT {i}", "ok": True})
    assert [r["sql"] for r in store.recent()] == [f"SELECT {i}" for i in reversed(range(5))]


def test_recent_honours_its_limit(db_engine):
    store = _store(db_engine)
    for i in range(5):
        store.append({"sql": f"SELECT {i}", "ok": True})
    assert len(store.recent(limit=2)) == 2


# ── Bounds ───────────────────────────────────────────────────────────────────────────


def test_long_sql_is_clipped_with_a_visible_marker(db_engine):
    store = _store(db_engine)
    row = store.append({"sql": "SELECT " + ("x" * (MAX_SQL_CHARS * 2)), "ok": True})
    assert len(row["sql"]) == MAX_SQL_CHARS
    assert row["sql"].endswith("[truncated]")


def test_the_ring_caps_at_max_sql_queries(db_engine, monkeypatch):
    # Exercise the real eviction path against a small cap rather than writing 501 rows.
    monkeypatch.setattr("backend.db.sql_query_log_store.MAX_SQL_QUERIES", 3)
    store = _store(db_engine)
    for i in range(6):
        store.append({"sql": f"SELECT {i}", "ok": True})
    assert store.count() == 3
    # The OLDEST rows are the ones evicted.
    assert [r["sql"] for r in store.recent()] == ["SELECT 5", "SELECT 4", "SELECT 3"]


def test_the_real_cap_is_sized_for_recent_history():
    assert MAX_SQL_QUERIES == 500


# ── Policy wiring ────────────────────────────────────────────────────────────────────


def test_sql_query_log_is_checkpoint_excluded():
    """A /rewind undoes what Lucius did to your DATA; a read did nothing to it, and the
    record of what was asked is forensic."""
    from backend.ai.checkpoint._policy import _EXCLUDED_TABLES

    assert "sql_query_log" in _EXCLUDED_TABLES


def test_sql_query_log_is_neither_bridged_nor_wiped():
    """Mirrors ``tool_call_log`` exactly — a runtime log has no export or reset role."""
    from backend.db import portable_bridge as pb

    assert not any("sql_query_log" in key for key in pb.BRIDGED)
    assert not any("sql_query_log" in key for key in pb._WIPE_FNS)
