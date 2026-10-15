"""
MemorySettingsStore — global memory toggle for the Lucius memory subsystem.

Single-item dict store: {"enabled": true}. Default when the namespace row is absent: enabled.

PLAN 4D TASK 2: DB-backed — a row in the shared ``settings`` table, namespace
"memory_settings", via ``NamespacedSettingsStore``. ``set_enabled`` is a read-modify-write
(``get()`` then ``save()``); the table lock covers each ``save()`` but NOT the whole
read-modify-write, so concurrent toggles are last-writer-wins — correct for this single-key
flag (the persisted value is always one writer's, never a torn merge). If this doc ever grows
a second independent key, thread one ``conn`` through ``get(conn=)``/``save(conn=)`` to make
the read-modify-write atomic.
"""

from __future__ import annotations

from backend.core.config import get_settings
from backend.db.payload_store import NamespacedSettingsStore


class MemorySettingsStore(NamespacedSettingsStore):
    def __init__(self, engine=None):
        super().__init__("memory_settings", engine=engine)

    # ── Public API ─────────────────────────────────────────────────────────────

    def enabled(self) -> bool:
        """Return True (the safe default) when the namespace row is absent or key missing."""
        return bool(self.get().get("enabled", True))

    def set_enabled(self, on: bool) -> dict:
        """Persist the flag and return the updated settings dict."""
        doc = self.get()
        doc["enabled"] = bool(on)
        self.save(doc)
        return {"enabled": bool(on)}


def memory_enabled_for(agent: dict | None) -> bool:
    """Return True iff both the global toggle AND (if agent given) per-agent flag are ON.

    Single AND-gate used by all memory paths across services and ai layers so the
    logic stays in one place at the db level where both layers can import it.
    """
    if not MemorySettingsStore().enabled():
        return False
    if agent is not None and not agent.get("memory_enabled", True):
        return False
    return True


def memory_writes_enabled() -> bool:
    """True when Lucius may ADD, forget or consolidate facts — the toggle, and never on a public demo.

    A demo's memory is shared by every visitor, so a fact distilled from one visitor's chat would
    surface in the next visitor's, and extraction is a model call nobody is charged for. Facts
    already in the book (the seeded ones) are still READ — injection checks ``enabled()`` only."""
    return not get_settings().demo_mode and MemorySettingsStore().enabled()
