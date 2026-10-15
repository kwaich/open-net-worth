"""Option contract schemas — derivative overlay data model (sys-options-data-model RFC).

A dedicated ``OptionContract`` entity rather than an ``AssetClass`` + Position sub-model
because an option fails every premise of the qty×price×quote_per_unit identity:
it expires to zero, can be a liability (short side), derives its mark from an underlying,
and carries a ×100 multiplier the flat ``quantity`` field cannot express.

Mirrors ``models/instrument.py`` (``BondTerms``/``AssetClass``) — scaffold only. The store
factory (``db/option_contract_store._make_option_contract``) is the runtime coercion
authority; the service (``services/options.py``) is the monetary-math home.

Leaf beside ``models/instrument.py`` — imports only stdlib, pydantic, and ``.base``.
"""

from __future__ import annotations

from enum import Enum
from typing import Optional

from .base import ONBaseModel


class OptionType(str, Enum):
    """Call = right to buy the underlying; put = right to sell it."""

    call = "call"
    put = "put"


class OptionSide(str, Enum):
    """Long = bought/owned (asset, premium paid). Short = written/sold (liability, premium received)."""

    long = "long"
    short = "short"


class OptionStatus(str, Enum):
    """Lifecycle state. Only ``open`` contributes to live net worth.
    ITM/OTM is DERIVED by ``services/options``, never stored."""

    open = "open"
    expired = "expired"
    closed = "closed"
    assigned = "assigned"
    exercised = "exercised"


class OptionContract(ONBaseModel):
    """Full persisted shape of one option contract row (``option_contracts.json``)."""

    id: str
    account_id: str
    underlying_instrument_id: str

    option_type: OptionType
    side: OptionSide
    status: OptionStatus = OptionStatus.open

    strike: float
    expiry: str  # ISO YYYY-MM-DD
    contracts: float  # number of contracts (≥0); economic shares = contracts × multiplier
    multiplier: int = 100  # shares per contract (OCC equity standard = 100)

    # Cost basis — per SHARE (OCC quote convention). Total cash = contracts × multiplier ×
    # premium_per_share. (The field was briefly declared premium_per_contract here while the
    # store/service/API all persisted premium_per_share — model/store drift, fixed 2026-07-15.)
    premium_per_share: float = 0.0
    premium_currency: str = "USD"
    # Currency the UNDERLYING is priced in — strike/intrinsic/collateral are denominated in
    # it; defaults to premium_currency at the store factory for pre-field rows.
    quote_currency: Optional[str] = None
    # Buy-to-close / sell-to-close per-share price, set when the contract closes. Read by
    # realized_outcomes for premium P&L; None = closed without a recorded price (realizes
    # None, never 0).
    close_premium_per_share: Optional[float] = None

    open_date: Optional[str] = None  # ISO YYYY-MM-DD
    closed_at: Optional[str] = None  # set when status leaves "open"

    # Collateral linkage (covered-call / CSP overlays).
    collateral_account_id: Optional[str] = None
    collateral_amount: Optional[float] = (
        None  # CSP cash lock in quote_currency; None for covered calls
    )

    strategy_tag: str = ""  # "covered_call" | "csp" | "long_call" | "protective_put" | ""
    idempotency_key: Optional[str] = None  # broker order id (dedupe)
    note: str = ""

    # Snapshot only — RECOMPUTED on read; never trusted for monetary math.
    realized_premium_base: Optional[float] = None

    created_at: Optional[str] = None
    updated_at: Optional[str] = None


class OptionContractCreate(ONBaseModel):
    """Resolve-time subset accepted by the store/REST/AI for opening a contract.

    Defaults mirror the full model: multiplier=100, status=open, strategy_tag="".
    The store factory stamps id/timestamps and coerces enums from strings."""

    account_id: str
    underlying_instrument_id: str
    option_type: OptionType
    side: OptionSide
    strike: float
    expiry: str  # ISO YYYY-MM-DD
    contracts: float
    multiplier: int = 100
    premium_per_share: float = 0.0
    premium_currency: str = "USD"
    quote_currency: Optional[str] = None
    open_date: Optional[str] = None
    collateral_account_id: Optional[str] = None
    collateral_amount: Optional[float] = None
    strategy_tag: str = ""
    idempotency_key: Optional[str] = None
    note: str = ""
