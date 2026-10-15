"""Every lot acquired in a date window, across all holdings — ``get_lots``' window mode.

Why it exists: a monthly report listed three of September's trades and could not say what was
bought — ``get_lots`` read one position at a time with no date filter, and the ledger's
``kind=investment`` rows carry no instrument (live, 2026-09: eleven purchase rows, none with a
ticker; an in-kind broker transfer has no ledger row at all). Sales already have
``get_realized_pnl`` by date.

Each lot keeps its ``kind``. Only a purchase spent money: ``buy`` / ``drp`` carry ``paid`` /
``paid_base``; a ``transfer_in`` (shares moved between brokers) or a ``grant`` (a vest) carries
``cost_basis`` / ``cost_basis_base`` instead, so totalling ``paid_base`` never counts shares
that arrived without money changing hands. The migration's synthetic ``opening`` lot is left out.

Base figures follow the historical-rate lock: the lot's stamped ``fx_rate`` while its
``base_currency`` matches the current base, otherwise today's rate (``rate`` says which).
"""

from __future__ import annotations

from backend.core.errors import ValidationError
from backend.core.fx import base_currency, to_base
from backend.db.lot_store import LotStore
from backend.services.fx_cache import load_fx_cache

MAX_ROWS = 300
_PAID = {"buy", "drp"}
_ARRIVED = {"transfer_in", "grant"}


def _base(native: float, lot: dict, ccy: str, base: str, rates: dict) -> tuple[float, str]:
    fx = lot.get("fx_rate")
    if fx and (lot.get("base_currency") or "").upper() == base:
        return native * float(fx), "trade_date"
    return to_base(native, ccy, base, rates=rates), "current"


def _priced(lot: dict, base: str, rates: dict) -> dict:
    kind = lot.get("kind") or "buy"
    out = dict(lot)
    if lot.get("unit_cost") is None:
        return out  # value unknown — never guessed
    ccy = (lot.get("cost_currency") or base).upper()
    native = float(lot.get("original_qty") or 0) * float(lot["unit_cost"]) + float(
        lot.get("fees") or 0
    )
    value, rate = _base(native, lot, ccy, base, rates)
    key = "paid" if kind in _PAID else "cost_basis"
    out.update({key: round(native, 2), f"{key}_base": round(value, 2), "rate": rate})
    return out


def lots_acquired(date_from: str, date_to: str, account_id: str | None = None) -> dict:
    if not date_from or not date_to or date_to < date_from:
        raise ValidationError("date_from and date_to are both required, date_from first")
    base, rates = base_currency().upper(), load_fx_cache()
    lots = sorted(
        (
            lot
            for lot in LotStore().all()
            if (lot.get("kind") or "buy") in _PAID | _ARRIVED
            and date_from <= (lot.get("acquired_at") or "") <= date_to
            and (account_id is None or lot.get("account_id") == account_id)
        ),
        key=lambda lot: (lot.get("acquired_at") or "", lot.get("created_at") or ""),
    )
    return {
        "base_currency": base,
        "window": {"from": date_from, "to": date_to},
        "lots": [_priced(lot, base, rates) for lot in lots[:MAX_ROWS]],
        "showing": min(len(lots), MAX_ROWS),
        "total": len(lots),
        "has_more": len(lots) > MAX_ROWS,
    }
