"""Correcting a lot that a sale has already used — date, quantity, cost, fees, kind.

A sold lot used to be frozen: its facts were "stamped on the sale", so fixing a wrong date or
cost meant undoing the sale and booking it again. Found 2026-10-13: a user could not give an
opening-balance lot its real purchase date because every share of it had been sold.

The correction keeps everything the SALE decided and recomputes only what follows from the
lot's facts:

  * which lots each sale used, and how many units from each, stay exactly as recorded — a
    correction never re-runs FIFO, so no other lot and no later sale moves;
  * each sale's quantity, price, fees and proceeds cash leg are untouched (none depend on the
    lot), so the ledger is not rewritten;
  * on each line that used THIS lot: the acquired date, the holding-period term, the unit cost
    and the line's gain are recomputed; the sale's realized gain is re-summed, and its base
    gain is converted at the rate the sale already stored (never today's);
  * the lot's quantity can't drop below what has been sold from it — that needs the sale
    edited, and the refusal says so.

``plan_sold_lot_amend`` is PURE — the Lucius card and the write use the same plan.
"""

from __future__ import annotations

import math
from typing import Optional

from backend.core.fx import to_base

_QTY_EPS = 1e-9
_LOT_FIELDS = ("unit_cost", "fees", "acquired_at", "kind", "cost_currency", "note")
# Only these change what the lot COST, so only they move a sale's gain. A date, kind or note
# correction re-dates the sale's lines (and their term) and leaves every gain as recorded.
_COST_FIELDS = ("unit_cost", "fees", "cost_currency")


def consumed_qty(lot: dict) -> float:
    return max(0.0, float(lot.get("original_qty") or 0) - float(lot.get("remaining_qty") or 0))


def sales_using(lot_id: str, events: list[dict]) -> list[dict]:
    return [
        ev for ev in events if any(c.get("lot_id") == lot_id for c in ev.get("consumptions") or [])
    ]


def plan_sold_lot_amend(
    lot: dict, change: dict, events: list[dict], *, base: str, rates: dict | None
) -> dict:
    """{"error"} or {"lot_updates", "event_updates": {id: patch}, "sales": [summary]}.

    ``events`` are every realized event of the lot's position (the caller reads them)."""
    sold = consumed_qty(lot)
    updates: dict = {k: change[k] for k in _LOT_FIELDS if k in change}
    if "quantity" in change:
        qty = float(change.get("quantity") or 0)
        if not math.isfinite(qty) or qty <= 0:
            return {"error": "quantity must be a positive number"}
        if qty + _QTY_EPS < sold:
            return {
                "error": (
                    f"{sold:g} units of this lot have already been sold, so it can't hold fewer "
                    f"than {sold:g} — edit or undo the sale first to sell fewer from it"
                )
            }
        updates["original_qty"] = qty
        updates["remaining_qty"] = round(qty - sold, 10)
    if "unit_cost" in updates or "cost_currency" in updates:
        from backend.services.lots import _rate_between

        ccy = (updates.get("cost_currency") or lot.get("cost_currency") or base).upper()
        updates["fx_rate"] = _rate_between(ccy, base, rates)
        updates["base_currency"] = base
    amended = {**lot, **updates}
    event_updates: dict[str, dict] = {}
    sales: list[dict] = []
    # Fees are spread over the lot's units, so with fees on the lot a new quantity is a new
    # per-unit cost too (review, 2026-10-13).
    costed = any(k in change for k in _COST_FIELDS) or (
        "quantity" in change and float(amended.get("fees") or 0) != 0
    )
    for ev in sales_using(lot["id"], events):
        patch = (
            _recompute_event(ev, amended, base=base, rates=rates)
            if costed
            else _redate_event(ev, amended)
        )
        if all(patch[k] == ev.get(k) for k in patch):
            continue  # a note or kind correction leaves this sale's record exactly as it is
        event_updates[ev["id"]] = patch
        sales.append(
            {
                "event_id": ev["id"],
                "disposed_at": ev.get("disposed_at"),
                "realized_gain_before": ev.get("realized_gain"),
                "realized_gain_after": patch["realized_gain"],
                "realized_gain_base_before": ev.get("realized_gain_base"),
                "realized_gain_base_after": patch["realized_gain_base"],
                "currency": ev.get("proceeds_currency"),
            }
        )
    return {"lot_updates": updates, "event_updates": event_updates, "sales": sales}


def _redate_event(ev: dict, lot: dict) -> dict:
    from backend.services.lots import classify_term

    lines = [
        {
            **line,
            "acquired_at": lot.get("acquired_at") or "",
            "term": classify_term(lot.get("acquired_at"), ev.get("disposed_at")),
        }
        if line.get("lot_id") == lot["id"]
        else line
        for line in ev.get("consumptions") or []
    ]
    return {
        "consumptions": lines,
        "cost_basis_known": ev.get("cost_basis_known"),
        "realized_gain": ev.get("realized_gain"),
        "realized_gain_base": ev.get("realized_gain_base"),
    }


def _recompute_event(ev: dict, lot: dict, *, base: str, rates: dict | None) -> dict:
    from backend.services.lots import line_realized_gain

    qty = float(ev.get("quantity") or 0)
    fee_per_unit = float(ev.get("disposal_fees") or 0) / qty if qty else 0.0
    lines = []
    for line in ev.get("consumptions") or []:
        if line.get("lot_id") != lot["id"]:
            lines.append(line)
            continue
        fresh = line_realized_gain(
            float(line.get("qty") or 0),
            float(ev.get("proceeds_per_unit") or 0),
            lot,
            ev.get("proceeds_currency") or "",
            rates,
            disposed_at=ev.get("disposed_at") or "",
            disposal_fee_per_unit=fee_per_unit,
        )
        lines.append({**line, **fresh})
    known = all(c.get("realized_gain") is not None for c in lines)
    if not ev.get("is_taxable", True):
        gain, gain_base = None, None
    elif known:
        gain = round(sum(float(c["realized_gain"]) for c in lines), 2)
        gain_base = _gain_base(ev, lines, gain, base=base, rates=rates)
    else:
        gain, gain_base = None, None
    return {
        "consumptions": lines,
        "cost_basis_known": known,
        "realized_gain": gain,
        "realized_gain_base": gain_base,
    }


def _gain_base(
    ev: dict, lines: list[dict], gain: float, *, base: str, rates: dict | None
) -> Optional[float]:
    """Per-leg (net proceeds at the sale's stored rate − each line's basis at its lot's
    acquire stamp) when every line carries a stamp; else the native gain at the sale's
    stored rate. Same two methods the sale itself used — only the sale-date rate is reused,
    so a correction never drags today's exchange rate into a past sale."""
    proceeds_rate = ev.get("fx_rate")
    ccy = ev.get("proceeds_currency") or base
    if (ev.get("base_currency") or base).upper() != base or not proceeds_rate:
        return to_base(gain, ccy, base, rates=rates)
    qty = float(ev.get("quantity") or 0)
    net = float(ev.get("proceeds_per_unit") or 0) * qty - float(ev.get("disposal_fees") or 0)
    stamps = [c.get("fx_rate_acquire") for c in lines]
    if all(stamps) and all(c.get("unit_cost") is not None for c in lines):
        cost_base = sum(
            float(c["unit_cost"]) * float(c.get("qty") or 0) * float(s)
            for c, s in zip(lines, stamps)
        )
        return round(net * float(proceeds_rate) - cost_base, 2)
    return round(gain * float(proceeds_rate), 2)
