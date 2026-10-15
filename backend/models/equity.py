"""Equity-compensation schemas — grants, vesting schedules, vest events (feat/equity-comp).

``models/`` is a leaf beside ``core/`` — imports only stdlib, pydantic, and ``.base``.
Mirrors the data model in .claude/rfcs/equity-comp.md §3.

Grant lifecycle: active → fully_vested | cancelled | forfeited.
Tranche identity is a stable uuid, not a positional index (reorder-safe).
VestEvent is the annotation row written by ``services/equity.apply_equity_change``
linking the four-store atomic vest action.

Two-step vest (``release_required=True``, ADR 2026-07-31): plans whose shares are
DELIVERED some time after they vest park the vested units in a ``bucket=contingent``
pending-release position instead of booking them straight to a liquid lot. The release
step moves shares only — it never touches money, so ``fmv_per_unit``/``date`` on the
VestEvent stay the write-time facts and the eventual lot is struck at those. Grants
without the flag keep the one-step path unchanged.
"""

from __future__ import annotations

from enum import Enum
from typing import List, Optional

from .base import ONBaseModel


class GrantType(str, Enum):
    rsu = "rsu"
    ltip = "ltip"


class TaxEvent(str, Enum):
    at_vest = "at_vest"
    deferred = "deferred"


class GrantStatus(str, Enum):
    active = "active"
    fully_vested = "fully_vested"
    cancelled = "cancelled"
    forfeited = "forfeited"


class TrancheKind(str, Enum):
    time = "time"
    performance = "performance"


class Tranche(ONBaseModel):
    """A single vesting tranche — stable ``id`` survives reorder/edit."""

    id: str
    vest_date: str  # YYYY-MM-DD
    units: float
    kind: TrancheKind = TrancheKind.time
    condition_note: Optional[str] = None
    vested: bool = False
    vest_event_id: Optional[str] = None


class Grant(ONBaseModel):
    """A persisted equity grant (RSU/LTIP)."""

    id: str
    type: GrantType = GrantType.rsu
    instrument_id: str
    grant_date: str  # YYYY-MM-DD
    total_units: float
    currency: str = ""  # grant-statement currency (informational)
    grant_price_per_unit: Optional[float] = None  # market price at grant date — reference only
    tax_event: TaxEvent = TaxEvent.at_vest
    status: GrantStatus = GrantStatus.active
    release_required: bool = False  # True = shares are delivered after vest (two-step)
    locked_position_id: Optional[str] = None
    pending_position_id: Optional[str] = None  # bucket=contingent; vested-not-delivered units
    account_id: Optional[str] = None
    tranches: List[Tranche] = []
    note: str = ""
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


class GrantCreate(ONBaseModel):
    """Input body for ``POST /api/equity/grants`` (and ``create_grant`` tool)."""

    type: GrantType = GrantType.rsu
    instrument_id: str
    grant_date: str
    total_units: float
    currency: str = ""
    grant_price_per_unit: Optional[float] = None  # market price at grant date — reference only
    tax_event: TaxEvent = TaxEvent.at_vest
    release_required: bool = False
    account_id: Optional[str] = None
    account_name: Optional[str] = None
    locked_position_id: Optional[str] = None
    tranches: List[dict] = []
    note: str = ""


class GrantUpdate(ONBaseModel):
    """Input body for ``PATCH /api/equity/grants/{id}`` — all fields optional."""

    grant_date: Optional[str] = None
    currency: Optional[str] = None
    grant_price_per_unit: Optional[float] = None  # market price at grant date — reference only
    tax_event: Optional[TaxEvent] = None
    release_required: Optional[bool] = None
    note: Optional[str] = None
    tranches: Optional[List[dict]] = None
    account_id: Optional[str] = None  # moves grant + locked position to this account
    account_name: Optional[str] = None  # creates/reuses an account by name


class VestApply(ONBaseModel):
    """Input body for ``POST /api/equity/grants/{id}/tranches/{tid}/vest``."""

    units: Optional[float] = None  # defaults to tranche.units
    fmv_per_unit: float
    fmv_currency: str
    vest_date: Optional[str] = None  # defaults to tranche.vest_date
    price_paid_per_unit: float = 0.0
    units_withheld: float = 0.0  # RESERVED (D5, no leg in v1)
    expected_release_date: Optional[str] = None  # optional; only meaningful when release_required
    idempotency_key: Optional[str] = None
    source: str = "manual"
    note: str = ""


class ReleaseApply(ONBaseModel):
    """Input body for ``POST /api/equity/vest-events/{id}/release``.

    All-or-nothing: a release delivers exactly the units that vested, so there is no
    ``units`` field. It moves shares only — no transaction is written, edited, or deleted.
    """

    release_date: Optional[str] = None  # defaults to today
    note: str = ""


class VestEvent(ONBaseModel):
    """The persisted vest-event annotation (append-only, idempotency-keyed)."""

    id: str
    grant_id: str
    tranche_id: str
    date: str  # vest date YYYY-MM-DD
    units: float
    fmv_per_unit: float
    fmv_currency: str
    price_paid_per_unit: float = 0.0
    units_withheld: float = 0.0
    gross_gain_native: float = 0.0  # snapshot; report recomputes
    txn_id: Optional[str] = None  # always None since 2026-09-22; set only on older events
    lot_id: Optional[str] = None  # None while pending_release — the lot is struck at release
    locked_position_id: Optional[str] = None
    # ── two-step vest (release_required grants) ──
    pending_release: bool = False  # True = vested, shares not yet delivered
    expected_release_date: Optional[str] = None
    released_on: Optional[str] = None  # actual delivery date; set by the release step
    pending_position_id: Optional[str] = None
    idempotency_key: Optional[str] = None
    source: str = "manual"
    base_amount: Optional[float] = None  # write-time snapshot; report recomputes
    base_currency: Optional[str] = None
    note: str = ""
    created_at: Optional[str] = None
