"""CheckpointIndexStore — the turn-checkpoint manifest (spec D7), on PayloadTableStore
(plan 4e, task 2).

Replaces the old ``CHECKPOINTS_INDEX`` JSON file (``DATA_DIR/checkpoints/index.json``) —
the flat list of checkpoint entries ``ai.checkpoint`` used to read/write wholesale on every
mutation. ``checkpointed=False`` at construction (mirrors ``PendingPushStore``'s precedent:
a construction-time opt-out of ever firing the write-log hook — the table is already in
``ai.checkpoint._EXCLUDED_TABLES``, which would make the capture a structural no-op anyway;
skipping the call is cheaper than a guaranteed no-op for a table that writes on every turn).
A checkpoint table must never checkpoint itself — that would recurse.

``payload`` is the WHOLE existing entry dict verbatim (``id``/``session_id``/
``anchor_history_len``/``created_at``/``message_preview``/``files``/``status``/``kind``/
``db_tables``/``receipt``) — NO promoted columns. ``ai.checkpoint.MAX_CHECKPOINTS`` (50)
active checkpoints plus a bounded pinned/orphaned tail keeps this table structurally tiny
forever (hard-capped, unlike every other Tier-2 store this program has migrated), so
``PayloadTableStore.query()``'s Python-side filtering over ``all()`` is the right call for
this table, not a shortcut promoted columns exist to avoid.

All the domain logic (list/count/find/prune/orphan/GC) stays in ``ai.checkpoint._index`` as
free functions, exactly as it did against the old JSON file — this class is deliberately a
thin, bare CRUD wrapper (no ``add``/``due``-style convenience methods, unlike
``PendingPushStore``), matching the ``db/`` layer's "repositories = data access only" rule.
"""

from __future__ import annotations

from backend.db.payload_store import PayloadTableStore
from backend.db.tables import checkpoints


class CheckpointIndexStore(PayloadTableStore):
    def __init__(self, engine=None):
        super().__init__(checkpoints, id_field="id", engine=engine, checkpointed=False)
