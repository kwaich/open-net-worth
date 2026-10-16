"""
RecurringStreamStore — cash-flow recurring-stream USER STATE (.claude/rfcs/2026-07-27-cashflow-stream-model.md).

What this store holds is deliberately narrow: **only what cannot be recomputed**. The
detection itself (which transactions form a recurring stream, at what cadence) is pure math
over the ledger and is re-derived on every read by ``services/recurring.py`` — never
persisted, so it self-corrects as new transactions arrive and can never go stale.

**overlay** — a user correction to a DETECTED stream, keyed by the detection signature so it
survives new occurrences landing (the signature is built from payee + amount band + cadence,
not from transaction ids):
    type       : "overlay"
    id         : str    the detection signature key
    status     : "confirmed" | "dismissed"   (absence of a row = "detected", the default)
    note       : str
    created_at : str    ISO 8601
    updated_at : str

Only overlays are written. Rows of the retired ``declared`` shape (ADR
``2026-10-16-declared-recurring-streams-are-retired``) may still sit in the table; nothing reads
or writes them, and ``overlays()`` skips them.

Dumb CRUD only — no detection, no cadence math, no FX (CLAUDE.md "Adding a new store"). All
writes funnel through ``core.io.fire_db_write_hook``, so a confirm or dismiss is
checkpoint-covered and undoable like any other user data.

PLAN 4A TASK 3: DB-backed (Tier-1 planning, table ``recurring_streams`` — backend/db/tables.py)
via ``BookPayloadTableStore``. Money lives in payload — exact via JSON round-trip; no promoted
money column, mirrors money_io's payload rule.
"""

from __future__ import annotations

from datetime import datetime, timezone

from backend.db.payload_store import BookPayloadTableStore
from backend.db.tables import recurring_streams

VALID_STATUS = ("confirmed", "dismissed")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class RecurringStreamStore(BookPayloadTableStore):
    def __init__(self, engine=None):
        super().__init__(recurring_streams, id_field="id", engine=engine)

    # ── overlay rows (corrections to detected streams) ──────────────────────────

    def overlays(self) -> dict[str, dict]:
        """{signature_key: overlay_row} for every stored overlay."""
        return {r["id"]: r for r in self.all() if r.get("type") == "overlay"}

    def set_status(self, key: str, status: str, note: str = "") -> dict:
        """Upsert the overlay for a detection signature. Idempotent."""
        if status not in VALID_STATUS:
            raise ValueError(f"status: must be one of {', '.join(VALID_STATUS)}")
        if not (key or "").strip():
            raise ValueError("key: is required")
        existing = self.find_by_id(key)
        if existing and existing.get("type") != "overlay":
            raise ValueError(f"key: {key} is not an overlay row")
        row = {
            "type": "overlay",
            "id": key,
            "status": status,
            "note": note or "",
            "created_at": (existing or {}).get("created_at") or _now(),
            "updated_at": _now(),
        }
        return self.upsert(row)

    def reset(self, key: str) -> bool:
        """Drop the overlay → the stream goes back to whatever detection says."""
        existing = self.find_by_id(key)
        if not existing or existing.get("type") != "overlay":
            return False
        return self.delete(key)
