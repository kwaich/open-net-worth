"""
GrantStore — persisted equity grants (RSU/LTIP).

One record per equity-compensation grant. Checkpoint-INCLUDED (user money-facts, not
a derived cache). A BookPayloadTableStore sibling of ``income_event_store.py``. The
service layer is the only writer; never raw-write this store.

No money column: unit counts (total_units, tranche units) are the payload floats;
grant value is derived, never stored.

Grant shape (full schema in backend/models/equity.py):
    id                  : str (uuid)
    type                : "rsu" | "ltip"
    instrument_id       : str
    grant_date          : "YYYY-MM-DD"
    total_units         : float
    currency            : str
    tax_event           : "at_vest" | "deferred"
    status              : "active" | "fully_vested" | "cancelled" | "forfeited"
    locked_position_id  : str | None
    account_id          : str | None
    tranches            : [Tranche]
    note                : str
    created_at, updated_at : ISO
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import Connection

from backend.core.ids import new_id
from backend.db.payload_store import BookPayloadTableStore
from backend.db.tables import grants


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class GrantStore(BookPayloadTableStore):
    def __init__(self, engine=None):
        super().__init__(grants, id_field="id", engine=engine)

    # ── Domain queries ────────────────────────────────────────────────────────

    def query_grants(
        self,
        status: str | None = None,
        instrument_id: str | None = None,
    ) -> list[dict]:
        """Filtered grant list. Optional status and instrument_id filters."""
        items = self.all()
        if status is not None:
            items = [g for g in items if g.get("status") == status]
        if instrument_id is not None:
            items = [g for g in items if g.get("instrument_id") == instrument_id]
        return sorted(items, key=lambda g: g.get("grant_date") or "")

    def find_by_idempotency_key(self, key: str) -> dict | None:
        """Dedupe lookup for grant-create re-runs. Empty key never matches."""
        if not key:
            return None
        for g in self.all():
            if g.get("idempotency_key") == key:
                return g
        return None

    # ── Writes ────────────────────────────────────────────────────────────────

    def append(self, data: dict, *, conn: Connection | None = None) -> dict:
        """Append (insert) a grant. Stamps id/created_at/updated_at when absent.

        ``conn``, when given, composes this write into a caller's own open transaction
        (Task 4, storage plan 4b). ``conn=None`` (the default, every pre-Task-4 call site)
        is byte-identical."""
        now = _now()
        row = {
            **data,
            "id": data.get("id") or new_id("grant"),
            "created_at": data.get("created_at") or now,
            "updated_at": data.get("updated_at") or now,
        }
        return self.insert(row, conn=conn)

    def update_grant(
        self, grant_id: str, changes: dict, *, conn: Connection | None = None
    ) -> dict | None:
        """Patch a grant (merges ``changes`` into the stored row, stamps updated_at).

        ``conn``, when given, composes both the read and the write into a caller's own
        open transaction (Task 4) — load-bearing when a composing choke already wrote
        this SAME grant earlier in the transaction (a fresh, conn-less read would miss
        that uncommitted change). ``conn=None`` is byte-identical to before."""
        row = self.find_by_id(grant_id, conn=conn)
        if row is None:
            return None
        updated = {**row, **changes, "updated_at": _now()}
        self.upsert(updated, conn=conn)
        return updated
