"""
IncomeEventStore — append-style ledger of investment income events (P2-INV2).

One record per dividend / interest / distribution a holding paid. Mirrors
``RealizedEventStore`` exactly (a sibling investment-money-fact store): income ≠
disposal, so they live in deliberately separate files, but share the same flat-list,
idempotency-keyed, date-filtered surface.

An income event is an ANNOTATION over a single cash inflow (the
``ledger_service.create_transaction`` the choke point makes) OR a reinvested lot (DRP).
It links that cash/lot to the instrument that paid it — the only way per-position income,
yield, and a forward forecast become answerable. ``income_summary``/``forecast_income``
RECOMPUTE the base figure from the native leg + current FX (storage's ``base_amount`` is a
write-time snapshot, stale after a base change — the CLAUDE.md invariant).

Identity is the UUID ``id``. Checkpoint-covered (real user data, NOT a cache).

Event schema (full shape in .claude/rfcs/phase2/inv2-dividends-income.md §Data model):
    id                : str
    position_id       : str
    instrument_id     : str
    account_id        : str
    paid_at           : "YYYY-MM-DD"
    income_kind       : "dividend" | "interest" | "distribution"
    quantity          : float|None       units held at pay date (per-share math)
    amount_per_unit   : float|None       per-unit payment in `currency`
    gross_amount      : float            total before withholding, in `currency` (authoritative)
    tax_withheld      : float            withholding in `currency`
    currency          : str
    is_drp            : bool             true = reinvested into a lot, no cash booked
    drp_lot_id        : str|None         the lot acquired when is_drp
    txn_id            : str|None         the cash income leg (null for DRP)
    base_amount       : float            write-time snapshot (recomputed on read)
    base_currency     : str
    fx_rate           : float|None
    idempotency_key   : str|None
    source            : "manual" | "ai" | "telegram" | "email"
    note              : str
    created_at        : ISO

PLAN 4A TASK 3: DB-backed (Tier-1 planning, table ``income_events`` — backend/db/tables.py) via
``BookPayloadTableStore``. Money lives in payload — exact via JSON round-trip; no promoted money
column, mirrors money_io's payload rule.
"""

from __future__ import annotations

from datetime import datetime, timezone

from backend.core.ids import new_id
from backend.db.payload_store import BookPayloadTableStore
from backend.db.tables import income_events


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class IncomeEventStore(BookPayloadTableStore):
    def __init__(self, engine=None):
        super().__init__(income_events, id_field="id", engine=engine)

    # ── Domain queries ────────────────────────────────────────────────────────

    def find_by_idempotency_key(self, key: str) -> dict | None:
        """Dedupe lookup for apply re-runs. Empty key never matches."""
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
        """Filtered income-event list. Dates are ISO 'YYYY-MM-DD'; lexical comparison is
        correct for ISO. Sorted by paid_at then created_at (chronological)."""
        items = self.all()
        if date_from is not None:
            items = [e for e in items if e.get("paid_at", "") >= date_from]
        if date_to is not None:
            items = [e for e in items if e.get("paid_at", "") <= date_to]
        if instrument_id is not None:
            items = [e for e in items if e.get("instrument_id") == instrument_id]
        if account_id is not None:
            items = [e for e in items if e.get("account_id") == account_id]
        if position_id is not None:
            items = [e for e in items if e.get("position_id") == position_id]
        return sorted(
            items,
            key=lambda e: (e.get("paid_at") or "", e.get("created_at") or ""),
        )

    # ── Writes ────────────────────────────────────────────────────────────────

    def append(self, data: dict) -> dict:
        """Append an income event. The service builds the full record (math home); this
        just stamps id/created_at if absent and persists."""
        row = {
            **data,
            "id": data.get("id") or new_id("inc"),
            "created_at": data.get("created_at") or _now(),
        }
        return self.insert(row)
