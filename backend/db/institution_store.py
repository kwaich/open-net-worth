"""Institution repository — shared, book-scoped account identity."""

from __future__ import annotations

from typing import Any

from sqlalchemy import update as sa_update

from backend.core.ids import new_id
from backend.db.payload_store import BookPayloadTableStore
from backend.db.tables import institutions


def normalize_institution_name(value: object) -> str:
    """Stable comparison key: trim, collapse whitespace, and case-fold."""
    return " ".join(str(value or "").split()).casefold()


class InstitutionStore(BookPayloadTableStore):
    def __init__(self, engine=None):
        super().__init__(institutions, id_field="id", engine=engine)

    def _insert_row(self, seq: int, id_val: Any, item: dict) -> dict:
        row = super()._insert_row(seq, id_val, item)
        row["normalized_name"] = normalize_institution_name(item.get("name"))
        return row

    def find_by_normalized_name(self, normalized_name: str, *, conn=None) -> dict | None:
        target = normalize_institution_name(normalized_name)
        if not target:
            return None
        for row in self.all(conn=conn):
            if normalize_institution_name(row.get("name")) == target:
                return row
        return None

    def update_by_id(self, id_val: str, changes: dict, *, conn=None) -> dict | None:
        """Keep the indexed comparison key in step with a renamed display name."""
        with self._use_conn(conn) as conn:
            existing = self._row(conn, id_val)
            if existing is None:
                return None
            merged = {**existing["payload"], **changes}
            self._fire_hook(conn, [(id_val, existing)])
            conn.execute(
                sa_update(self._table)
                .where(self._table.c.id == id_val)
                .values(
                    payload=merged,
                    normalized_name=normalize_institution_name(merged.get("name")),
                )
            )
        return merged

    def resolve_or_create(self, name: object, *, conn=None) -> dict | None:
        display_name = " ".join(str(name or "").split())
        if not display_name:
            return None
        existing = self.find_by_normalized_name(display_name, conn=conn)
        if existing is not None:
            return existing
        institution = {"id": new_id("inst"), "name": display_name}
        return self.insert(institution, conn=conn)

    def names_by_id(self, *, conn=None) -> dict[str, str]:
        return {row["id"]: row["name"] for row in self.all(conn=conn)}
