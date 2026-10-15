"""SkillStateStore — runtime state for the skill registry (``backend/db/skill_store.py``),
keyed by skill id: ``{enabled, author, created_at, updated_at, invocation_count}``.

WAVE 5C (JSON->DB): replaces the hand-rolled ``json.loads``/``atomic_write_text`` I/O in
``skill_store.py``'s ``_load_state``/``_save_state`` (``skills_state.json``). Dict-shaped —
mirrors ``ModelTelemetryStore``/``CostLogDailyStore``: id = the skill id, payload = the
state dict verbatim (no ``"id"`` key inside it). Subclasses ``PayloadTableStore`` for its
plumbing only (``_use_conn``/``_row``/``_fire_hook``/``_next_seq``/``_insert_row``), never
its generic domain methods (``insert``/``upsert``/``query``/``all`` all assume the id lives
INSIDE the payload dict, which is false here).

Checkpoint-INCLUDED + portable (durable per-skill config; the skill CONTENT itself — SKILL.md
+ scripts/references/assets — stays files under ``SKILLS_DIR``, untouched by this store) —
unlike ``model_telemetry``/``cost_log_daily``, which are checkpoint-EXCLUDED telemetry and
share this same dict-shaped plumbing but not this table's portable/checkpoint status.
"""

from __future__ import annotations

from sqlalchemy import insert as sa_insert
from sqlalchemy import select

from backend.db.payload_store import PayloadTableStore
from backend.db.tables import skills_state


class SkillStateStore(PayloadTableStore):
    def __init__(self, engine=None):
        # id_field="id" is documentation only — every method below is hand-rolled against
        # _row/_use_conn directly, mirroring ModelTelemetryStore's own comment.
        super().__init__(skills_state, id_field="id", engine=engine)

    def all_as_dict(self) -> dict[str, dict]:
        """Every skill's state row, keyed by skill id."""
        with self._eng().connect() as conn:
            return {r.id: r.payload for r in conn.execute(select(self._table))}

    def replace_all_keyed(self, state: dict[str, dict]) -> None:
        """Whole-dict replace, in ONE transaction — mirrors
        ``PayloadTableStore.replace_all``'s wipe-and-reinsert shape, keyed variant (each
        item's id is the DICT KEY, never a field inside its own payload). Matches
        ``skill_store._save_state``'s pre-migration whole-file-rewrite contract exactly:
        every existing row is captured as a before-image (even one whose key survives
        unchanged — the checkpoint hook doesn't need to diff, only capture), a key absent
        from the new ``state`` is dropped, seq is renumbered by ``state``'s own dict order
        (Python dicts preserve insertion order, matching the source JSON's key order)."""
        with self._use_conn(None) as conn:
            existing_map = {r.id: dict(r._mapping) for r in conn.execute(select(self._table))}
            hook_rows = list(existing_map.items())
            hook_rows += [(k, None) for k in state if k not in existing_map]
            self._fire_hook(conn, hook_rows)
            conn.execute(self._table.delete())
            if state:
                conn.execute(
                    sa_insert(self._table),
                    [self._insert_row(i, k, v) for i, (k, v) in enumerate(state.items())],
                )
