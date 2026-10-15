"""Lot schemas — acquisition lots backing a position (cost basis + FIFO order).

Mirrors ``db/lot_store.py`` (``_make_lot``, ``LOT_KINDS``). The ``unit_cost``
"0/blank = unknown → None" rule is the same one ``positions_service.coerce_cost``
applies to ``cost_basis``; centralized here as a validator.

Scaffold/validation only — ``services/lots.py`` is the runtime authority for the
realized-P&L math (RFC P2-INV1 §7.3).
"""

from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import field_validator

from .base import ONBaseModel


class LotKind(str, Enum):
    buy = "buy"
    drp = "drp"
    transfer_in = "transfer_in"
    grant = "grant"
    opening = "opening"  # migration synthetic


def _cost_zero_to_none(v):
    """0 / "" / None → None (unknown cost). Mirrors positions_service.coerce_cost."""
    if v in (None, "", 0, 0.0):
        return None
    return v


class Lot(ONBaseModel):
    """The stored lot shape (superset incl. timestamps)."""

    id: str
    position_id: str = ""
    instrument_id: str = ""
    account_id: str = ""
    acquired_at: str = ""  # "" = unknown (migration sentinel)
    original_qty: float = 0.0
    remaining_qty: float = 0.0
    unit_cost: Optional[float] = None  # per unit in cost_currency; None = unknown
    cost_currency: Optional[str] = None
    fees: float = 0.0
    kind: LotKind = LotKind.buy
    txn_id: Optional[str] = None
    note: str = ""
    created_at: Optional[str] = None
    updated_at: Optional[str] = None

    @field_validator("unit_cost", mode="before")
    @classmethod
    def _coerce_cost(cls, v):
        return _cost_zero_to_none(v)

    @field_validator("cost_currency", mode="before")
    @classmethod
    def _upper(cls, v):
        return v.upper() if isinstance(v, str) and v else (v or None)


class LotCreate(ONBaseModel):
    """Acquire-a-lot input (the REST ``POST .../lots`` body)."""

    quantity: float
    unit_cost: Optional[float] = None
    cost_currency: Optional[str] = None
    fees: float = 0.0
    acquired_at: str = ""
    kind: LotKind = LotKind.buy
    allow_duplicate: bool = False  # override the identical-lot double-book refusal
    note: str = ""
    # Optional cash debit (ADR 2026-09-11) — omit to book the lot only.
    funding_account_id: Optional[str] = None

    @field_validator("unit_cost", mode="before")
    @classmethod
    def _coerce_cost(cls, v):
        return _cost_zero_to_none(v)

    @field_validator("cost_currency", mode="before")
    @classmethod
    def _upper(cls, v):
        return v.upper() if isinstance(v, str) and v else (v or None)


class InstrumentLotCreate(LotCreate):
    """First-hold acquire: mint (or reuse) the liquid position, then acquire."""

    account_id: str


class LotAmend(ONBaseModel):
    """PATCH a fully-open lot (identity) or any lot's note."""

    quantity: Optional[float] = None
    unit_cost: Optional[float] = None
    cost_currency: Optional[str] = None
    fees: Optional[float] = None
    acquired_at: Optional[str] = None
    kind: Optional[LotKind] = None
    note: Optional[str] = None
    funding_account_id: Optional[str] = None

    @field_validator("unit_cost", mode="before")
    @classmethod
    def _coerce_cost(cls, v):
        return _cost_zero_to_none(v)

    @field_validator("cost_currency", mode="before")
    @classmethod
    def _upper(cls, v):
        return v.upper() if isinstance(v, str) and v else (v or None)
