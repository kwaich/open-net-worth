"""
TargetsStore — deadline savings targets (the "save X by date Y" primitive) (P2-BU1).

Modeled on CommitmentsStore: dumb storage + shape/validation ONLY. All target math
(required-monthly, months-remaining, pace verdict, auto-allocation) lives in
backend/services/targets.py, NOT here.

Writes fire core.io.fire_db_write_hook (the checkpoint capture point), so a target edit is
snapshot pre-write and a turn restore reverts it — the "targets" table is intentionally NOT in
ai.checkpoint._EXCLUDED_TABLES.

Target schema:
    id                : str    generated "tgt_<uuid12>" via core.ids.new_id
    name              : str    display name
    target_amount     : float  native (currency below); the goal
    current_amount    : float  native; saved so far (manual; service overrides when linked)
    currency          : str    native; defaults to base_currency
    target_date       : str    ISO date — the deadline
    linked_account_ids: list[str]  optional savings accounts; Σ live balances IS current_amount
    category          : str|None  optional budget category tie (e.g. "travel")
    priority          : int    ordering for auto-allocation (lower = funded first)
    status            : "active" | "completed" | "cancelled"  (display; pace verdict computed)
    note              : str
    created_at        : ISO date  anchors the on-pace expectation

See .claude/rfcs/phase2/bu1-budgets-targets.md.

PLAN 4A TASK 3: DB-backed (Tier-1 planning, table ``targets`` — backend/db/tables.py) via
``BookPayloadTableStore``. Money lives in payload — exact via JSON round-trip; no promoted money
column, mirrors money_io's payload rule.
"""

from __future__ import annotations

from backend.core.ids import new_id
from backend.core.user_time import user_today
from backend.db.payload_store import BookPayloadTableStore
from backend.db.tables import targets

STATUSES = {"active", "completed", "cancelled"}

# Fields a caller may set on update (id/created_at are immutable).
_EDITABLE = {
    "name",
    "target_amount",
    "current_amount",
    "currency",
    "target_date",
    "linked_account_ids",
    "category",
    "priority",
    "status",
    "note",
}


def _today() -> str:
    return user_today().isoformat()


def _to_float(value, default: float | None = 0.0) -> float | None:
    if value is None or value == "":
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _to_int(value, default: int = 1) -> int:
    if value is None or value == "":
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _clean_account_ids(value) -> list[str]:
    """Coerce a linked-accounts value to a clean list[str]: drop blanks, dedupe (order-preserving).

    Accepts None (→ []) or a list of ids. The singular linked_account_id was folded into this list
    by the forward migration (scripts/migrate_goals.py); the store never dual-reads the old key."""
    if value is None:
        return []
    if not isinstance(value, (list, tuple)):
        value = [value]
    out: list[str] = []
    for item in value:
        s = str(item).strip() if item is not None else ""
        if s and s not in out:
            out.append(s)
    return out


def _make_target(data: dict) -> dict:
    status = data.get("status")
    if status not in STATUSES:
        status = "active"
    return {
        "id": data.get("id") or new_id("tgt"),
        "name": (data.get("name") or "").strip(),
        "target_amount": _to_float(data.get("target_amount"), 0.0),
        "current_amount": _to_float(data.get("current_amount"), 0.0),
        "currency": (data.get("currency") or "").upper(),
        "target_date": data.get("target_date") or "",
        "linked_account_ids": _clean_account_ids(data.get("linked_account_ids")),
        "category": data.get("category") or None,
        "priority": _to_int(data.get("priority"), 1),
        "status": status,
        "note": data.get("note", ""),
        "created_at": data.get("created_at") or _today(),
    }


class TargetsStore(BookPayloadTableStore):
    def __init__(self, engine=None):
        super().__init__(targets, id_field="id", engine=engine)

    def create(self, data: dict) -> dict:
        """Build + persist a target with defaults, a unique id, and validated/coerced fields.

        Raises ValueError on the hard invariants (caller maps to a 4xx / structured error):
        name required, target_amount > 0.
        """
        target = _make_target(data)
        if not target["name"]:
            raise ValueError("name is required")
        if (target["target_amount"] or 0.0) <= 0:
            raise ValueError("target_amount must be greater than 0")
        return self.insert(target)

    def update_target(self, target_id: str, changes: dict) -> dict | None:
        """Patch whitelisted fields. Unknown/immutable keys dropped; floats/int/status validated.
        Returns the updated target, or None if not found."""
        if self.find_by_id(target_id) is None:
            return None
        clean = {k: v for k, v in changes.items() if k in _EDITABLE}
        if "target_amount" in clean:
            clean["target_amount"] = _to_float(clean["target_amount"], 0.0)
        if "current_amount" in clean:
            clean["current_amount"] = _to_float(clean["current_amount"], 0.0)
        if "priority" in clean:
            clean["priority"] = _to_int(clean["priority"], 1)
        if "currency" in clean:
            clean["currency"] = (clean["currency"] or "").upper()
        if "linked_account_ids" in clean:
            clean["linked_account_ids"] = _clean_account_ids(clean["linked_account_ids"])
        if "category" in clean:
            clean["category"] = clean["category"] or None
        if "status" in clean and clean["status"] not in STATUSES:
            clean.pop("status")
        return self.update_by_id(target_id, clean) if clean else self.find_by_id(target_id)

    def query_targets(self, status: str | None = None) -> list[dict]:
        items = self.all()
        if status is not None:
            items = [t for t in items if t.get("status") == status]
        return items
