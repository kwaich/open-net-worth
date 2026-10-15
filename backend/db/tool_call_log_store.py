"""ToolCallLogStore — the per-tool-call TELEMETRY ring (one row per dispatch).

Every tool call, from every surface, funnels through ``ai/tools/registry.dispatch`` and
lands one row here. This is the instrument behind decisions that were previously made from
judgement: which of the ~19 always-offered ``CORE_TOOL_NAMES`` actually earn their ~6,900
prompt tokens, which tools return results large enough to poison the rest of a session,
how many tool calls a turn really takes, and which ``find_tools`` queries were followed by
a call the query never returned.

**SIZES AND SHAPES ONLY — never raw arguments or raw results.** This app holds real
financial records, and this log is high-volume, long-lived, and read by scripts. It stores
``input_chars``/``result_chars``, an ``error_class``, and the ``mutates`` domains actually
declared — no argument values, no result bodies, not even an error *message* (assistant.py
already logs those to ``app.log``, which rotates; this ring does not). The only free-text
fields are registry-derived (tool name, alias) or a small closed vocabulary (origin,
error class). See ``ai/tools/call_log.py`` for the recorder that enforces this.

Relationship to the other trails (deliberately NOT merged — see
``docs/adr/2026-07-19-per-tool-call-telemetry.md``):
  * ``mcp_write_log`` — a MUTATING-MCP-call AUDIT with money amounts and a 500-row
    retention guarantee. This log SUPERSETS its non-money fields, but merging would let
    ~100x-higher-volume read telemetry evict the money-write audit trail within minutes.
  * ``run_history.json`` (via ``core.run_steps``) — a per-agent-run NARRATIVE for a human
    reviewing one run. Different consumer, different retention, keeps result snippets.
  * ``cost_log.json`` — per-API-call token/cost. Complementary: it counts model
    round-trips, this counts tool calls. Joined by session id at query time.
  * ``tool_discovery_log.json`` — ``find_tools`` queries and their hits. This log supplies
    the missing half (what was called AFTER a query), joined by timestamp.

A capped ring, newest first, exactly like ``McpWriteLogStore``. Runtime/behavioral telemetry
→ checkpoint-EXCLUDED (``ai/checkpoint._EXCLUDED_TABLES``; rows are written INSIDE the
CheckpointContext a chat turn opens, so the exclusion must be explicit) and deliberately
absent from ``core/portable.py``'s ``PORTABLE_FILES`` allowlist — it is instrument output,
not user data.

Two row KINDS share the ring, told apart by ``event`` so no query double-counts:
``event="call"`` (the default — one completed dispatch) and ``event="discovery_gate"`` (the
interactive gate auto-loading a tool the model called before loading it). A gate row fills
``gate_outcome``/``gate_args_valid`` and leaves the dispatch-only fields at their zero
values; an ``executed`` gate row is FOLLOWED by a real ``call`` row for the same call.

Row schema:
    id            : str
    ts            : ISO8601 UTC
    event         : str          "call" | "discovery_gate"
    tool          : str          CANONICAL name (aliases resolved)
    alias_used    : str | None   the former name the caller actually used, if any
    origin        : str          "web" | "connector:<name>" | "agent:<id>" | "scheduled"
                                 | "mcp" | "skill[:<id>]" | "import" | "unknown"
    session_id    : str | None   chat session / agent run correlation id
    turn_index    : int          ordinal of the turn within the session (-1 = no turn)
    call_index    : int          ordinal of this call within the turn (0-based)
    mutated       : bool         did this call declare any write domains
    domains       : list[str]    the ``mutates`` domains ACTUALLY declared for this call
    ok            : bool         did it succeed
    error_class   : str | None   "validation" | "unknown_tool" | "tool_error"
                                 | "exception:<ExcName>"; None when ok
    duration_ms   : float        handler wall time
    result_chars  : int          size of the text fed back into model context
    input_chars   : int          size of the serialized arguments
    checkpoint_id : str | None   the checkpoint a write is undoable from
    gate_outcome  : str | None   discovery_gate rows only: "executed"
                                 | "executed_external" | "bounced_invalid_args" | "bounced_write"
                                 | "bounced_external"
    gate_args_valid : bool|None  did the model's pre-schema arguments validate
                                 (None = never checked)

PLAN 4C TASK 1: DB-backed (Tier-2 logs, table ``tool_call_log``) via ``PayloadTableStore``.
``recent``/``all_rows`` read via an explicit ``ORDER BY seq DESC`` (Design Note 2, plan 4c).
The ORIGINAL's own ``fresh.reverse()`` in ``append_many`` is DROPPED — it was only needed to
splice a whole batch into a list-prepend; seq-ascending insertion + descending read makes it
unnecessary.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import delete as sa_delete
from sqlalchemy import func, select
from sqlalchemy import insert as sa_insert

from backend.core.ids import new_id
from backend.db.payload_store import PayloadTableStore
from backend.db.tables import tool_call_log

# Rolling cap. Rows are ~230 bytes, so 5,000 rows ≈ 1.2 MB on disk — the same order as
# the other capped rings here. Sized off the questions it must answer rather than off the
# file: at the measured ~4 tool calls/turn this covers ~1,200 turns, i.e. weeks of real
# use, which is the window "which CORE tool was never called over the last N days" needs
# to be trustworthy. (``MAX_WRITE_LOG = 500`` is right for its log — a few money writes a
# week — and far too short here; that mismatch is exactly why the two stores stayed
# separate.) Enforced on every append, so the table can never grow without bound.
MAX_TOOL_CALLS = 5000


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _make_row(data: dict) -> dict:
    """Coerce a recorder payload into the stored shape. Dumb + total — the store never
    inspects semantics, and never sees an argument value to begin with."""
    return {
        "id": data.get("id") or new_id("tcall"),
        "ts": data.get("ts") or _now(),
        "event": data.get("event") or "call",
        "tool": data.get("tool", ""),
        "alias_used": data.get("alias_used"),
        "origin": data.get("origin") or "unknown",
        "session_id": data.get("session_id"),
        "turn_index": int(data.get("turn_index", -1)),
        "call_index": int(data.get("call_index", 0)),
        "mutated": bool(data.get("mutated", False)),
        "domains": list(data.get("domains") or []),
        "ok": bool(data.get("ok", False)),
        "error_class": data.get("error_class"),
        "duration_ms": round(float(data.get("duration_ms", 0.0)), 3),
        "result_chars": int(data.get("result_chars", 0)),
        "input_chars": int(data.get("input_chars", 0)),
        "checkpoint_id": data.get("checkpoint_id"),
        "gate_outcome": data.get("gate_outcome"),
        "gate_args_valid": data.get("gate_args_valid"),
    }


class ToolCallLogStore(PayloadTableStore):
    def __init__(self, engine=None):
        super().__init__(tool_call_log, id_field="id", engine=engine)

    def recent(self, limit: int = 200) -> list[dict]:
        """The newest ``limit`` rows, newest first."""
        with self._eng().connect() as conn:
            rows = conn.execute(
                select(self._table).order_by(self._table.c.seq.desc()).limit(max(0, limit))
            )
            return [r.payload for r in rows]

    def all_rows(self) -> list[dict]:
        """Every retained row, newest first. The query surface reads the whole ring."""
        with self._eng().connect() as conn:
            rows = conn.execute(select(self._table).order_by(self._table.c.seq.desc()))
            return [r.payload for r in rows]

    def append_many(self, rows: list[dict]) -> int:
        """Append a BATCH of telemetry rows, capping at ``MAX_TOOL_CALLS``.

        Batched by contract: the recorder buffers in memory and flushes on a background
        thread, so one write covers many calls instead of one read-modify-write per
        tool call on the dispatch hot path. ``rows`` arrives oldest-first (append order)
        and is inserted in that same order at ascending seq — the descending read
        (``recent``/``all_rows``) is what makes the ring newest-first, not insertion order.

        Holds this table's lock for the full read-modify-write, mirroring
        ``McpWriteLogStore.append``. Returns the number of rows written.
        """
        if not rows:
            return 0
        fresh = [_make_row(data) for data in rows]  # oldest-first, AS GIVEN — no reversal needed
        with self._use_conn(None) as conn:
            self._fire_hook(conn, [(r["id"], None) for r in fresh])
            seq0 = self._next_seq(conn)
            conn.execute(
                sa_insert(self._table),
                [self._insert_row(seq0 + i, r["id"], r) for i, r in enumerate(fresh)],
            )
            over = (
                conn.execute(select(func.count()).select_from(self._table)).scalar_one()
                - MAX_TOOL_CALLS
            )
            if over > 0:
                stale = [
                    r.id
                    for r in conn.execute(
                        select(self._table.c.id).order_by(self._table.c.seq).limit(over)
                    )
                ]
                conn.execute(sa_delete(self._table).where(self._table.c.id.in_(stale)))
        return len(rows)

    def append(self, data: dict) -> dict:
        """Single-row convenience (tests, one-off callers). Prefer ``append_many``."""
        row = _make_row(data)
        self.append_many([row])
        return row
