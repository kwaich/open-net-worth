"""
EnvelopeStore — the budget envelope entity, decoupled from category.

Each row:

    {"id": "env_...", "name": "Groceries", "category_ids": ["food"],
     "group_id": "grp_...", "order": 0, "archived": false, "color": "",
     "archive_reason": "", "merged_into": "", "former_category_ids": [],
     "archived_at": ""}

Money — assigned amounts, targets, notes — is stored ENVELOPE-ID NATIVE in
``budget_assignments``/``profile.category_targets``/``profile.category_notes``
(docs/adr/2026-07-05-envelope-id-native.md), never on the envelope row itself. This
store carries NO money — it is a plain no-money planning store despite living beside
budget_assignments.

Dumb storage + shape coercion ONLY — cardinality (a category maps to AT MOST one envelope)
and all assigned/available/RTA math live in ``services.envelopes``/``services.planning``.

PLAN 4A TASK 2: DB-backed (Tier-1 planning, table ``envelopes`` — backend/db/tables.py) via
``BookPayloadTableStore`` — an envelope edit stays undoable in a Lucius turn, via the
write_log row-level mechanism now (spec D7), not the old file-blob one.

The archive-metadata fields (``archive_reason``/``merged_into``/``former_category_ids``/
``archived_at``) are stamped by ``services.envelopes.delete_envelope``/``merge_envelope`` at
archive time and read back by ``list_archived_envelopes`` — the store just coerces their shape.

See ``.claude/rfcs/envelope-not-category.md``.
"""

from __future__ import annotations

from sqlalchemy import Connection

from backend.core.ids import new_id
from backend.db.payload_store import BookPayloadTableStore
from backend.db.tables import envelopes

# Fields a caller may patch on update (id immutable).
_EDITABLE = {
    "name",
    "category_ids",
    "group_id",
    "order",
    "archived",
    "color",
    "archive_reason",
    "merged_into",
    "former_category_ids",
    "archived_at",
}


def _clean_category_ids(value) -> list[str]:
    """Coerce a category_ids value to a clean list[str]: drop blanks, dedupe (order-preserving)."""
    if value is None:
        return []
    if not isinstance(value, (list, tuple)):
        value = [value]
    out: list[str] = []
    for item in value:
        s = str(item).strip() if item is not None else ""
        if s and s not in out:
            out.append(s)
    return out


def _make_envelope(data: dict, order: int) -> dict:
    return {
        "id": data.get("id") or new_id("env"),
        "name": (data.get("name") or "").strip(),
        "category_ids": _clean_category_ids(data.get("category_ids")),
        "group_id": data.get("group_id") or None,
        "order": int(data.get("order", order)),
        "archived": bool(data.get("archived", False)),
        "color": (data.get("color") or "").strip(),
        "archive_reason": (data.get("archive_reason") or "").strip(),
        "merged_into": (data.get("merged_into") or "").strip(),
        "former_category_ids": _clean_category_ids(data.get("former_category_ids")),
        "archived_at": (data.get("archived_at") or "").strip(),
    }


class EnvelopeStore(BookPayloadTableStore):
    def __init__(self, engine=None):
        super().__init__(envelopes, id_field="id", engine=engine)

    def list_ordered(self) -> list[dict]:
        """All envelopes sorted by (order, name) — the stable display order."""
        return sorted(
            self.all(), key=lambda e: (int(e.get("order", 0)), (e.get("name") or "").lower())
        )

    def create(self, data: dict) -> dict:
        """Build + persist an envelope. Raises ValueError when name is blank."""
        existing = self.all()
        envelope = _make_envelope(data, order=len(existing))
        if not envelope["name"]:
            raise ValueError("name is required")
        return self.insert(envelope)

    def update_envelope(
        self, envelope_id: str, changes: dict, *, conn: Connection | None = None
    ) -> dict | None:
        """Patch whitelisted fields. Returns the updated envelope, or None if not found.

        ``conn`` composes this write into a caller's own open transaction (choke
        composition, plan 4a task 5) — omitting it (``None``, the default) is
        byte-identical to before: this call opens its own lock-held transaction. Both
        ``find_by_id`` calls below are threaded with ``conn`` too (fix round 1) — a
        connection-blind existence check would wrongly report "not found" for an envelope
        THIS SAME transaction already inserted but has not committed yet."""
        if self.find_by_id(envelope_id, conn=conn) is None:
            return None
        clean = {k: v for k, v in changes.items() if k in _EDITABLE}
        if "category_ids" in clean:
            clean["category_ids"] = _clean_category_ids(clean["category_ids"])
        if "name" in clean:
            clean["name"] = (clean["name"] or "").strip()
        if "group_id" in clean:
            clean["group_id"] = clean["group_id"] or None
        if "order" in clean:
            clean["order"] = int(clean["order"] or 0)
        if "archived" in clean:
            clean["archived"] = bool(clean["archived"])
        if "color" in clean:
            clean["color"] = (clean["color"] or "").strip()
        if "archive_reason" in clean:
            clean["archive_reason"] = (clean["archive_reason"] or "").strip()
        if "merged_into" in clean:
            clean["merged_into"] = (clean["merged_into"] or "").strip()
        if "former_category_ids" in clean:
            clean["former_category_ids"] = _clean_category_ids(clean["former_category_ids"])
        if "archived_at" in clean:
            clean["archived_at"] = (clean["archived_at"] or "").strip()
        if not clean:
            return self.find_by_id(envelope_id, conn=conn)
        return self.update_by_id(envelope_id, clean, conn=conn)
