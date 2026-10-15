"""OptionContractStore — persisted derivative overlay contracts.

One record per option contract (covered-call/CSP overlay, defined-risk long).
Checkpoint-INCLUDED (portfolio truth, like lots/grants — unlike runtime caches).
A BookPayloadTableStore sibling of ``db/grant_store.py`` (flat-list shape, factory as
single coercion authority).

The service layer (``services/options.py``) is the ONLY writer; never raw-write
this store or bypass ``options_service.apply_option_change``.

Money in payload (strike/premium/collateral) — exact via JSON round-trip; no promoted
money column.

Contract shape (full schema in backend/models/option.py):
    id                       : str (uuid)  "opt_…"
    account_id               : str          FK → accounts.json
    underlying_instrument_id : str          FK → instruments.json
    option_type              : "call" | "put"
    side                     : "long" | "short"
    status                   : "open" | "expired" | "closed" | "assigned" | "exercised"
    strike                   : float
    expiry                   : "YYYY-MM-DD"
    contracts                : float        # of contracts (≥0)
    multiplier               : int = 100    shares per contract
    premium_per_share        : float        per share in quote_currency (OCC convention)
    premium_currency         : str
    quote_currency           : str          currency the underlying is priced in (defaults to premium_currency)
    open_date                : "YYYY-MM-DD" | None
    closed_at                : "YYYY-MM-DD" | None
    collateral_account_id    : str | None
    collateral_amount        : float | None
    strategy_tag             : str = ""
    idempotency_key          : str | None
    note                     : str = ""
    realized_premium_base    : float | None (snapshot only — RECOMPUTED on read)
    created_at, updated_at   : ISO
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import Connection

from backend.core.ids import new_id
from backend.db.payload_store import BookPayloadTableStore
from backend.db.tables import option_contracts

_VALID_TYPES = {"call", "put"}
_VALID_SIDES = {"long", "short"}
_VALID_STATUSES = {"open", "expired", "closed", "assigned", "exercised"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class OptionContractStore(BookPayloadTableStore):
    def __init__(self, engine=None):
        super().__init__(option_contracts, id_field="id", engine=engine)

    # ── Domain queries ────────────────────────────────────────────────────────

    def find_by_idempotency_key(self, key: str) -> dict | None:
        """Short-circuit dedupe: if a contract with this broker order id already
        exists, return it so ``apply_option_change`` can no-op."""
        if not key:
            return None
        for c in self.all():
            if c.get("idempotency_key") == key:
                return c
        return None

    def query_contracts(
        self,
        account_id: str | None = None,
        status: str | None = None,
        underlying_instrument_id: str | None = None,
    ) -> list[dict]:
        items = self.all()
        if account_id is not None:
            items = [c for c in items if c.get("account_id") == account_id]
        if status is not None:
            items = [c for c in items if c.get("status") == status]
        if underlying_instrument_id is not None:
            items = [
                c for c in items if c.get("underlying_instrument_id") == underlying_instrument_id
            ]
        return items

    # ── Writes ────────────────────────────────────────────────────────────────

    def create_contract(self, data: dict, *, conn: Connection | None = None) -> dict:
        """Coerce + insert one contract row. The single coercion authority.

        ``conn``, when given, composes this write into a caller's own open transaction
        (Task 4, storage plan 4b). ``conn=None`` (the default, every pre-Task-4 call site)
        is byte-identical."""
        return self.insert(_make_option_contract(data), conn=conn)

    def update_contract(
        self, contract_id: str, changes: dict, *, conn: Connection | None = None
    ) -> dict | None:
        """Patch a contract (merges changes, bumps updated_at).

        ``conn``, when given, composes both the read and the write into a caller's own
        open transaction (Task 4). ``conn=None`` is byte-identical to before."""
        row = self.find_by_id(contract_id, conn=conn)
        if row is None:
            return None
        updated = {**row, **changes, "updated_at": _now()}
        self.upsert(updated, conn=conn)
        return updated


def _make_option_contract(data: dict) -> dict:
    """Single coercion authority — normalises a raw dict into a stored contract row.

    Bad enum values clamp to defaults; numeric fields coerce to float/int with
    fallback to 0. Mirrors ``db/instrument_store._make_instrument`` in pattern."""
    option_type = str(data.get("option_type") or "").strip().lower()
    if option_type not in _VALID_TYPES:
        option_type = "call"

    side = str(data.get("side") or "").strip().lower()
    if side not in _VALID_SIDES:
        side = "long"

    status = str(data.get("status") or "").strip().lower()
    if status not in _VALID_STATUSES:
        status = "open"

    multiplier = _opt_int(data.get("multiplier"), default=100)
    if not multiplier or multiplier <= 0:
        multiplier = 100

    now = _now()
    return {
        "id": data.get("id") or new_id("opt"),
        "account_id": str(data.get("account_id") or ""),
        "underlying_instrument_id": str(data.get("underlying_instrument_id") or ""),
        "option_type": option_type,
        "side": side,
        "status": status,
        "strike": _opt_float(data.get("strike"), default=0.0),
        "expiry": str(data.get("expiry") or ""),
        "contracts": _opt_float(data.get("contracts"), default=0.0),
        "multiplier": multiplier,
        "premium_per_share": _opt_float(data.get("premium_per_share"), default=0.0),
        "premium_currency": (str(data.get("premium_currency") or "USD") or "USD").upper(),
        "quote_currency": (
            str(data.get("quote_currency") or data.get("premium_currency") or "USD") or "USD"
        ).upper(),
        # Buy/sell-to-close per-share price — set at close time (services._apply_close),
        # read by realized_outcomes. None = closed without a recorded price (realizes None,
        # never 0). Coerced here so the money field has a schema'd home, not a side-door key.
        "close_premium_per_share": _opt_float(data.get("close_premium_per_share")),
        "open_date": str(data.get("open_date")).strip() if data.get("open_date") else None,
        "closed_at": str(data.get("closed_at")).strip() if data.get("closed_at") else None,
        "collateral_account_id": data.get("collateral_account_id") or None,
        "collateral_amount": _opt_float(data.get("collateral_amount")),
        "strategy_tag": str(data.get("strategy_tag") or ""),
        "idempotency_key": data.get("idempotency_key") or None,
        "note": str(data.get("note") or ""),
        # Snapshot only — RECOMPUTED on read (never trusted for monetary math).
        "realized_premium_base": _opt_float(data.get("realized_premium_base")),
        "created_at": data.get("created_at") or now,
        "updated_at": data.get("updated_at") or now,
    }


def _opt_float(value, default: float | None = None) -> float | None:
    if value in (None, ""):
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _opt_int(value, default: int | None = None) -> int | None:
    if value in (None, ""):
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default
