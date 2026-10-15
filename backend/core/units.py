"""Unit conversion for holdings counted in something other than their price's native unit.

A holding's value is `quantity × price × quote_per_unit`, where `quote_per_unit` converts ONE
holding unit into the unit the price is quoted in. For equities/ETFs/crypto the holding unit IS
the quote unit (share/coin), so `quote_per_unit == 1.0` and nothing changes.

Physical precious metals are the motivating case: spot/futures gold (GC=F, XAUUSD=X) quote per
TROY OUNCE, but physical holders count in grams, kilo bars, or regional units (tael / lượng in
VN, tola in IN, baht in TH). This table + helper turn a holding unit into the per-troy-ounce
factor so the universal qty×price math stays correct without special-casing any asset class.

See docs/prd/investment-tracking.md → "Units & physical commodities".
"""

from __future__ import annotations

# Grams per 1 of each mass unit. The canonical bridge between any two mass units.
GRAMS_PER: dict[str, float] = {
    "g": 1.0,
    "kg": 1000.0,
    "mg": 0.001,
    "oz": 28.349523125,  # avoirdupois ounce
    "ozt": 31.1034768,  # troy ounce — the precious-metal pricing standard
    "lb": 453.59237,
    "tael": 37.799364167,  # HK/China tael
    "luong": 37.5,  # Vietnamese lượng / cây (SJC gold bar, official 37.5 g)
    "tola": 11.6638038,  # India / South Asia
    "baht": 15.244,  # Thailand gold
}

# Spot/futures precious metals are quoted per troy ounce.
NATIVE_PRECIOUS_UNIT = "ozt"

# Holding units a user can pick for physical metal (UI/AI surface). Ordered by commonness.
METAL_UNITS = ["g", "kg", "ozt", "oz", "tael", "luong", "tola", "baht", "lb"]


def normalize_unit(unit: str | None) -> str:
    """Lower/trim a unit string. '' means native (share/coin) — no conversion."""
    return (unit or "").strip().lower()


def quote_per_unit(
    holding_unit: str | None, native_unit: str | None = NATIVE_PRECIOUS_UNIT
) -> float:
    """Factor to multiply a price quoted per `native_unit` by, to value ONE `holding_unit`.

    Returns 1.0 unless BOTH are known mass units (so shares/coins and unknown units pass
    through unchanged). Example: quote_per_unit('g', 'ozt') == 1/31.1035 — one gram of a
    per-troy-ounce-priced metal is ~0.0322 of the quoted price."""
    h = normalize_unit(holding_unit)
    n = normalize_unit(native_unit)
    if h in GRAMS_PER and n in GRAMS_PER:
        return GRAMS_PER[h] / GRAMS_PER[n]
    return 1.0
