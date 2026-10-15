"""ActionStore — the property/commitment action log (``backend/services/actions.py``).

WAVE 5C (JSON->DB): replaces the hand-rolled ``json.load``/``atomic_write_text`` I/O in
``services/actions.py``/``api/actions_api.py`` (``actions.json``). Flat list, no book_id —
``services/actions.py``'s own pre-migration shape never had a book concept.

IDS ARE PLAIN INTEGERS — confirmed by ``ai/tools/portfolio.py``'s own
``UpdateActionStatusInput`` schema ("these ids are plain integers, not the xxx_<hex> ids
other stores use"), the first store this migration has hit with a non-string natural id.
``payload_table()``'s ``id`` column is ``Text``; SQLite's flexible column-affinity
coercion would silently paper over a bare Python ``int`` landing there, but Postgres
(CI-tested from revision 1) is not guaranteed to — so ``_insert_row`` below explicitly
``str()``-coerces the id COLUMN on every write path (insert/bulk_insert/upsert/
replace_all all route through this ONE override). The stored ``payload`` keeps the real
JSON int verbatim — round-trip fidelity is a payload-content question, not an id-column
one. No live production caller ever creates a NEW action (grep confirmed: only
read + status-update paths exist), so no id-minting logic is needed here — a caller that
ever adds one must decide how ids are assigned; this store does not guess.

Checkpoint-INCLUDED + portable (``ACTIONS_JSON`` was already reachable by ``/api/reset``
via ``core.portable.PORTABLE_FILES`` pre-migration — the DB bridge in
``db/portable_bridge.py`` preserves that).
"""

from __future__ import annotations

from typing import Any

from backend.db.payload_store import PayloadTableStore
from backend.db.tables import actions


class ActionStore(PayloadTableStore):
    def __init__(self, engine=None):
        super().__init__(actions, id_field="id", engine=engine)

    def _insert_row(self, seq: int, id_val: Any, item: dict) -> dict:
        return {"seq": seq, "id": str(id_val), "payload": item}
