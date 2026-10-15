"""
EnvelopeGroupsStore — the budget's envelope groups (PLAN).

A group is a heading in the Planning (envelope-budgeting) table — Bills, Needs, Wants, the
app's own Commitments / Credit Card Payments, the "Others" catch-all. It carries a display
order + a collapsed flag, and holds ENVELOPES: an envelope places itself by ``envelope.group_id``
(+ ``envelope.order``), so a group has no member list of its own. Categories belong to no group:

    {"id": "grp-...", "name": "Essentials", "collapsed": false, "order": 0, "is_others": false}

Dumb storage + shape coercion ONLY — group ordering/validation and the "deleting a group moves
its envelopes to Others" rule live in ``services.planning`` (so an envelope is never orphaned).
Every envelope lands in a real group: one whose ``group_id`` doesn't resolve falls into the
persisted **"Others"** group (the default catch-all — ``ensure_others`` seeds it lazily; there
is NO synthetic in-memory bucket).

DB-backed (Tier-1 planning, table ``envelope_groups`` — backend/db/tables.py) via
``BookPayloadTableStore`` — no promoted columns beyond ``book_id``/``seq``/``id``/``payload``. A
group edit stays undoable in a Lucius turn — the write_log row-level mechanism (spec D7).

The catch-all is identified by the ``is_others`` flag, not a fixed id — like every other
group it gets an opaque ``new_id("grp")`` id, so its display name stays freely renamable
(a user can rename "Others" to anything) without any id-equality check silently breaking.

See docs/adr/2026-10-03-category-group-membership-is-retired.md (the table was
``category_groups`` and carried a per-group ``categories`` list until then);
docs/adr/2026-07-07-others-group-flag-not-id.md.
"""

from __future__ import annotations

from sqlalchemy import Connection

from backend.core.ids import new_id
from backend.db.payload_store import BookPayloadTableStore
from backend.db.tables import envelope_groups

# Fields a caller may patch on update (id immutable; is_others is internal-only — never
# caller-settable via update_group).
_EDITABLE = {"name", "collapsed", "order"}

# Seed name for a freshly minted catch-all group (display-only; renamable afterwards).
OTHERS_GROUP_NAME = "Others"


def _make_group(data: dict, order: int) -> dict:
    return {
        "id": data.get("id") or new_id("grp"),
        "name": (data.get("name") or "").strip(),
        "collapsed": bool(data.get("collapsed", False)),
        "order": int(data.get("order", order)),
        # A user-created group is never the catch-all — only ensure_others() sets this.
        "is_others": False,
    }


class EnvelopeGroupsStore(BookPayloadTableStore):
    def __init__(self, engine=None):
        super().__init__(envelope_groups, id_field="id", engine=engine)

    def list_ordered(self) -> list[dict]:
        """All groups sorted by (order, name) — the stable display order."""
        return sorted(
            self.all(), key=lambda g: (int(g.get("order", 0)), (g.get("name") or "").lower())
        )

    def create(self, data: dict, *, conn: Connection | None = None) -> dict:
        """Build + persist a group. Raises ValueError when name is blank.

        ``conn`` composes this write into a caller's own open transaction (choke
        composition, plan 4a task 5) — omitting it (``None``, the default) is
        byte-identical to before: this call opens its own lock-held transaction."""
        existing = self.all()
        group = _make_group(data, order=len(existing))
        if not group["name"]:
            raise ValueError("name is required")
        return self.insert(group, conn=conn)

    def update_group(
        self, group_id: str, changes: dict, *, conn: Connection | None = None
    ) -> dict | None:
        """Patch whitelisted fields. Returns the updated group, or None if not found.

        ``conn`` composes this write into a caller's own open transaction (choke
        composition, plan 4a task 5) — omitting it (``None``, the default) is
        byte-identical to before: this call opens its own lock-held transaction. Both
        ``find_by_id`` calls below are threaded with ``conn`` too (fix round 1) — a
        connection-blind existence check would wrongly report "not found" for a group THIS
        SAME transaction already inserted but has not committed yet."""
        if self.find_by_id(group_id, conn=conn) is None:
            return None
        clean = {k: v for k, v in changes.items() if k in _EDITABLE}
        if "name" in clean:
            clean["name"] = (clean["name"] or "").strip()
        if "collapsed" in clean:
            clean["collapsed"] = bool(clean["collapsed"])
        if "order" in clean:
            clean["order"] = int(clean["order"] or 0)
        if not clean:
            return self.find_by_id(group_id, conn=conn)
        return self.update_by_id(group_id, clean, conn=conn)

    def ensure_others(self, *, conn: Connection | None = None) -> dict:
        """Return the persisted catch-all group (flagged ``is_others``), creating it if
        absent (idempotent). Seeded with a high ``order`` so it sorts to the bottom of the
        table.

        ``conn`` composes a fresh-seed write into a caller's own open transaction (choke
        composition, plan 4a task 5) — omitting it (``None``, the default) is byte-identical
        to before: the write (when one happens) opens its own lock-held transaction."""
        flagged = next((g for g in self.all() if g.get("is_others")), None)
        if flagged is not None:
            return flagged
        group = {
            "id": new_id("grp"),
            "name": OTHERS_GROUP_NAME,
            "collapsed": False,
            "is_others": True,
            # +1000 keeps Others last even as user groups are added/reordered.
            "order": len(self.all()) + 1000,
        }
        return self.insert(group, conn=conn)

    def reorder(self, ordered_ids: list[str]) -> list[dict]:
        """Re-stamp ``order`` from the given id sequence; ids not listed keep relative order
        after the listed ones. Returns the new ordered list."""
        rank = {gid: i for i, gid in enumerate(ordered_ids)}
        groups = self.all()
        tail = len(rank)
        for g in groups:
            g["order"] = rank.get(g["id"], tail + g.get("order", 0))
        self.replace_all(groups)
        return self.list_ordered()
