"""A position's quantity, cost basis and cost currency — derived from its records, ONE writer.

ADR 2026-10-07-a-position-quantity-is-derived-from-its-records. The figures stay on the row
so every reader is unchanged, but nothing writes them except ``rederive``. Which records
they come from depends on what the position is:

  * a grant's LOCKED position — the units of its tranches not yet vested (0 once cancelled:
    the units were forfeited); unvested units have no basis.
  * a grant's PENDING-RELEASE position — the units of its vests still parked, at the
    vest-date FMV (``equity_release.pending_basis``).
  * anything else — its open lots.

The one row with no record: an unvested position no grant owns (hand-made before
2026-10-08, impossible since). Its stored figure is its only record and is left untouched.

Every copy the old write paths kept (± arithmetic at vest/park/undo, a caller-typed quantity
on add/update, a lot seeded from the stored number on read) drifted from these records.
"""

from __future__ import annotations

import logging

from sqlalchemy import Connection

from backend.core.fx import to_base
from backend.db.grant_store import GrantStore
from backend.db.instrument_store import InstrumentStore
from backend.db.lot_store import LotStore
from backend.db.position_store import PositionStore, vesting_state_of
from backend.db.vest_event_store import VestEventStore

logger = logging.getLogger(__name__)

_QTY_EPS = 1e-9


def grant_role(position_id: str, *, conn: Connection | None = None) -> tuple[str, dict | None]:
    """("locked" | "pending" | "owned", the owning grant or None)."""
    for grant in GrantStore().all(conn=conn):
        if grant.get("locked_position_id") == position_id:
            return "locked", grant
        if grant.get("pending_position_id") == position_id:
            return "pending", grant
    return "owned", None


def derive(position_id: str, *, conn: Connection | None = None) -> dict | None:
    """The figures the position's records imply, or None when there is nothing to derive
    from: the position doesn't exist, or it is an unvested row no grant owns."""
    pos = PositionStore().find_by_id(position_id, conn=conn)
    if pos is None:
        return None
    role, grant = grant_role(position_id, conn=conn)
    if role == "locked":
        return _locked_figures(grant)
    if role == "pending":
        return _pending_figures(position_id, conn=conn)
    if vesting_state_of(pos) == "unvested":
        # Unvested units come from a grant's schedule. A hand-made unvested row (possible
        # before 2026-10-08, refused since) has no grant, so its stored number IS its only
        # record — left exactly as it is, never zeroed. Nothing can edit it any more.
        return None
    return _owned_figures(pos, conn=conn)


def rederive(position_id: str, *, conn: Connection | None = None) -> dict:
    """Write the derived figures onto the position — the ONLY writer of them. Returns
    ``{"quantity", "cost_basis"}`` ({} when the position doesn't exist)."""
    figures = derive(position_id, conn=conn)
    if figures is None:
        return {}
    PositionStore().write_figures(position_id, figures, conn=conn)
    return {"quantity": figures["quantity"], "cost_basis": figures.get("cost_basis")}


def _locked_figures(grant: dict) -> dict:
    if grant.get("status") == "cancelled":
        return {"quantity": 0.0, "cost_basis": None}
    unvested = sum(
        float(t.get("units") or 0) for t in grant.get("tranches") or [] if not t.get("vested")
    )
    return {"quantity": round(unvested, 10), "cost_basis": None}


def _pending_figures(position_id: str, *, conn: Connection | None) -> dict:
    from backend.services import equity_release

    parked = sum(
        float(ev.get("units") or 0)
        for ev in VestEventStore().all(conn=conn)
        if ev.get("pending_release") is True and ev.get("pending_position_id") == position_id
    )
    basis, ccy = equity_release.pending_basis(position_id, conn=conn)
    figures: dict = {"quantity": max(0.0, round(parked, 10)), "cost_basis": basis}
    if ccy:
        figures["cost_currency"] = ccy
    return figures


def _owned_figures(pos: dict, *, conn: Connection | None) -> dict:
    """quantity = Σ remaining_qty of open lots; cost_basis = basis-weighted average unit cost
    when EVERY open lot's cost is known (None otherwise — a partial average would assign the
    known lots' cost to unknown units); cost_currency = the one currency those lots share.
    Mixed-currency lots convert each lot's full basis into the instrument's quote currency at
    current rates — averaging raw unit costs across currencies fabricates a number."""
    from backend.services.lots import effective_unit_cost

    open_lots = LotStore().open_lots_for_position(pos["id"], conn=conn)
    quantity = round(sum(float(lot.get("remaining_qty") or 0) for lot in open_lots), 10)
    known: list[tuple[float, float, str]] = []
    for lot in open_lots:
        remaining = float(lot.get("remaining_qty") or 0)
        unit_cost = effective_unit_cost(lot)
        if unit_cost is None:
            if remaining > 0:
                return {"quantity": quantity, "cost_basis": None}
            continue
        known.append((remaining, unit_cost, (lot.get("cost_currency") or "").upper()))
    known_qty = sum(q for q, _, _ in known)
    currencies = {ccy for _, _, ccy in known if ccy}
    if len(currencies) <= 1:
        basis = sum(q * c for q, c, _ in known)
        figures: dict = {
            "quantity": quantity,
            "cost_basis": round(basis / known_qty, 10) if known_qty else None,
        }
        if currencies:
            figures["cost_currency"] = next(iter(currencies))
        return figures
    inst = InstrumentStore().find_by_id(pos.get("instrument_id") or "", conn=conn) or {}
    quote = (inst.get("quote_currency") or "USD").upper()
    basis_quote = sum(
        to_base(q * c, ccy, quote) if ccy and ccy != quote else q * c for q, c, ccy in known
    )
    return {
        "quantity": quantity,
        "cost_basis": round(basis_quote / known_qty, 10) if known_qty else None,
        "cost_currency": quote,
    }


# ── Parity ──────────────────────────────────────────────────────────────────────────


def _differs(stored, derived) -> bool:
    if stored is None or derived is None:
        return (stored is None) != (derived is None)
    return abs(float(stored) - float(derived)) > _QTY_EPS * max(1.0, abs(float(derived)))


def check_parity() -> list[dict]:
    """Every position whose stored figures disagree with its records."""
    drift = []
    for pos in PositionStore().all():
        figures = derive(pos["id"])
        if figures is None:
            continue
        fields = {
            key: (pos.get(key), figures.get(key))
            for key in ("quantity", "cost_basis")
            if _differs(pos.get(key), figures.get(key))
        }
        if (
            "cost_currency" in figures
            and (pos.get("cost_currency") or None) != figures["cost_currency"]
        ):
            fields["cost_currency"] = (pos.get("cost_currency"), figures["cost_currency"])
        if fields:
            drift.append({"position_id": pos["id"], "fields": fields})
    return drift


def stray_grant_lots() -> list[dict]:
    """Lots on a grant's locked or pending-release position — units there are never lots.

    Migration 202610111300 removes the shape the old lots read wrote; anything else found
    here (sold from, or linked to cash) needs a person, so it is reported, never deleted."""
    managed = {
        grant.get(key)
        for grant in GrantStore().all()
        for key in ("locked_position_id", "pending_position_id")
        if grant.get(key)
    }
    return [lot for lot in LotStore().all() if lot.get("position_id") in managed]


def reconcile() -> list[dict]:
    """Boot repair: re-derive every drifted position and tell the user which holdings moved.

    Drift can only enter now through a raw write or a restored older backup — every choke
    re-derives. Correcting it (rather than refusing to boot) keeps the app usable; the
    notification names each change so a wrong one is visible, and the boot's pre-migrate
    backup holds the before-image."""
    drift = check_parity()
    for item in drift:
        rederive(item["position_id"])
        logger.warning("position %s re-derived from its records: %s", item["position_id"], item)
    for lot in stray_grant_lots():
        logger.warning(
            "lot %s sits on grant holding %s, whose units are not lots — left in place: %s",
            lot.get("id"),
            lot.get("position_id"),
            {k: lot.get(k) for k in ("kind", "original_qty", "remaining_qty", "txn_id")},
        )
    if any("quantity" in item["fields"] for item in drift):
        _tell_the_user(drift)
    return drift


def _tell_the_user(drift: list[dict]) -> None:
    from backend.db import NotificationLogStore

    instruments = {i.get("id"): i for i in InstrumentStore().all()}
    positions = {p.get("id"): p for p in PositionStore().all()}
    moved = []
    for item in drift:
        if "quantity" not in item["fields"]:
            continue
        before, after = item["fields"]["quantity"]
        inst = instruments.get((positions.get(item["position_id"]) or {}).get("instrument_id"), {})
        moved.append(f"{inst.get('symbol') or 'a holding'} {float(before or 0):g} → {after:g}")
    NotificationLogStore().append(
        {
            "type": "notification",
            "title": "Corrected holding quantities",
            "summary": (
                f"{len(moved)} holding(s) had a stored quantity that disagreed with their "
                f"purchase, sale and vest records, and now follow those records: "
                f"{', '.join(moved)}."
            ),
            "trigger": "data_repair",
        }
    )
