"""Evidence import/export between the JSON file shape and the DB tables.

payload is VERBATIM source of truth; expires_at/domain/subjects are derived
indexes recomputed on every write; seq preserves file insertion order (assigned
explicitly — portable across dialects). export_evidence() is the round-trip
verifier for the cutover revision AND the portable-export seam (spec D6 step 4)."""

from __future__ import annotations

from sqlalchemy import Connection, insert, select

from backend.db.tables import evidence, evidence_subjects

_BATCH = 1000


def derive_columns(row: dict) -> dict:
    return {
        "id": row.get("id"),
        "expires_at": row.get("expires_at") or "",
        "domain": row.get("domain") or "",
        "created_at": row.get("first_seen") or "",
        "payload": row,
    }


def subject_rows(row: dict) -> list[dict]:
    return [
        {"evidence_id": row.get("id"), "subject": s} for s in sorted(set(row.get("subjects") or []))
    ]


def import_evidence_json(conn: Connection, rows: list[dict]) -> int:
    ev_rows, sub_rows = [], []
    for seq, row in enumerate(rows):
        ev_rows.append({**derive_columns(row), "seq": seq})
        sub_rows.extend(subject_rows(row))
    for i in range(0, len(ev_rows), _BATCH):
        conn.execute(insert(evidence), ev_rows[i : i + _BATCH])
    for i in range(0, len(sub_rows), _BATCH):
        conn.execute(insert(evidence_subjects), sub_rows[i : i + _BATCH])
    return len(ev_rows)


def export_evidence(conn: Connection) -> list[dict]:
    return [r.payload for r in conn.execute(select(evidence).order_by(evidence.c.seq))]
