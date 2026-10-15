"""SqlQueryLogStore — the audit ring for Lucius's read-only SQL access (one row per query).

Every ``run_sql_query`` dispatch lands one row here, success or refusal, so the owner can
answer "what has it actually been asking my database?" from Settings > Developer rather than by
scrolling chat transcripts.

**THIS RING STORES THE SQL TEXT VERBATIM** — a deliberate departure from its closest sibling,
``ToolCallLogStore``, whose docstring pins "SIZES AND SHAPES ONLY — never raw arguments". The
distinction is not a relaxation of that rule; it is a different question. For a telemetry ring
sized to answer "which tools earn their prompt tokens", the arguments are incidental and
storing them would turn a high-volume log into a financial-record spill. Here the statement IS
the auditable action: a log saying "a 412-character query ran and returned 9 rows" tells the
owner nothing about whether the capability is behaving. ``McpWriteLogStore`` made the same call
for the same reason — it keeps the real call, not its length.

What follows from that: the SQL is capped at ``MAX_SQL_CHARS`` (a runaway generated statement
must not be able to bloat the ring), and the only reader is an auth-gated endpoint that sets
``Cache-Control: no-store``. Nothing under ``ai/`` reads this table back into a model context.

Tier-2 (table ``sql_query_log``, revision 0050, NO ``book_id``), checkpoint-EXCLUDED
(``ai.checkpoint._policy._EXCLUDED_TABLES``), and — mirroring ``tool_call_log`` exactly —
NEITHER a ``db/portable_bridge.py`` BRIDGED entry NOR a ``_WIPE_FNS`` one: a runtime log has no
export or reset role to preserve.

Row schema:
    id          : str
    ts          : ISO8601 UTC
    sql         : str          the statement AS SUBMITTED, capped at MAX_SQL_CHARS
    ok          : bool         did it execute
    error       : str | None   the refusal/failure message shown to the model; None when ok
    row_count   : int          rows actually returned (0 on refusal)
    truncated   : bool         did the row cap bite
    elapsed_ms  : float        wall time inside the executor
    origin      : str          "web" | "connector:<name>" | "scheduled" | "mcp" | "unknown"
    session_id  : str | None   chat session correlation id
    caveats     : list[str]    the caveats attached to this result
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import delete as sa_delete
from sqlalchemy import func, select
from sqlalchemy import insert as sa_insert

from backend.core.ids import new_id
from backend.db.payload_store import PayloadTableStore
from backend.db.tables import sql_query_log

# Rolling cap. A query row is dominated by its SQL text, so at the 4,000-char ceiling below the
# worst case is ~2 MB for a full ring — the same order as the other capped rings here, and the
# realistic average is a small fraction of that. 500 rows is sized off the question this log
# must answer ("what has it been asking, lately?"), which is a recent-history question, not the
# multi-week trend analysis ``tool_call_log``'s 5,000 exists for.
MAX_SQL_QUERIES = 500

# A single statement long enough to hit this is already pathological. Truncating keeps one
# runaway generated query from evicting the rest of the ring.
MAX_SQL_CHARS = 4000

_TRUNCATION_MARKER = "… [truncated]"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _clip(sql: str) -> str:
    if len(sql) <= MAX_SQL_CHARS:
        return sql
    return sql[: MAX_SQL_CHARS - len(_TRUNCATION_MARKER)] + _TRUNCATION_MARKER


def _make_row(data: dict) -> dict:
    """Coerce a recorder payload into the stored shape. Dumb + total — the store never
    inspects the SQL's semantics and never decides whether a query should have run."""
    return {
        "id": data.get("id") or new_id("sqlq"),
        "ts": data.get("ts") or _now(),
        "sql": _clip(str(data.get("sql") or "")),
        "ok": bool(data.get("ok", False)),
        "error": data.get("error"),
        "row_count": int(data.get("row_count", 0)),
        "truncated": bool(data.get("truncated", False)),
        "elapsed_ms": round(float(data.get("elapsed_ms", 0.0)), 3),
        "origin": data.get("origin") or "unknown",
        "session_id": data.get("session_id"),
        "caveats": list(data.get("caveats") or []),
    }


class SqlQueryLogStore(PayloadTableStore):
    def __init__(self, engine=None):
        super().__init__(sql_query_log, id_field="id", engine=engine)

    def recent(self, limit: int = 50) -> list[dict]:
        """The newest ``limit`` rows, newest first."""
        with self._eng().connect() as conn:
            rows = conn.execute(
                select(self._table).order_by(self._table.c.seq.desc()).limit(max(0, limit))
            )
            return [r.payload for r in rows]

    def count(self) -> int:
        with self._eng().connect() as conn:
            return int(conn.execute(select(func.count()).select_from(self._table)).scalar_one())

    def append(self, data: dict) -> dict:
        """Append one query record, capping the ring at ``MAX_SQL_QUERIES``.

        Holds this table's lock for the full read-modify-write, mirroring
        ``ToolCallLogStore.append_many`` / ``McpWriteLogStore.append``. Unbatched by
        contract: a SQL query is a human-paced, low-volume event (unlike a tool dispatch,
        which fires several times per turn), so the buffering machinery that log needs
        would be complexity with nothing to buy.
        """
        row = _make_row(data)
        with self._use_conn(None) as conn:
            self._fire_hook(conn, [(row["id"], None)])
            seq = self._next_seq(conn)
            conn.execute(sa_insert(self._table), [self._insert_row(seq, row["id"], row)])
            over = (
                conn.execute(select(func.count()).select_from(self._table)).scalar_one()
                - MAX_SQL_QUERIES
            )
            if over > 0:
                stale = [
                    r.id
                    for r in conn.execute(
                        select(self._table.c.id).order_by(self._table.c.seq).limit(over)
                    )
                ]
                conn.execute(sa_delete(self._table).where(self._table.c.id.in_(stale)))
        return row
