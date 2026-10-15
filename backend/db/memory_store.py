"""
MemoryStore — Lucius's cross-session memory.

A flat list of memory items. Each item is a distilled fact about the user (a
preference, decision, intention, or piece of context) that should persist across
conversations. See docs/rfc/lucius-memory.md.

PLAN 4D TASK 2: DB-backed (table ``memory``) via ``PayloadTableStore``. **``user_id``-scoped
(spec:276), NOT ``book_id``** — a memory fact is a per-USER relationship with Lucius, not a
per-book ledger fact. Single-user for now (no ``user_id`` column yet); family mode adds one in
a LATER migration, never ``book_id``.
"""

from __future__ import annotations

from backend.core.user_time import user_today
from backend.db.payload_store import PayloadTableStore
from backend.db.tables import memory

TIERS = ("durable", "time_bound")
SOURCES = ("implicit", "explicit")


class MemoryStore(PayloadTableStore):
    def __init__(self, engine=None):
        super().__init__(memory, id_field="id", engine=engine)

    def all_facts(self) -> list[dict]:
        """All items, newest-updated first."""
        return sorted(
            self.all(),
            key=lambda f: f.get("updated_at", ""),
            reverse=True,
        )

    def active_facts(self, today: str | None = None) -> list[dict]:
        """
        All items except time_bound ones whose expires_at is in the past.
        This is what the system prompt injects.
        """
        today = today or user_today().isoformat()
        out = []
        for f in self.all_facts():
            exp = f.get("expires_at")
            if f.get("tier") == "time_bound" and exp and exp < today:
                continue
            out.append(f)
        return out

    def remove_by_id(self, id_val: str) -> bool:
        return self.delete(id_val)
