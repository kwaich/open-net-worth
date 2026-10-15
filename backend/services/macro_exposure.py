"""
macro_exposure — deterministic portfolio exposure to macro factors (no LLM).

Computes exact currency exposure (FX math from core.fx.to_base) and tag-based
directional holdings for sector/theme signals. Imported by risk_signals.py (FX
mismatch check) and intel_agent.py (attaching computed affected_holdings to
type="macro" stories).

Dependency rule: imports only services/ db/ core/ — no api, no ai, no monitoring.
"""

from __future__ import annotations

import logging

from backend.core.errors import MissingFxRateError
from backend.core.fx import to_base
from backend.services.fx_cache import load_fx_cache
from backend.services.positions import hydrated as hydrated_positions
from backend.services.profile import read_profile

logger = logging.getLogger(__name__)


def _row_base_value(row: dict, base: str, rates: dict) -> float | None:
    """Market value of one hydrated position row in base currency, or None when its currency
    has no FX rate (core.fx refuses to guess 1:1)."""
    qty = float(row.get("quantity") or 0)
    price = float(row.get("price") or 0)
    qpu = float(row.get("quote_per_unit") or 1.0)
    native = qty * price * qpu
    ccy = row.get("quote_currency") or row.get("currency") or base
    try:
        return to_base(native, ccy, base, rates=rates)
    except MissingFxRateError:
        return None


def currency_exposure(
    rows: list[dict] | None = None,
    rates: dict | None = None,
    base: str | None = None,
) -> dict:
    """Exact per-currency FX exposure of the portfolio.

    Returns:
        {
          "<CCY>": {
            "base_value": float,          # total base-currency value of positions in this CCY
            "weight_pct": float,          # % of total portfolio (liquid positions)
            "holdings": [                 # each contributing position
              {"instrument_id": str, "label": str, "ticker": str, "base_value": float}
            ]
          },
          ...
        }

    Only liquid positions with a non-zero price are counted. A position whose currency has no
    FX rate cannot be valued in base: it is left out of the exposure and of the weight
    denominator (never valued 1:1) and logged, so the weights describe the valued book only.
    """
    if rows is None:
        rows = hydrated_positions()
    if rates is None:
        rates = load_fx_cache()
    if base is None:
        profile = read_profile()
        base = (profile.get("base_currency") or "SGD").upper()
    else:
        base = base.upper()

    liquid = [
        r for r in rows if r.get("vesting_state") != "unvested" and float(r.get("price") or 0) > 0
    ]
    values = [(r, _row_base_value(r, base, rates)) for r in liquid]
    unvalued = {
        (r.get("quote_currency") or r.get("currency") or base).upper()
        for r, v in values
        if v is None
    }
    if unvalued:
        logger.warning(
            "macro_exposure: no FX rate to %s for %s; those holdings are left out",
            base,
            ", ".join(sorted(unvalued)),
        )
    valued = [(r, v) for r, v in values if v is not None]
    total_base = sum(v for _, v in valued)

    exposure: dict[str, dict] = {}
    for row, bv in valued:
        ccy = (row.get("quote_currency") or row.get("currency") or base).upper()
        if bv <= 0:
            continue
        if ccy not in exposure:
            exposure[ccy] = {"base_value": 0.0, "weight_pct": 0.0, "holdings": []}
        exposure[ccy]["base_value"] = round(exposure[ccy]["base_value"] + bv, 2)
        exposure[ccy]["holdings"].append(
            {
                "instrument_id": row.get("instrument_id") or "",
                "label": row.get("label") or "",
                "ticker": row.get("ticker") or "",
                "base_value": None if bv is None else round(bv, 2),
            }
        )

    for ccy, data in exposure.items():
        data["weight_pct"] = round(data["base_value"] / total_base * 100, 2) if total_base else 0.0

    return exposure


def affected_holdings(
    macro_kind: str,
    *,
    currency: str | None = None,
    sector: str | None = None,
    rows: list[dict] | None = None,
    rates: dict | None = None,
    base: str | None = None,
    direction: str = "unknown",
) -> list[dict]:
    """Holdings affected by a macro event.

    macro_kind: "fx" | "rates" | "sector" | "theme"
      - "fx" / "rates": match by quote_currency == currency; base_value is EXACT via to_base.
      - "sector" / "theme": tag-only directional match (no magnitude claim); impact='directional'.

    Each returned entry:
      {instrument_id, label, ticker, base_value, impact, direction}

    direction: 'up' | 'down' | 'unknown' — supplied by the caller (the macro event author).
    FX mismatch (no rate): direction forced to 'unknown', impact 'directional' and base_value
    None — the holding is named but never valued at an invented rate.
    """
    if rows is None:
        rows = hydrated_positions()
    if rates is None:
        rates = load_fx_cache()
    if base is None:
        profile = read_profile()
        base = (profile.get("base_currency") or "SGD").upper()
    else:
        base = base.upper()

    liquid = [
        r for r in rows if r.get("vesting_state") != "unvested" and float(r.get("price") or 0) > 0
    ]
    results: list[dict] = []

    if macro_kind in ("fx", "rates") and currency:
        ccy_upper = currency.upper()
        for row in liquid:
            row_ccy = (row.get("quote_currency") or row.get("currency") or base).upper()
            if row_ccy != ccy_upper:
                continue
            bv = _row_base_value(row, base, rates)
            fx_degraded = bv is None
            eff_direction = "unknown" if fx_degraded else direction
            results.append(
                {
                    "instrument_id": row.get("instrument_id") or "",
                    "label": row.get("label") or "",
                    "ticker": row.get("ticker") or "",
                    "base_value": None if bv is None else round(bv, 2),
                    "impact": "directional" if fx_degraded else "fx_exact",
                    "direction": eff_direction,
                }
            )

    elif macro_kind in ("sector", "theme") and sector:
        sector_lo = sector.lower()
        for row in liquid:
            tags = [t.lower() for t in (row.get("tags") or [])]
            row_sector = (row.get("sector") or "").lower()
            if sector_lo not in tags and sector_lo != row_sector:
                continue
            bv = _row_base_value(row, base, rates)
            results.append(
                {
                    "instrument_id": row.get("instrument_id") or "",
                    "label": row.get("label") or "",
                    "ticker": row.get("ticker") or "",
                    "base_value": None if bv is None else round(bv, 2),
                    "impact": "directional",
                    "direction": direction,
                }
            )

    return results
