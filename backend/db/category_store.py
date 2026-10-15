"""CategoryStore — per-instance category registry.

Seeded once on first boot from STARTER_CATEGORIES (including the ``split`` sentinel);
afterwards fully user-owned. Each row:
    {id, label, kind, color, hidden?}
The ``id_field`` is ``id`` — an OPAQUE id (``cat_<12hex>``, minted via ``core.ids.new_id``;
built-in seeds carry a stable hardcoded id instead). ``label`` is the only human-readable
field; nothing derives a slug/key from it (category-opaque-ids migration). The one
structural exception is the ``split`` sentinel row, whose id is the literal string
``"split"`` — not a minted id (it is a marker, never a real category; see
``services.categories.STRUCTURAL_KEYS``).

After the P1 read-flip, classification queries (kind) and enumeration
(expense set, all categories) read from this store instead of the frozen code list,
so a user-deleted built-in is genuinely gone from every classification path
(RFC categories-as-data.md P0–P1).

PLAN 4A TASK 2: DB-backed (Tier-1 planning, table ``categories`` — backend/db/tables.py)
via ``BookPayloadTableStore`` — read-only methods below are unchanged, over ``self.all()``.
Seeding (backend/main.py) goes through this store's own ``bulk_insert``, so it is
transparently DB-backed too; nothing about the seed call site changed.
"""

from __future__ import annotations

from backend.db.payload_store import BookPayloadTableStore
from backend.db.tables import categories


class CategoryStore(BookPayloadTableStore):
    def __init__(self, engine=None):
        super().__init__(categories, id_field="id", engine=engine)

    def get(self, key: str) -> dict | None:
        """Return the entry for a category id, or None if absent."""
        return self.find_by_id(key)

    def where(self, *, kind: str | None = None, include_hidden: bool = True) -> list[dict]:
        """Return categories matching the given filter.

        ``kind=None``          → all categories regardless of kind.
        ``include_hidden=False`` → drop rows whose ``hidden`` field is truthy.
        """
        rows = self.all()
        if kind is not None:
            rows = [r for r in rows if r.get("kind") == kind]
        if not include_hidden:
            rows = [r for r in rows if not r.get("hidden")]
        return rows
