"""
RealizedEventStore — append-only ledger of disposal events + realized P&L.

A disposal (sell / transfer-out / gift) consumes one or more lots and books a
realized-gain *fact*. Unlike lots (mutable: remaining_qty shrinks), realized events
are immutable once written — they are the audit trail tax/return reports consume.
Splitting them from LotStore keeps each store single-responsibility and lets the
realized ledger be queried directly without walking mutable lot state.

Identity is the UUID `id`. See .claude/rfcs/phase2/inv1-tax-lots-pnl.md §7.2.

Money in payload (proceeds/realized_gain/fx/consumptions) — exact via JSON round-trip;
no promoted money column.

Event schema (abbreviated; full shape in the RFC):
    id                : str
    position_id       : str
    instrument_id     : str
    account_id        : str
    disposed_at       : "YYYY-MM-DD"
    quantity          : float       units disposed
    proceeds_per_unit : float       sale price per unit in proceeds_currency
    proceeds_currency : str
    disposal_fees     : float       in proceeds_currency (reduce proceeds)
    method            : "fifo" | "specific_id"
    is_taxable        : bool        false for transfer-out / gift (carries basis out)
    cost_basis_known  : bool        false if any consumed lot has unknown unit_cost
    consumptions      : list[dict]  per-lot lines: {lot_id, qty, unit_cost, cost_currency,
                                     acquired_at, term, realized_gain}
    realized_gain     : float|None  Σ line gains in proceeds_currency; None if cost unknown
    realized_gain_base: float|None  gain normalized to base at disposal (write-time snapshot)
    base_currency     : str         base in effect at write-time
    fx_rate           : float|None  proceeds_currency → base at disposal (snapshot)
    txn_id            : str|None     optional link to the cash credit leg
    idempotency_key   : str|None     client-supplied; dedupe re-apply
    source            : "manual" | "ai" | "telegram"
    note              : str
    created_at        : ISO

Checkpoint-covered (real user money-facts, not a cache).
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import Connection

from backend.core.ids import new_id
from backend.db.payload_store import BookPayloadTableStore
from backend.db.tables import realized_events


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class RealizedEventStore(BookPayloadTableStore):
    def __init__(self, engine=None):
        super().__init__(realized_events, id_field="id", engine=engine)

    # ── Domain queries ────────────────────────────────────────────────────────

    def find_by_idempotency_key(self, key: str) -> dict | None:
        """Dedupe lookup for apply re-runs (AC-16). Empty key never matches."""
        if not key:
            return None
        for ev in self.all():
            if ev.get("idempotency_key") == key:
                return ev
        return None

    def query_events(
        self,
        date_from: str | None = None,
        date_to: str | None = None,
        instrument_id: str | None = None,
        account_id: str | None = None,
        position_id: str | None = None,
    ) -> list[dict]:
        """Filtered realized-event list. Dates are ISO 'YYYY-MM-DD'; lexical comparison
        is correct for ISO. Sorted by disposed_at then created_at (chronological)."""
        items = self.all()
        if date_from is not None:
            items = [e for e in items if e.get("disposed_at", "") >= date_from]
        if date_to is not None:
            items = [e for e in items if e.get("disposed_at", "") <= date_to]
        if instrument_id is not None:
            items = [e for e in items if e.get("instrument_id") == instrument_id]
        if account_id is not None:
            items = [e for e in items if e.get("account_id") == account_id]
        if position_id is not None:
            items = [e for e in items if e.get("position_id") == position_id]
        return sorted(
            items,
            key=lambda e: (e.get("disposed_at") or "", e.get("created_at") or ""),
        )

    # ── Writes ────────────────────────────────────────────────────────────────

    def append(self, data: dict, *, conn: Connection | None = None) -> dict:
        """Append a disposal event. The service builds the full record (math home);
        this just stamps id/created_at if absent and persists.

        ``conn``, when given, composes this write into a caller's own open transaction
        (Task 4, storage plan 4b). ``conn=None`` (the default, every pre-Task-4 call site)
        is byte-identical."""
        row = {
            **data,
            "id": data.get("id") or new_id("rlz"),
            "created_at": data.get("created_at") or _now(),
        }
        return self.insert(row, conn=conn)
