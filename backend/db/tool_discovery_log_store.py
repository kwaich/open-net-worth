"""ToolDiscoveryLogStore — find_tools search-quality telemetry (tool-discovery RFC
follow-up).

A capped ring (oldest first, bounded to ``MAX_EVENTS``) of ``find_tools(query=…)`` call
events, so "is the lexical search good enough?" can be answered empirically instead of
by guess — see ``ai/tools/discovery_log.py`` for the classification logic
(miss/core_only/ok) and the two readers (``recent``/``miss_rate``). Runtime/behavioral
telemetry → checkpoint-EXCLUDED (``ai/checkpoint._EXCLUDED_TABLES``; mirrors
``cost_log`` — a Lucius restore must not rewrite the record of what was searched).

WAVE 5B (JSON->DB): replaces the hand-rolled ``json.loads``/``atomic_write_text`` I/O in
``ai/tools/discovery_log.py``. Events have NO natural id field (that module never used a
``JsonStore``), so ``append`` mints one fresh per row via ``new_id`` — the id lives ONLY
in the row's ``id`` column, never mixed into ``payload`` (matches
``logs_io.import_payload_list_minted``'s cutover-side precedent, so a historical row
imported by revision 0023 and a row appended live afterward carry the exact same payload
shape). No external write lock needed — ``append`` does its whole insert-then-evict cycle
inside ONE transaction via ``PayloadTableStore._use_conn``, exactly like
``HarnessSuggestionStore.append``.
"""

from __future__ import annotations

from sqlalchemy import delete as sa_delete
from sqlalchemy import func, select
from sqlalchemy import insert as sa_insert

from backend.core.ids import new_id
from backend.db.payload_store import PayloadTableStore
from backend.db.tables import tool_discovery_log

# Events are tiny (~120 bytes); 1000 covers a long debugging window (mirrors the
# pre-migration file ring's own _MAX_EVENTS exactly).
MAX_EVENTS = 1000


class ToolDiscoveryLogStore(PayloadTableStore):
    def __init__(self, engine=None):
        super().__init__(tool_discovery_log, id_field="id", engine=engine)

    def append(self, event: dict) -> dict:
        """Append one telemetry event, capping at MAX_EVENTS (oldest, lowest-seq,
        dropped) — insert + cap-evict inside ONE transaction. ``event`` is stored
        VERBATIM as payload; no id is ever mixed into it."""
        with self._use_conn(None) as conn:
            row_id = new_id("tdisc")
            self._fire_hook(conn, [(row_id, None)])
            seq = self._next_seq(conn)
            conn.execute(sa_insert(self._table).values(**self._insert_row(seq, row_id, event)))
            over = (
                conn.execute(select(func.count()).select_from(self._table)).scalar_one()
                - MAX_EVENTS
            )
            if over > 0:
                stale = [
                    r.id
                    for r in conn.execute(
                        select(self._table.c.id).order_by(self._table.c.seq).limit(over)
                    )
                ]
                conn.execute(sa_delete(self._table).where(self._table.c.id.in_(stale)))
        return event

    def recent(self, limit: int = 50) -> list[dict]:
        """The most recent ``limit`` events, oldest-of-the-window first (matches the
        pre-migration file's ``events[-limit:]`` — the tail of an oldest-first list)."""
        with self._eng().connect() as conn:
            rows = conn.execute(
                select(self._table).order_by(self._table.c.seq.desc()).limit(max(0, limit))
            )
            return [r.payload for r in rows][::-1]
