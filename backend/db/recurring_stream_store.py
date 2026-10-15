"""
RecurringStreamStore — cash-flow recurring-stream USER STATE (.claude/rfcs/2026-07-27-cashflow-stream-model.md).

What this store holds is deliberately narrow: **only what cannot be recomputed**. The
detection itself (which transactions form a recurring stream, at what cadence) is pure math
over the ledger and is re-derived on every read by ``services/recurring.py`` — never
persisted, so it self-corrects as new transactions arrive and can never go stale.

Two row shapes share one flat list, discriminated by ``type``:

**overlay** — a user correction to a DETECTED stream, keyed by the detection signature so it
survives new occurrences landing (the signature is built from payee + amount band + cadence,
not from transaction ids):
    type       : "overlay"
    id         : str    the detection signature key
    status     : "confirmed" | "dismissed"   (absence of a row = "detected", the default)
    note       : str
    created_at : str    ISO 8601
    updated_at : str

**declared** — a stream the user/Lucius asserts that history cannot show yet: a raise starting
next month, a subscription just cancelled (``end_date``), a bonus known to be coming. This is
the "expert input / known upcoming events" layer standard cash-flow practice calls for on top
of history-derived patterns:
    type       : "declared"
    id         : str    minted, ``rst_<12hex>``
    direction  : "in" | "out"
    label      : str    human name ("Salary — new rate")
    amount     : float  NATIVE amount per occurrence
    currency   : str    3-letter; "" = base
    cadence    : "weekly" | "biweekly" | "monthly" | "quarterly" | "annual"
    anchor_date: str    YYYY-MM-DD — one real occurrence date; the cadence steps from here
    start_date : str    YYYY-MM-DD | "" — stream is inactive before this
    end_date   : str    YYYY-MM-DD | "" — stream is inactive after this (a cancellation)
    note       : str
    created_at : str
    updated_at : str

Dumb CRUD only — no detection, no cadence math, no FX (CLAUDE.md "Adding a new store"). All
writes funnel through ``core.io.fire_db_write_hook``, so a dismiss or a declared-stream edit is
checkpoint-covered and undoable like any other user data.

PLAN 4A TASK 3: DB-backed (Tier-1 planning, table ``recurring_streams`` — backend/db/tables.py)
via ``BookPayloadTableStore``. Money lives in payload — exact via JSON round-trip; no promoted
money column, mirrors money_io's payload rule.
"""

from __future__ import annotations

from datetime import datetime, timezone

from backend.core.ids import new_id
from backend.db.payload_store import BookPayloadTableStore
from backend.db.tables import recurring_streams

VALID_STATUS = ("confirmed", "dismissed")
VALID_DIRECTION = ("in", "out")
VALID_CADENCE = ("weekly", "biweekly", "monthly", "quarterly", "annual")


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

    # ── declared rows (user/Lucius-asserted streams) ────────────────────────────

    def declared(self) -> list[dict]:
        return [r for r in self.all() if r.get("type") == "declared"]

    def create_declared(self, data: dict) -> dict:
        row = self._validated_declared(data)
        row["id"] = new_id("rst")
        row["type"] = "declared"
        row["created_at"] = _now()
        row["updated_at"] = _now()
        return self.insert(row)

    def update_declared(self, stream_id: str, changes: dict) -> dict | None:
        existing = self.find_by_id(stream_id)
        if not existing or existing.get("type") != "declared":
            return None
        merged = {**existing, **{k: v for k, v in changes.items() if k in _DECLARED_FIELDS}}
        row = self._validated_declared(merged)
        row["id"] = existing["id"]
        row["type"] = "declared"
        row["created_at"] = existing.get("created_at") or _now()
        row["updated_at"] = _now()
        return self.upsert(row)

    def delete_declared(self, stream_id: str) -> bool:
        existing = self.find_by_id(stream_id)
        if not existing or existing.get("type") != "declared":
            return False
        return self.delete(stream_id)

    @staticmethod
    def _validated_declared(data: dict) -> dict:
        """Field-prefixed plain-string errors (the WatchlistStore convention), never
        FastAPI's structured shape — these surface verbatim to a chat user."""
        label = str(data.get("label") or "").strip()
        if not label:
            raise ValueError("label: is required")
        if len(label) > 200:
            raise ValueError("label: must be 200 characters or fewer")
        direction = str(data.get("direction") or "").strip().lower()
        if direction not in VALID_DIRECTION:
            raise ValueError(f"direction: must be one of {', '.join(VALID_DIRECTION)}")
        cadence = str(data.get("cadence") or "").strip().lower()
        if cadence not in VALID_CADENCE:
            raise ValueError(f"cadence: must be one of {', '.join(VALID_CADENCE)}")
        try:
            amount = abs(float(data.get("amount")))
        except (TypeError, ValueError):
            raise ValueError("amount: must be a number") from None
        if amount <= 0:
            raise ValueError("amount: must be greater than zero")
        anchor = _valid_date(data.get("anchor_date"), "anchor_date", required=True)
        start = _valid_date(data.get("start_date"), "start_date")
        end = _valid_date(data.get("end_date"), "end_date")
        if start and end and end < start:
            raise ValueError("end_date: must not be before start_date")
        return {
            "direction": direction,
            "label": label,
            "amount": round(amount, 2),
            "currency": str(data.get("currency") or "").strip().upper(),
            "cadence": cadence,
            "anchor_date": anchor,
            "start_date": start,
            "end_date": end,
            "note": str(data.get("note") or "")[:1000],
        }


_DECLARED_FIELDS = {
    "direction",
    "label",
    "amount",
    "currency",
    "cadence",
    "anchor_date",
    "start_date",
    "end_date",
    "note",
}


def _valid_date(value, field: str, *, required: bool = False) -> str:
    raw = str(value or "").strip()
    if not raw:
        if required:
            raise ValueError(f"{field}: is required (YYYY-MM-DD)")
        return ""
    try:
        datetime.strptime(raw, "%Y-%m-%d")
    except ValueError:
        raise ValueError(f"{field}: must be a YYYY-MM-DD date") from None
    return raw
