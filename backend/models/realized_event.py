"""Realized-event schemas — disposal events + per-lot consumption lines.

Mirrors ``db/realized_event_store.py`` and the draft built by
``services/lots.py:compute_disposal``. ``DisposalCreate`` / ``LotSelection`` are
the REST/AI input shapes; ``Consumption`` / ``RealizedEvent`` describe what the
service writes.

Scaffold/validation only — ``services/lots.py`` is the runtime authority for the
realized-gain arithmetic, holding-period classification, and FX conversion (RFC
P2-INV1 §7.3).
"""

from __future__ import annotations

from enum import Enum
from typing import List, Optional

from pydantic import field_validator

from .base import ONBaseModel


class LotMethod(str, Enum):
    fifo = "fifo"
    specific_id = "specific_id"


class Term(str, Enum):
    long = "long"
    short = "short"
    unknown = "unknown"


class LotSelection(ONBaseModel):
    """One named lot + qty in a specific-ID disposal."""

    lot_id: str
    qty: float


class Consumption(ONBaseModel):
    """A consumed-lot line on a realized event (the per-lot audit trail). Per-line
    ``term`` lets a downstream tax report split long vs short (AC-9)."""

    lot_id: str
    qty: float
    unit_cost: Optional[float] = None  # None when the lot's cost is unknown
    cost_currency: Optional[str] = None
    acquired_at: str = ""
    term: Term = Term.unknown
    realized_gain: Optional[float] = None  # per-line, in proceeds_currency; None if cost unknown


class DisposalCreate(ONBaseModel):
    """Sell/transfer-out input (the REST ``POST .../disposals`` body)."""

    quantity: float
    proceeds_per_unit: float = 0.0
    proceeds_currency: Optional[str] = None
    disposal_fees: float = 0.0
    disposed_at: str = ""
    method: LotMethod = LotMethod.fifo
    lot_selection: Optional[List[LotSelection]] = None  # required for specific_id
    is_taxable: bool = True  # false for transfer-out / gift (AC-15)
    # INV1-2: optionally deposit the net proceeds into this cash account (a transfer from a
    # synthetic "Market" account). None / "" → no cash leg ("proceeds not added to any account").
    proceeds_account_id: Optional[str] = None
    # Where the shares went when this is a move between the user's own accounts (id or
    # exact name). A label on the disposal, NOT a second leg — the receiving lots are still
    # acquired separately (kind=transfer_in). Requires is_taxable=false, zero proceeds and
    # no proceeds_account_id: a transfer moves shares, not money.
    transfer_to_account_id: Optional[str] = None
    note: str = ""

    @field_validator("proceeds_currency", mode="before")
    @classmethod
    def _upper(cls, v):
        return v.upper() if isinstance(v, str) and v else (v or None)


class RealizedEvent(ONBaseModel):
    """The stored disposal event (superset incl. timestamps)."""

    id: str
    position_id: str = ""
    instrument_id: str = ""
    account_id: str = ""
    disposed_at: str = ""
    quantity: float = 0.0
    proceeds_per_unit: float = 0.0
    proceeds_currency: Optional[str] = None
    disposal_fees: float = 0.0
    method: LotMethod = LotMethod.fifo
    is_taxable: bool = True
    transfer_to_account_id: Optional[str] = None  # resolved id; only on a share move
    cost_basis_known: bool = True
    consumptions: List[Consumption] = []
    realized_gain: Optional[float] = None
    realized_gain_base: Optional[float] = None
    base_currency: Optional[str] = None
    fx_rate: Optional[float] = None
    txn_id: Optional[str] = None
    idempotency_key: Optional[str] = None
    source: str = "manual"
    note: str = ""
    created_at: Optional[str] = None
