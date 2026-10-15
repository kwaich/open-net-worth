"""
VestEventStore — append-only ledger of vest events (feat/equity-comp).

One record per tranche vest action. Checkpoint-INCLUDED (user money-facts, not a
cache — same as income_events). A BookPayloadTableStore sibling of
``income_event_store.py`` adapted for the vest-event schema; the two stores serve
different lifecycles (grants = long-lived mutable config; vest_events = append-only
money-facts with idempotency + date-range queries).

Money in payload (fmv_per_unit/gross_gain_native/base_amount) — exact via JSON
round-trip; no promoted money column.

Event shape (full schema in backend/models/equity.py):
    id                  : str (uuid)
    grant_id            : str
    tranche_id          : str          — back-link to the specific tranche consumed
    date                : "YYYY-MM-DD" — vest date
    units               : float
    fmv_per_unit        : float        — as entered (fmv_currency)
    fmv_currency        : str
    price_paid_per_unit : float        — default 0 (RSU); >0 for discounted ESOW
    units_withheld      : float        — RESERVED (D5)
    gross_gain_native   : float        — units × (fmv − price_paid) in fmv_currency; snapshot
    txn_id              : str | None   — always None since 2026-09-22 (a vest books no txn);
                                         set only on events written before that
    lot_id              : str | None   — None while pending_release (lot is struck at release)
    locked_position_id  : str | None
    pending_release     : bool         — True = vested, shares not yet delivered
    expected_release_date : "YYYY-MM-DD" | None
    released_on         : "YYYY-MM-DD" | None
    pending_position_id : str | None   — the bucket=contingent position holding them
    idempotency_key     : str | None
    source              : "manual" | "ai" | "import"
    base_amount         : float        — write-time snapshot; report recomputes
    base_currency       : str
    note                : str
    created_at          : ISO
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import Connection

from backend.core.ids import new_id
from backend.db.payload_store import BookPayloadTableStore
from backend.db.tables import vest_events


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class VestEventStore(BookPayloadTableStore):
    def __init__(self, engine=None):
        super().__init__(vest_events, id_field="id", engine=engine)

    # ── Domain queries ────────────────────────────────────────────────────────

    def find_by_idempotency_key(self, key: str) -> dict | None:
        """Dedupe lookup for apply re-runs. Empty key never matches."""
        if not key:
            return None
        for ev in self.all():
            if ev.get("idempotency_key") == key:
                return ev
        return None

    def find_by_grant_tranche(self, grant_id: str, tranche_id: str) -> dict | None:
        """Find the vest event (if any) that consumed a specific tranche.
        Belt-and-suspenders idempotency check alongside find_by_idempotency_key."""
        for ev in self.all():
            if ev.get("grant_id") == grant_id and ev.get("tranche_id") == tranche_id:
                return ev
        return None

    def query_events(
        self,
        date_from: str | None = None,
        date_to: str | None = None,
        grant_id: str | None = None,
    ) -> list[dict]:
        """Filtered vest-event list. Dates are ISO 'YYYY-MM-DD'; sorted by date then created_at."""
        items = self.all()
        if date_from is not None:
            items = [e for e in items if e.get("date", "") >= date_from]
        if date_to is not None:
            items = [e for e in items if e.get("date", "") <= date_to]
        if grant_id is not None:
            items = [e for e in items if e.get("grant_id") == grant_id]
        return sorted(
            items,
            key=lambda e: (e.get("date") or "", e.get("created_at") or ""),
        )

    # ── Writes ────────────────────────────────────────────────────────────────

    def append(self, data: dict, *, conn: Connection | None = None) -> dict:
        """Append a vest event. Stamps id/created_at when absent.

        ``conn``, when given, composes this write into a caller's own open transaction
        (Task 4, storage plan 4b). ``conn=None`` (the default, every pre-Task-4 call site)
        is byte-identical."""
        row = {
            **data,
            "id": data.get("id") or new_id("vest"),
            "created_at": data.get("created_at") or _now(),
        }
        return self.insert(row, conn=conn)

    def update_event(
        self, event_id: str, changes: dict, *, conn: Connection | None = None
    ) -> dict | None:
        """Patch a vest event in place. Returns the updated row, or None if not found.

        The store is append-only for EVENTS — a vest is never rewritten into a different
        vest. This exists for the release step, which annotates an existing event with its
        delivery outcome (``released_on``/``lot_id``/``pending_release``) rather than
        appending a second money-fact for one economic event.

        ``conn``, when given, composes this write into a caller's own open transaction
        (Task 4). ``conn=None`` is byte-identical to before.
        """
        return self.update_by_id(event_id, changes, conn=conn)

    def query_pending_releases(self, grant_id: str | None = None) -> list[dict]:
        """Vest events whose delivery has not been recorded.

        Includes legacy events that predate ``pending_release``/``released_on``. Their
        existing lot is preserved; recording release annotates the date only.
        """
        items = [
            e
            for e in self.all()
            if e.get("pending_release") is True
            or (e.get("pending_release") is None and not e.get("released_on"))
        ]
        if grant_id is not None:
            items = [e for e in items if e.get("grant_id") == grant_id]
        return sorted(
            items, key=lambda e: (e.get("expected_release_date") or "9999", e.get("date") or "")
        )

    def delete_event(self, event_id: str, *, conn: Connection | None = None) -> bool:
        """Remove a vest event by id. Returns True if deleted, False if not found.

        ``conn``, when given, composes this write into a caller's own open transaction
        (Task 4, storage plan 4b — beyond the task brief's binding list: needed by
        ``equity._vest_undo``'s own composition). ``conn=None`` is byte-identical to
        before."""
        ev = self.find_by_id(event_id, conn=conn)
        if ev is None:
            return False
        self.delete(event_id, conn=conn)
        return True
