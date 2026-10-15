"""Portable import/export for the book-scoped institution registry."""

from __future__ import annotations

import hashlib

from sqlalchemy import Connection, insert, select

from backend.core.errors import ValidationError
from backend.core.io import fire_db_write_hook
from backend.db.institution_store import normalize_institution_name
from backend.db.tables import DEFAULT_BOOK_ID, institutions


def export_institutions(conn: Connection) -> list[dict]:
    return [row.payload for row in conn.execute(select(institutions).order_by(institutions.c.seq))]


def import_institutions(conn: Connection, raw: list[dict]) -> int:
    rows: list[dict] = []
    for seq, payload in enumerate(raw):
        name = payload.get("name")
        if not isinstance(name, str) or not normalize_institution_name(name):
            raise ValueError(f"institution {payload.get('id')!r} needs a non-empty name")
        rows.append(
            {
                "id": payload.get("id"),
                "book_id": DEFAULT_BOOK_ID,
                "seq": seq,
                "normalized_name": normalize_institution_name(name),
                "payload": payload,
            }
        )
    if rows:
        fire_db_write_hook(institutions.name, [(row["id"], None) for row in rows], conn=conn)
        conn.execute(insert(institutions), rows)
    return len(rows)


def normalize_account_institutions(
    account_rows: list[dict] | None, institution_rows: list[dict] | None
) -> tuple[list[dict] | None, list[dict] | None]:
    """Upgrade a pre-registry portable bundle before its account rows are written.

    Pre-migration exports stored a free-text ``platform`` beside every account. A restore
    must keep working after the runtime has stopped accepting that field, so this makes
    canonical institution rows and replaces the legacy field with their stable ids.
    """
    if account_rows is None:
        return account_rows, institution_rows
    rows = [dict(row) for row in account_rows]
    institutions_out = [dict(row) for row in institution_rows or []]
    ids_by_normalized: dict[str, str] = {}
    for row in institutions_out:
        name = row.get("name")
        norm = normalize_institution_name(name) if isinstance(name, str) else ""
        institution_id = row.get("id")
        if not norm or not isinstance(institution_id, str) or not institution_id:
            raise ValidationError("institutions.json contains an institution without id and name")
        if norm in ids_by_normalized and ids_by_normalized[norm] != institution_id:
            raise ValidationError(f"institutions.json duplicates institution {name!r}")
        ids_by_normalized[norm] = institution_id

    for row in rows:
        legacy = row.pop("platform", None)
        if legacy in (None, ""):
            continue
        if not isinstance(legacy, str):
            raise ValidationError(f"account {row.get('id')!r} has an invalid platform")
        norm = normalize_institution_name(legacy)
        if not norm:
            continue
        institution_id = ids_by_normalized.get(norm)
        if institution_id is None:
            institution_id = f"inst_{hashlib.sha256(norm.encode()).hexdigest()[:12]}"
            ids_by_normalized[norm] = institution_id
            institutions_out.append({"id": institution_id, "name": " ".join(legacy.split())})
        existing_id = row.get("institution_id")
        if existing_id not in (None, "", institution_id):
            raise ValidationError(
                f"account {row.get('id')!r} has conflicting platform and institution_id"
            )
        row["institution_id"] = institution_id

    known_ids = {row["id"] for row in institutions_out}
    for row in rows:
        institution_id = row.get("institution_id")
        if institution_id in (None, ""):
            continue
        if not isinstance(institution_id, str) or institution_id not in known_ids:
            raise ValidationError(
                f"account {row.get('id')!r} refers to missing institution {institution_id!r}"
            )
    return rows, institutions_out
