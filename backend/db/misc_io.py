"""Misc-store import/export between the JSON file shapes and the 4 Wave-E Tier-2 tables
(``artifacts``, ``uploads``, ``email_snapshots``, ``pending_pushes`` — built by revision
0017) — the plan-4e task-1 absorb, revision 0018. ``checkpoints`` (the 5th Wave-E table,
also built by 0017) has no io functions here — it is schema-only this task; its absorb
is a separate, dedicated revision 0019 (plan 4e task 2), per that task's own binding.
``uploads`` was renamed ``attachments`` at revision 0041 (cross-channel-attachments RFC
Phase 2) — this module's generic ``import_payload_list``/``export_payload_list`` are
table-parametric and untouched by the rename; only the live ``attachments`` import below
and ``MISC_PAYLOAD_SPECS`` track the new name, current-state.

Mirrors ``logs_io.py``'s generic pair exactly: NO ``book_id`` anywhere in this wave —
none of the four misc stores is book/money-scoped (session-scoped chat artifacts, a
user's own attachment catalog, global email provenance, a global runtime retry queue).
Self-contained, not importing another domain's io module — matches Wave B's own explicit
choice not to import ``planning_io``; each wave keeps its io module standalone.

All four sources are flat lists, already in PLAIN APPEND order on disk — none is a
prepend/ring source (``ArtifactStore.create``/``AttachmentStore.create``/
``EmailSnapshotStore.stash``/``PendingPushStore.add`` all insert via the base class's
own append-only ``insert()``, never ``items.insert(0, ...)``), so every call below keeps
the plain ascending-seq shape — no ``reverse`` flag, unlike ``logs_io``/``config_io``,
each of which carries one real prepend-order exception. The cutover revision's round-trip
check (``export == source``) is still ORDER-SENSITIVE regardless (plain ``==`` on a
parsed JSON list is positional, not a set/count comparison) — a wrong assumption about
append-vs-prepend order would still fail loud here, not silently pass on a same-shape
fixture.

NO MONEY GUARD: none of the four carries a net-worth-bearing amount — every field stays
payload-verbatim like every other non-promoted field, mirroring ``logs_io``'s identical
rule.
"""

from __future__ import annotations

from sqlalchemy import Connection, Table, insert, select

from backend.core.io import fire_db_write_hook
from backend.db.tables import artifacts, attachments, email_snapshots, pending_pushes

_BATCH = 1000


def import_payload_list(
    conn: Connection,
    table: Table,
    raw: list[dict],
    *,
    id_field: str = "id",
) -> int:
    """Absorb a flat JSON list into ``table``, one row per element with an ascending
    ``seq`` (0-based, file order) — every one of the four Wave-E sources is already
    append-order on disk (see module docstring), so no ``reverse`` flag is needed."""
    rows = [{"seq": seq, "id": row.get(id_field), "payload": row} for seq, row in enumerate(raw)]
    if rows:
        fire_db_write_hook(table.name, [(r["id"], None) for r in rows], conn=conn)
    for i in range(0, len(rows), _BATCH):
        conn.execute(insert(table), rows[i : i + _BATCH])
    return len(rows)


def export_payload_list(conn: Connection, table: Table) -> list[dict]:
    return [r.payload for r in conn.execute(select(table).order_by(table.c.seq))]


# ── Filename/table/id_field specs — future-facing scaffolding (mirrors
# LOGS_PAYLOAD_SPECS/CONFIG_PAYLOAD_SPECS: neither the cutover revision nor
# portable_bridge.py actually iterates these — both reference backend.db.tables
# directly, matching every prior wave); test-pinned cross-check that this module's
# coverage matches the wave's real scope. ──────────────────────────────────────────

MISC_PAYLOAD_SPECS: list[tuple[str, Table, str]] = [
    ("artifacts.json", artifacts, "id"),
    ("attachments.json", attachments, "id"),  # was "uploads.json"/uploads — revision 0041
    ("email_snapshots.json", email_snapshots, "message_id"),
    ("pending_pushes.json", pending_pushes, "id"),
]
MISC_FILES: tuple[str, ...] = tuple(fn for fn, *_rest in MISC_PAYLOAD_SPECS)
