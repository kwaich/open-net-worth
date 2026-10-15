"""Instrument schemas — the instrument master (the "what").

Mirrors ``db/instrument_store.py`` (``_make_instrument``, ``ASSET_CLASSES``,
``PRICE_SOURCES``). ``asset_class`` is the economic EXPOSURE (the grouping axis),
*not* the fund wrapper — there is no ``etf`` class; the store collapses a legacy
``"etf"`` to ``equity`` and records the wrapper in ``asset_subclass``.

Scaffold only (RFC P1-A3): these describe the stored shape; the store factory
stays the runtime coercion authority in wave 1.
"""

from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import field_validator

from .base import ONBaseModel


class AssetClass(str, Enum):
    """Economic exposure. NB: ``etf`` is intentionally absent — it is a wrapper."""

    equity = "equity"
    crypto = "crypto"
    commodity = "commodity"
    cash = "cash"
    bond = "bond"
    property = "property"
    # Derivative overlay band (sys-options-data-model RFC). Additive, default-absent —
    # no existing instrument migrates; the holding source of truth is OptionContractStore.
    # Used only for net-worth/allocation banding so the signed option mark lands in its
    # own band rather than folding into equity.
    option = "option"


class PriceSource(str, Enum):
    yahoo = "yahoo"
    coingecko = "coingecko"
    manual = "manual"


class Identifiers(ONBaseModel):
    """External identifiers for disambiguation — all optional."""

    isin: Optional[str] = None
    figi: Optional[str] = None
    mic: Optional[str] = None


class Constituent(ONBaseModel):
    """One look-through exposure slice of a fund (P2-INV5). weight is a fraction 0–1."""

    weight: float = 0.0
    sector: str = ""
    region: str = ""
    label: str = ""


class BondSubtype(str, Enum):
    """Fixed-income pricing model selector (bonds-first-class RFC)."""

    generic = ""
    market = "market"
    ssb = "ssb"
    tbill = "tbill"
    etf = "etf"
    fd = "fd"


class BondTerms(ONBaseModel):
    """Fixed-income terms on a ``asset_class == "bond"`` instrument (None for non-bonds).

    ``coupon_rate_annual`` is a FRACTION (0.035 == 3.5%). ``face_value`` is par per holding
    unit (a market bond is 1000 with qty = #bonds; ssb/tbill/fd use 1 with qty = principal).
    ``issue_price`` is the % of par paid at issue — drives T-bill discount accretion.
    Marks/coupons are DERIVED by ``services/fixed_income.py`` from these terms; nothing here
    is itself a price."""

    subtype: BondSubtype = BondSubtype.generic
    face_value: float = 100.0
    coupon_rate_annual: Optional[float] = None
    coupon_freq_months: Optional[int] = None
    issue_date: Optional[str] = None
    maturity_date: Optional[str] = None
    issue_price: Optional[float] = None


class Instrument(ONBaseModel):
    """The stored instrument shape (superset incl. timestamps)."""

    id: str
    symbol: str
    name: str = ""
    asset_class: AssetClass = AssetClass.equity
    asset_subclass: str = ""
    # GICS-style sector cached from the price source for concentration analysis.
    # "" = not yet enriched or N/A (crypto/commodity/FX have none).
    sector: str = ""
    quote_currency: str = "USD"
    price_source: PriceSource = PriceSource.yahoo
    price_source_id: str = ""
    precision: int = 4
    # Holding unit ("" = native share/coin; "g","ozt","tael"… for physical metal).
    unit: str = ""
    # native-quote-units per holding unit (value = qty × price × quote_per_unit).
    quote_per_unit: float = 1.0
    identifiers: Identifiers = Identifiers()
    manual_price: Optional[float] = None
    # Forward annual per-unit dividend rate in quote_currency (P2-INV2). None = unknown.
    dividend_rate_annual: Optional[float] = None
    # Provenance of dividend_rate_annual so the income forecast can tell a user-confirmed
    # figure from an auto-enriched one (mirrors classification_source, 2-tier: no
    # heuristic/source distinction applies to a dividend rate). Never overwrite "manual".
    # Ratchet: "" → "enriched" → "manual".
    dividend_rate_source: str = ""
    # Cached dividend calendar metadata (ADR 0020). All None = unknown / non-payer.
    # next_ex_date/next_pay_date are Yahoo's DECLARED upcoming dates (stored only when future);
    # last_ex_date + dividend_frequency anchor the read-time projection when nothing is declared.
    next_ex_date: Optional[str] = None  # ISO YYYY-MM-DD; Yahoo-declared upcoming ex-date
    next_pay_date: Optional[str] = None  # ISO YYYY-MM-DD; Yahoo-declared upcoming pay date
    last_ex_date: Optional[str] = None  # ISO YYYY-MM-DD; most recent historical ex-date
    last_dividend_amount: Optional[float] = None  # per-unit, quote_currency
    dividend_frequency: Optional[str] = None  # "monthly"|"quarterly"|"semi-annual"|"annual"
    dividend_meta_updated_at: Optional[str] = None  # ISO; system-maintained, not user-editable
    # ETF look-through map (P2-INV5): exposure breakdown; [] = opaque/self.
    constituents: list[Constituent] = []
    # Fixed-income terms (bonds-first-class RFC). None for every non-bond.
    bond_terms: Optional[BondTerms] = None
    # Region cached from a real source or inference (mirrors `sector`).
    # "" = not yet enriched. ISO-ish bucket: "United States" | "Europe" | "Asia ex-Japan" | …
    region: str = ""
    # Provenance of the auto sector/region so the UI can show "auto vs manual".
    # Never overwrite "manual". Ratchet: "" → "heuristic" → "source" → "manual".
    classification_source: str = ""
    # When the auto classification was last computed (ISO). System-maintained.
    classification_updated_at: Optional[str] = None
    note: str = ""
    created_at: Optional[str] = None
    updated_at: Optional[str] = None

    @field_validator("symbol", "quote_currency", mode="before")
    @classmethod
    def _upper(cls, v):
        return v.upper() if isinstance(v, str) else v


class InstrumentCreate(ONBaseModel):
    """The resolve-time subset accepted by ``InstrumentStore.create``/``find_or_create``.

    Replaces the loose dict the store accepts and the resolve-adjacent shape in
    ``api/instruments.py`` (wave 2).
    """

    symbol: str = ""
    name: str = ""
    asset_class: AssetClass = AssetClass.equity
    asset_subclass: str = ""
    sector: str = ""
    quote_currency: str = "USD"
    price_source: PriceSource = PriceSource.yahoo
    price_source_id: str = ""
    precision: int = 4
    unit: str = ""
    quote_per_unit: float = 1.0
    identifiers: Identifiers = Identifiers()
    manual_price: Optional[float] = None
    dividend_rate_annual: Optional[float] = None
    bond_terms: Optional[BondTerms] = None

    @field_validator("symbol", "quote_currency", mode="before")
    @classmethod
    def _upper(cls, v):
        return v.upper() if isinstance(v, str) else v
