"""
InstrumentStore — the instrument master (the "what").

One canonical record per security/coin/commodity, shared across every venue that
holds it. Identity for *pricing* is (price_source, price_source_id) — that pair is
what backend/api/prices.py consumes, and it never collides the way a human `symbol`
can across exchanges. Putting price_source_id here is what removes the hardcoded
COINGECKO_IDS map: a coin simply carries price_source="coingecko", price_source_id="solana".

DB-backed (plan 2, task 5): the first CHECKPOINT-INCLUDED store to leave the filesystem,
riding ``PayloadTableStore`` over ``backend.db.tables.instruments`` (a plain ``payload_table``
— no derived/indexed columns, since a row here is exactly one JsonStore item). Same public
surface as the retired flat-list-JSON implementation — every caller across services/ and
ai/tools/ is unchanged. Every domain lookup below is still an ``all()``-scan, exactly as it
was over the JSON file; this task does not "optimize" any of them into SQL (a subclass with a
real hot filter would earn its own indexed column instead, as ``evidence``'s expires_at/
domain already do — not warranted here yet).

Instrument schema:
    id              : str
    symbol          : str            display ticker (human-facing; may collide across venues)
    name            : str            long name (cached from price source)
    asset_class     : "equity" | "crypto" | "commodity" | "cash" | "bond" | "property"
                      economic EXPOSURE (the grouping axis). NB: "etf" is NOT a class — a fund's
                      class is its underlying exposure (a gold ETF is `commodity`); the ETF-ness
                      lives in asset_subclass. "etf" passed here is collapsed to "equity".
    asset_subclass  : str            WRAPPER/form: stock|etf|etp|etc|coin|stablecoin|precious_metal|physical|index|reit...
    quote_currency  : ISO code       currency the instrument is PRICED in
    price_source    : "yahoo" | "coingecko" | "manual"
    price_source_id : str            yahoo symbol ("ONWAI.DE") OR coingecko id ("solana")
    precision       : int            display decimals for quantity (crypto wants 8)
    unit            : str            holding unit ("" = native share/coin; "g","ozt","tael"… for physical metal)
    quote_per_unit  : float          native-quote-units per holding unit (default 1.0; value = qty × price × this)
    identifiers     : dict           {isin, figi, mic, coingecko_id} — all optional, for disambiguation.
                      Load-bearing for the resolve-time dedup ladder (services.instruments.
                      find_existing) — a shared isin/coingecko_id catches the same asset
                      resolved through two different price vendors, which the identity key
                      (price_source, price_source_id) alone cannot.
    manual_price    : float | None   used only when price_source == "manual"
    note            : str
    created_at      : ISO
    updated_at      : ISO

See .claude/rfcs/instrument-position-data-model.md.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone

from backend.core.errors import ValidationError
from backend.core.ids import new_id
from backend.db import tables
from backend.db.payload_store import PayloadTableStore

# Economic exposure, not wrapper. "etf" is intentionally absent: a fund is classified by its
# underlying exposure (a gold ETF is `commodity`), with the ETF-ness recorded in asset_subclass.
ASSET_CLASSES = {"equity", "crypto", "commodity", "cash", "bond", "property", "option"}
PRICE_SOURCES = {"yahoo", "coingecko", "manual"}

# Fixed-income subtypes (bonds-first-class RFC). "" = generic/unknown.
#   market = individual market bond (manual clean price as % of par)
#   ssb    = par-redeemable savings bond (marks at 100)
#   tbill  = discount bill (accretes issue_price → 100 over issue→maturity)
#   etf    = bond fund (Yahoo-priced; terms are informational)
#   fd     = fixed deposit / CD (principal + accrued simple interest)
BOND_SUBTYPES = {"", "market", "ssb", "tbill", "etf", "fd"}

# Loose check for manual price_source_ids. These never reach a URL fetcher; their ids
# are internal reference strings only (e.g. "ibkr:AAPL  240119C00150000" for IBKR
# derivatives). We only reject control characters (0x00-0x1F, 0x7F) and enforce a cap.
_MANUAL_PRICE_SOURCE_ID_MAX_LEN = 128
_MANUAL_CONTROL_CHAR_RE = re.compile(r"[\x00-\x1f\x7f]")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class InstrumentStore(PayloadTableStore):
    def __init__(self, engine=None):
        super().__init__(tables.instruments, id_field="id", engine=engine, checkpointed=True)

    # ── Domain lookups (dedupe before creating) ──────────────────────────────

    def find_by_source(self, price_source: str, price_source_id: str) -> dict | None:
        """The canonical dedupe key: an instrument is unique by how it's priced."""
        if not price_source_id:
            return None
        ps = (price_source or "").lower()
        pid = price_source_id.lower()
        for it in self.all():
            if (it.get("price_source") or "").lower() == ps and (
                it.get("price_source_id") or ""
            ).lower() == pid:
                return it
        return None

    def find_by_price_source_id(self, price_source_id: str) -> dict | None:
        """Case-insensitive lookup by price_source_id alone, any price_source — the
        identity a /quote/$symbol URL carries (it doesn't know the vendor). Safe
        because price_source_id is the pricing identity and, per the module docstring,
        "never collides the way a human `symbol` can across exchanges" — unlike
        find_by_symbol below, which is a KNOWN collision case (e.g. two distinct
        instruments both trading under the bare symbol "OKLO", see CommandPalette's
        Markets-result id comment)."""
        if not price_source_id:
            return None
        pid = price_source_id.lower()
        for it in self.all():
            if (it.get("price_source_id") or "").lower() == pid:
                return it
        return None

    def find_by_symbol(self, symbol: str) -> dict | None:
        """Case-insensitive human-symbol lookup (first match; symbol may not be unique)."""
        if not symbol:
            return None
        s = symbol.upper()
        for it in self.all():
            if (it.get("symbol") or "").upper() == s:
                return it
        return None

    def find_all_by_symbol(self, symbol: str) -> list[dict]:
        """Every instrument (any asset_class) sharing this case-insensitive bare symbol.
        Unlike find_by_symbol's "first match wins", this exposes whether the symbol is
        actually unique — a caller can safely treat it as an identity ONLY when exactly
        one instrument matches (e.g. the real "oklo" stock vs its tokenized-wrapper
        crypto twin: two entries, so neither is a safe blind pick)."""
        if not symbol:
            return []
        s = symbol.upper()
        return [it for it in self.all() if (it.get("symbol") or "").upper() == s]

    def find_by_symbol_and_class(self, symbol: str, asset_class: str) -> dict | None:
        """Case-insensitive symbol lookup scoped to one asset_class (first match).

        Distinct from find_by_symbol (which ignores class): a same-symbol collision only
        matters within the same economic exposure (a "BTC" equity ticker and a "BTC" coin
        are not the same ambiguity). Used by the resolve-time dedup ladder."""
        if not symbol:
            return None
        s = symbol.upper()
        for it in self.all():
            if (it.get("symbol") or "").upper() == s and it.get("asset_class") == asset_class:
                return it
        return None

    def find_by_identifier(self, key: str, value: str) -> dict | None:
        """Case-insensitive lookup by a value in the `identifiers` bag (isin/figi/coingecko_id).

        Catches the same asset resolved through two different price vendors (e.g. a coin
        added via CoinGecko, then re-added via a manual entry carrying the same
        coingecko_id) — the identity key (price_source, price_source_id) alone can't see
        this since the vendor differs. Used by the resolve-time dedup ladder."""
        if not key or not value:
            return None
        needle = str(value).lower()
        for it in self.all():
            existing = (it.get("identifiers") or {}).get(key)
            if existing and str(existing).lower() == needle:
                return it
        return None

    def query_instruments(
        self,
        asset_class: str | None = None,
        price_source: str | None = None,
    ) -> list[dict]:
        items = self.all()
        if asset_class is not None:
            items = [i for i in items if i.get("asset_class") == asset_class]
        if price_source is not None:
            ps = price_source.lower()
            items = [i for i in items if (i.get("price_source") or "").lower() == ps]
        return items

    # ── Writes ───────────────────────────────────────────────────────────────

    def create(self, data: dict) -> dict:
        return self.insert(_make_instrument(data))

    def find_or_create(self, data: dict) -> tuple[dict, bool]:
        """Return (instrument, created). Dedupes on (price_source, price_source_id)."""
        existing = self.find_by_source(
            data.get("price_source", ""), data.get("price_source_id", "")
        )
        if existing:
            return existing, False
        return self.create(data), True

    def touch(self, id_val: str, changes: dict) -> dict | None:
        """update_by_id but always bumps updated_at."""
        return self.update_by_id(id_val, {**changes, "updated_at": _now()})

    def set_constituents(self, id_val: str, constituents: list[dict]) -> dict | None:
        """Persist the ETF look-through map on an instrument (P2-INV5), bumping updated_at.

        Stores a NORMALIZED, coerced copy (each {weight, sector, region, label}). Validation
        (weights ≥0, Σ within tolerance) lives in services.xray.validate_constituents — the
        caller (route/tool) validates first; this is the dumb persist. `[]` clears the map
        (the instrument becomes opaque/self again). Returns the updated instrument or None."""
        clean = _coerce_constituents(constituents)
        return self.touch(id_val, {"constituents": clean})


def _validate_price_source_id(pid: str, price_source: str) -> None:
    """Raise ValidationError if price_source_id contains unsafe characters.

    Empty string is always accepted (manual-priced instruments may omit the id).

    Two tiers:
    - yahoo / coingecko: strict charset — letters, digits, . - ^ = _ only (max 64).
      These ids are interpolated into URLs so / ? # : @ whitespace are all blocked.
      re.fullmatch is used so a trailing \\n does NOT match.
    - manual: loose check — only reject control chars and enforce a 128-char cap.
      Manual ids (e.g. "ibkr:AAPL  240119C00150000") are internal reference strings
      that never reach a URL fetcher, so : and spaces are permitted."""
    if not pid:
        return
    if price_source in ("yahoo", "coingecko"):
        if not re.fullmatch(r"[A-Za-z0-9.=^_-]{1,64}", pid):
            raise ValidationError(
                f"price_source_id {pid!r} contains invalid characters or exceeds 64 chars. "
                "Allowed: letters, digits, . - ^ = _",
                field="price_source_id",
                value=pid,
            )
    else:
        # manual (and any future unknown source): reject control chars, cap length
        if len(pid) > _MANUAL_PRICE_SOURCE_ID_MAX_LEN or _MANUAL_CONTROL_CHAR_RE.search(pid):
            raise ValidationError(
                f"price_source_id {pid!r} contains control characters or exceeds 128 chars.",
                field="price_source_id",
                value=pid,
            )


def _make_instrument(data: dict) -> dict:
    asset_class = data.get("asset_class", "equity")
    asset_subclass = data.get("asset_subclass", "")
    if asset_class == "etf":  # legacy/wrapper value → exposure + keep the wrapper
        asset_class, asset_subclass = "equity", asset_subclass or "etf"
    if asset_class not in ASSET_CLASSES:
        asset_class = "equity"
    price_source = (data.get("price_source") or "yahoo").lower()
    if price_source not in PRICE_SOURCES:
        price_source = "yahoo"
    price_source_id = data.get("price_source_id", "")
    _validate_price_source_id(price_source_id, price_source)
    quote_currency = (data.get("quote_currency") or "").upper()
    # CoinGecko live prices are always fetched vs USD (prices._fetch_coingecko_by_id) and
    # valuation converts the mark AS quote_currency (ledger.row_base_value) — a non-USD
    # quote on a coingecko row would mislabel every mark and double-apply FX. Pin the
    # invariant here; the factory is the single coercion authority. (USD on coingecko is
    # definitional, not a fabrication — which is why the pin runs BEFORE the refuse below.)
    if price_source == "coingecko":
        quote_currency = "USD"
    elif not quote_currency:
        # Fail closed. A fabricated "USD" here is exactly how KSTL.DE shipped as a USD
        # instrument while priced in EUR (owner incident 2026-08-28): a caller-supplied
        # pin skipped the currency fetch and this default filled the gap, silently wrong
        # on every valuation by the EUR/USD rate. A creator that cannot supply a
        # currency must REFUSE — resolve fetches it from the feed (or demands it
        # explicitly for manual); changing it later is a controlled re-bind
        # (services.instruments._rebind_updates), never this default.
        raise ValidationError(
            "instrument has no quote_currency — refusing to fabricate one (a position "
            "must never be priced in a currency nobody fetched)",
            field="quote_currency",
        )
    now = _now()
    bond_terms = _coerce_bond_terms(data.get("bond_terms"), asset_class)
    return {
        "id": data.get("id") or new_id("inst"),
        "symbol": (data.get("symbol") or "").upper(),
        "name": data.get("name", ""),
        "asset_class": asset_class,
        "asset_subclass": asset_subclass,
        # GICS-style sector (e.g. "Technology", "Industrials"), cached from the price source for
        # concentration analysis. "" = not yet enriched or N/A (crypto/commodity/FX have none).
        "sector": data.get("sector", ""),
        "quote_currency": quote_currency,
        "price_source": price_source,
        "price_source_id": price_source_id,
        "precision": int(data.get("precision", 4) or 4),
        "unit": (data.get("unit") or "").strip().lower(),
        # ADR 0020: a bond's price is percent-of-par, so quote_per_unit MUST equal
        # face_value/100 — derived here, never caller-supplied (a wrong caller value would
        # silently mis-value every position by an order of magnitude, MATH-50).
        "quote_per_unit": (
            bond_terms["face_value"] / 100.0
            if bond_terms is not None
            else float(data.get("quote_per_unit") or 1.0) or 1.0
        ),
        "identifiers": data.get("identifiers") or {},
        "manual_price": data.get("manual_price"),
        # Forward annual per-unit dividend rate in quote_currency (P2-INV2). None = unknown
        # (forecast falls back to cached/trailing). Set by price-fetch enrichment or user override.
        "dividend_rate_annual": data.get("dividend_rate_annual"),
        # Provenance ratchet for dividend_rate_annual (mirrors classification_source):
        # "" → "enriched" → "manual". Never overwrite "manual".
        "dividend_rate_source": _coerce_dividend_rate_source(data.get("dividend_rate_source")),
        # Cached dividend calendar metadata (ADR 0020) — same additive/nullable pattern as
        # dividend_rate_annual. Set by prices.enrich_dividend_meta (or a user override); all None
        # for a non-payer. next_* are Yahoo-declared upcoming dates (future-only); last_ex_date +
        # dividend_frequency anchor the read-time projection in services.dividend_calendar.
        "next_ex_date": data.get("next_ex_date"),
        "next_pay_date": data.get("next_pay_date"),
        "last_ex_date": data.get("last_ex_date"),
        "last_dividend_amount": data.get("last_dividend_amount"),
        "dividend_frequency": data.get("dividend_frequency"),
        "dividend_meta_updated_at": data.get("dividend_meta_updated_at"),
        # ETF look-through map (P2-INV5): the fund's exposure breakdown — list of
        # {weight (0–1), sector, region, label?}. [] = opaque/self (a plain stock IS its own
        # exposure). Additive; defaults [] so every instrument written before this change is
        # "unmapped" with NO read-time fallback branch (the factory is the single coercion
        # authority — same pattern as `sector`/`dividend_rate_annual` were added).
        "constituents": _coerce_constituents(data.get("constituents")),
        # Fixed-income terms (bonds-first-class RFC). None for every non-bond — additive,
        # default-None, so instruments written before this change are simply "no terms"
        # (no migration, no read-time fallback). The store factory is the single coercion
        # authority; services.fixed_income derives marks/coupons from these terms.
        "bond_terms": bond_terms,
        # Automated sector/region classification fields (sys-classification RFC).
        # Additive, default-empty — instruments without these fields load as "unenriched"
        # with no read-time fallback (factory is the single coercion authority, same pattern).
        # classification_source ratchet: "" → "heuristic" → "source" → "manual".
        "region": str(data.get("region") or ""),
        "classification_source": _coerce_classification_source(data.get("classification_source")),
        "classification_updated_at": data.get("classification_updated_at") or None,
        "note": data.get("note", ""),
        "created_at": data.get("created_at") or now,
        "updated_at": data.get("updated_at") or now,
    }


def _coerce_constituents(value) -> list[dict]:
    """Normalize a raw constituents list into [{weight, sector, region, label}] dicts.
    Non-list / falsy → []. Drops unknown keys; coerces weight to float, sector/region/label
    to str. No weight normalization here (that's services.xray's job) — this is storage shape."""
    if not isinstance(value, list):
        return []
    out: list[dict] = []
    for c in value:
        if not isinstance(c, dict):
            continue
        try:
            weight = float(c.get("weight") or 0)
        except (TypeError, ValueError):
            weight = 0.0
        out.append(
            {
                "weight": weight,
                "sector": str(c.get("sector") or ""),
                "region": str(c.get("region") or ""),
                "label": str(c.get("label") or ""),
            }
        )
    return out


def _coerce_bond_terms(value, asset_class: str) -> dict | None:
    """Normalize fixed-income terms into a stored dict, or None.

    Returns None unless this is a bond (asset_class == "bond") AND value is a dict — bond
    terms are meaningless on a stock and we never invent them. subtype is clamped to
    BOND_SUBTYPES; numeric fields coerce to float/int with None for blank/unparseable;
    dates pass through as trimmed strings (validated by callers/services, not storage).
    coupon_rate_annual is a FRACTION (0.035 == 3.5%); face_value defaults to 100 (par)."""
    if asset_class != "bond" or not isinstance(value, dict):
        return None
    subtype = str(value.get("subtype") or "").strip().lower()
    if subtype not in BOND_SUBTYPES:
        subtype = ""
    return {
        "subtype": subtype,
        "face_value": _opt_float(value.get("face_value"), default=100.0) or 100.0,
        "coupon_rate_annual": _opt_float(value.get("coupon_rate_annual")),
        # Clamp to a sane domain [1, 120] months (monthly … decennial). An out-of-range or
        # fat-fingered value (e.g. the AI passing 1e8) must never reach the coupon date-walk
        # in services.fixed_income — an absurd step pushes datetime past its year bounds and
        # raises, which would 500 the fixed-income lens. Bounded here at the coercion authority.
        "coupon_freq_months": _clamp_freq_months(_opt_int(value.get("coupon_freq_months"))),
        "issue_date": (str(value.get("issue_date")).strip() or None)
        if value.get("issue_date")
        else None,
        "maturity_date": (str(value.get("maturity_date")).strip() or None)
        if value.get("maturity_date")
        else None,
        "issue_price": _opt_float(value.get("issue_price")),
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


def _clamp_freq_months(value: int | None) -> int | None:
    """Bound a coupon frequency to [1, 120] months. None / ≤0 → None (no schedule)."""
    if value is None or value <= 0:
        return None
    return min(value, 120)


_VALID_CLASSIFICATION_SOURCES = {"", "heuristic", "source", "manual"}


def _coerce_classification_source(value) -> str:
    """Normalise classification_source to a valid ratchet value, default ""."""
    v = str(value or "").strip().lower()
    return v if v in _VALID_CLASSIFICATION_SOURCES else ""


_VALID_DIVIDEND_RATE_SOURCES = {"", "enriched", "manual"}


def _coerce_dividend_rate_source(value) -> str:
    """Normalise dividend_rate_source to a valid ratchet value, default ""."""
    v = str(value or "").strip().lower()
    return v if v in _VALID_DIVIDEND_RATE_SOURCES else ""
