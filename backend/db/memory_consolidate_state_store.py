"""MemoryConsolidateStateStore — the memory-consolidation cadence watermark.

A single tiny state row: ``{last_count: int, ts: ISO}`` — the active-fact count right
after the last consolidation run. ``ai/memory.py::_should_consolidate`` reads it to gate
re-consolidation on GROWTH since that run (``CONSOLIDATE_MIN_GROWTH``), not merely "still
over threshold" — otherwise a book permanently above the threshold would re-consolidate
near every turn (cost fix 2026-07-11).

Runtime state, NOT user-authored data → checkpoint-EXCLUDED (a Lucius restore must not
roll the cadence watermark back), NOT portable.

WAVE 5F (JSON->DB): replaces the hand-rolled ``memory_consolidate_state.json`` (CACHE_DIR)
file I/O with a row in the ``runtime_state`` table, namespace ``"memory_consolidate_state"``
— mirrors ``HarnessStateStore`` exactly (same namespaced-singleton shape, same table,
same "genuinely a watermark, not a rebuildable cache" reasoning that put it there instead
of staying a file — see ``docs/adr/2026-08-10-intentionally-file-backed-stores.md``).
"""

from __future__ import annotations

from typing import Optional

from backend.db.payload_store import NamespacedSettingsStore
from backend.db.tables import runtime_state


class MemoryConsolidateStateStore(NamespacedSettingsStore):
    def __init__(self, engine: Optional[object] = None):
        super().__init__("memory_consolidate_state", table=runtime_state, engine=engine)
