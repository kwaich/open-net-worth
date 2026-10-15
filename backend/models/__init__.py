"""Pydantic domain + API schemas for Open Net Worth.

A leaf layer beside ``core/`` (RFC P1-A3): the single source of truth for the
five highest-value domain shapes — Account, Transaction, Position, Instrument,
Profile — each as a *domain* model (the stored shape) plus the *API request*
schemas (create/update) that replace today's inline ``api/*`` duplicates.

``models/`` may import only stdlib, ``pydantic``, and ``backend.core`` — never
``db``/``services``/``api``/``ai`` (enforced by import-linter, P1-A6).

Scaffold only (wave 1): nothing imports these at runtime yet; call-site migration
is wave 2 (lazy, as files are touched). Callers write::

    from backend.models import Account, AccountCreate
"""

from __future__ import annotations

from .account import Account, AccountCreate, AccountKind, AccountType, AccountUpdate
from .base import ONBaseModel, _blank_to_none
from .instrument import (
    AssetClass,
    Identifiers,
    Instrument,
    InstrumentCreate,
    PriceSource,
)
from .position import (
    Bucket,
    Liquidity,
    Position,
    PositionCreate,
    PositionUpdate,
    VestingState,
)
from .profile import (
    BriefingPreferences,
    Liability,
    Profile,
    ProfileUpdate,
)
from .transaction import (
    BulkTransactionImport,
    Transaction,
    TransactionCreate,
    TransactionUpdate,
    TxnKind,
    TxnSource,
)

__all__ = [
    # base
    "ONBaseModel",
    "_blank_to_none",
    # account
    "Account",
    "AccountCreate",
    "AccountUpdate",
    "AccountKind",
    "AccountType",
    # transaction
    "Transaction",
    "TransactionCreate",
    "TransactionUpdate",
    "BulkTransactionImport",
    "TxnKind",
    "TxnSource",
    # position
    "Position",
    "PositionCreate",
    "PositionUpdate",
    "Bucket",  # DEPRECATED — migration/legacy read path only
    "Liquidity",
    "VestingState",
    # instrument
    "Instrument",
    "InstrumentCreate",
    "Identifiers",
    "AssetClass",
    "PriceSource",
    # profile
    "Profile",
    "ProfileUpdate",
    "Liability",
    "BriefingPreferences",
]
