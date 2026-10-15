"""Top dollar-contribution drivers for today — shared by Investments and Intel briefs."""

from __future__ import annotations

from backend.core.fx import to_base
from backend.services.fx_cache import load_fx_cache
from backend.services.positions import hydrated as hydrated_positions
from backend.services.profile import read_profile


def _day_change_base(row: dict, base: str, rates: dict) -> float | None:
    dc = row.get("day_change")
    if dc is None:
        return None
    ccy = row.get("quote_currency") or row.get("currency") or base
    return to_base(float(dc), ccy, base, rates=rates)


def get_drivers(limit_each: int = 3) -> dict:
    """Return top gainers + top losers by base-currency day contribution.

    Strict 3+3: takes up to limit_each from each side; if one side is short,
    compensates from the other so total stays up to limit_each*2.
    """
    rates = load_fx_cache()
    profile = read_profile()
    base = (profile.get("base_currency") or "SGD").upper()
    rows = hydrated_positions()

    gainers, losers = [], []
    for r in rows:
        if not float(r.get("price") or 0) > 0:
            continue
        # Unvested equity-grant units are not owned yet (ADR
        # 2026-07-31-unvested-equity-excluded-from-net-worth) — never a "driver" the holder
        # can act on, however large the paper move.
        if r.get("vesting_state") == "unvested":
            continue
        dc_base = _day_change_base(r, base, rates)
        if dc_base is None:
            continue
        entry = {
            "ticker": r.get("ticker") or "",
            "label": r.get("label") or "",
            "instrument_id": r.get("instrument_id") or "",
            "day_change_base": round(dc_base, 2),
            "day_change_pct": round(float(r.get("day_change_pct") or 0), 4),
        }
        if dc_base > 0:
            gainers.append(entry)
        elif dc_base < 0:
            losers.append(entry)

    gainers.sort(key=lambda x: x["day_change_base"], reverse=True)
    losers.sort(key=lambda x: x["day_change_base"])  # most negative first

    top_g = gainers[:limit_each]
    top_l = losers[:limit_each]
    # compensate if one side is short
    g_short = limit_each - len(top_g)
    l_short = limit_each - len(top_l)
    if g_short > 0:
        top_l = losers[: limit_each + g_short]
    if l_short > 0:
        top_g = gainers[: limit_each + l_short]

    return {"top_gainers": top_g, "top_losers": top_l, "base_currency": base}
