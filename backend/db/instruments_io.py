"""Instrument import/export between the JSON flat-list shape and the ``instruments``
payload table (``backend.db.tables.instruments``, built by ``payload_table("instruments")``).

The source file (``instruments.json``) is a FLAT LIST — ``InstrumentStore``'s pre-cutover
``JsonStore.__init__`` passed ``root_key=None``, so there is no derived/keyed wrapper to
unwrap (contrast ``evidence_io.py``'s per-row derived columns or ``price_history_io.py``'s
``{price_source_id: [...]}`` keyed shape). Import is therefore a straight seq-ordered
payload insert and export is its exact inverse — the payload is stored VERBATIM, like
``evidence.payload``. ``import_instruments`` / ``export_instruments`` are used by the
cutover revision (0005) AND by Task 7's portable-export bridge for DB-backed stores
(see .superpowers/sdd/2026-08-07-storage-plan2-reference-data-and-write-log/)."""

from __future__ import annotations

from sqlalchemy import Connection, insert, select

from backend.db.tables import instruments

_BATCH = 1000


def import_instruments(conn: Connection, raw: list[dict]) -> int:
    rows = [{"seq": seq, "id": item.get("id"), "payload": item} for seq, item in enumerate(raw)]
    for i in range(0, len(rows), _BATCH):
        conn.execute(insert(instruments), rows[i : i + _BATCH])
    return len(rows)


def export_instruments(conn: Connection) -> list[dict]:
    return [r.payload for r in conn.execute(select(instruments).order_by(instruments.c.seq))]
