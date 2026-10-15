"""
equity_release_service — the RELEASE (delivery) half of the two-step vest.

Many equity plans vest on one date and DELIVER the shares on a later one: broker
settlement lag, country-specific delivery rules, blackout/lock-up windows. Between the
two the units are genuinely yours — they move with the price and belong in net worth —
but they are not sellable, so booking them straight into the liquid position (the
one-step path) overstates liquidity.

The model reuses the shape the codebase already trusts for "units you own but can't
touch": a quantity-only position with no lots, exactly like the grant's ``locked``
position. Vested-not-delivered units park in a dedicated pending sibling — `vested`
(they ARE the holder's) but derived-`semi_liquid` (not sellable yet); the release step
moves them to the liquid position as a normal FMV lot.

INVARIANT — a release moves SHARES ONLY. It never creates, edits, or deletes a
transaction. The income leg (and therefore the taxable moment) is struck by the vest
step at vest-date FMV, and the lot this module creates is back-stamped to that same
date and price, so a delivery lag can never move money or restart a holding period.

Opt-in per grant (``grant.release_required``). Grants without the flag never touch this
module and keep the one-step path byte-for-byte.

IMPORT DISCIPLINE: sibling services (``lots``, ``positions``) are lazy-imported — a
module-level import would form a cycle and violate Contract 3.

STORE HANDLES: this module deliberately owns NO store instances. ``services.equity`` holds
the single set (``grants``/``vest_events``/``positions``/``instruments``/``price_history``)
and they are the documented monkeypatch target for tests, which patch the path constants
and then re-instantiate. A second set here would be bound to the paths as they stood at
import time — reading the LIVE data dir while the rest of a test runs against a tmp one.
``_svc()`` is therefore a lazy accessor, not an import cycle: ``equity`` imports this module
at load, this module reaches back only at call time.
"""

from __future__ import annotations

from typing import Optional

from sqlalchemy import Connection

from backend.core.errors import NotFoundError, ValidationError
from backend.core.fx import base_currency, to_base
from backend.core.user_time import user_today
from backend.db.engine import locked_unit_of_work
from backend.db.tables import lots as lots_tbl
from backend.db.tables import positions as positions_tbl
from backend.db.tables import vest_events as vest_events_tbl
from backend.services import position_figures

# Vested-but-undelivered units ARE the holder's — entitlement is unconditional — so the
# pending position is `vested` like any other owned holding. What makes it special is
# REACHABILITY, and that is the derived liquidity axis's job: `positions_service.
# liquidity_of` reports `semi_liquid` for a grant's pending position.
# (ADR 2026-07-31-split-position-bucket. Before the split this position was `contingent`,
# which conflated "not sellable" with "not owned" — exactly the overload that ADR removes.)
PENDING_VESTING_STATE = "vested"


def _svc():
    """The equity service module — sole owner of this domain's store handles."""
    from backend.services import equity as equity_service

    return equity_service


# ── Position helpers (shared with services/equity.py) ────────────────────────────────


def find_or_create_position(
    instrument_id: str, account_id: str, role: str, exclude_ids: set[str] = frozenset()
) -> str:
    """Find-or-create the sibling position for a grant ROLE.

    ``role`` is "liquid" (the delivered/sellable sibling) or "pending" (vested-not-yet-
    delivered). Both are `vesting_state="vested"` — they differ by which position the
    grant points at, NOT by an entitlement flag, so they must be told apart by identity
    rather than by scanning for a stored value.
    """
    from backend.services import positions as positions_service

    if role == "liquid":
        for pos in _svc().positions.all():
            if (
                pos.get("instrument_id") == instrument_id
                and pos.get("account_id") == account_id
                and pos["id"] not in exclude_ids
                and _vesting_state_of(pos) == "vested"
            ):
                return pos["id"]

    result = positions_service.apply_position_change(
        {
            "type": "add",
            "instrument_id": instrument_id,
            "account_id": account_id,
            "quantity": 0,
            "vesting_state": "unvested" if role == "locked" else "vested",
            "_grant_managed_ok": True,
            # No acquired_at: these positions are quantity-only. A lot is created by the
            # vest (one-step) or the release (two-step), never by the position add.
        }
    )
    if result.get("error"):
        raise ValidationError(f"failed to create {role} position: {result['error']}")
    return result["position_id"]


def _vesting_state_of(pos: dict) -> str:
    from backend.db.position_store import vesting_state_of

    return vesting_state_of(pos)


def find_or_create_liquid_position(grant: dict, account_id: str) -> str:
    """The grant's DELIVERED/sellable sibling.

    The grant's own locked and pending positions are excluded by id: since the split they
    are `vested` too (the pending one at least), so a value-scan would happily hand back
    the pending position and book the released lot straight onto the units that are still
    undelivered.
    """
    return find_or_create_position(
        grant["instrument_id"],
        account_id,
        "liquid",
        exclude_ids={
            grant.get("locked_position_id") or "",
            grant.get("pending_position_id") or "",
        },
    )


def assert_no_existing_vest_lot(position_id: str, tranche_id: str) -> None:
    """Belt-and-suspenders: prevent a duplicate vest lot if a prior apply crashed mid-flight."""
    from backend.db.lot_store import LotStore

    for lot in LotStore().lots_for_position(position_id):
        if (lot.get("note") or "").endswith(f":{tranche_id}"):
            raise ValidationError(
                "a vest lot already exists for this tranche",
                tranche_id=tranche_id,
                lot_id=lot.get("id"),
            )


def pending_basis(
    pending_position_id: str, *, conn: Connection | None = None
) -> tuple[float | None, str | None]:
    """The pending position's ``(cost_basis, cost_currency)``: the quantity-weighted vest-date
    FMV of every vest still parked on it. A parked unit has no lot — it is not sellable —
    but its basis IS known, and the Holdings row merges pending units with the delivered
    shares of the same instrument, so an unknown basis here blanks the P&L of the WHOLE
    holding (owner 2026-09-23: KSTL's 15 Sep tranche read "—" beside 1,230 broker-held
    shares with a perfectly good cost).

    Read by ``position_figures.rederive`` AFTER the vest event is written, so the store
    already holds every parked vest. Vests that disagree on currency yield ``(None, None)``:
    averaging raw unit costs across currencies fabricates a number, and unknown is honest.
    """
    legs: list[tuple[float, float, str]] = [
        (
            float(ev.get("units") or 0),
            float(ev.get("fmv_per_unit") or 0),
            ev.get("fmv_currency") or "",
        )
        for ev in _svc().vest_events.all(conn=conn)
        if ev.get("pending_release") is True
        and ev.get("pending_position_id") == pending_position_id
    ]
    legs = [(q, c, ccy.upper()) for q, c, ccy in legs if q > 0 and c > 0]
    qty = sum(q for q, _, _ in legs)
    if not qty or len({ccy for _, _, ccy in legs}) != 1:
        return None, None
    return round(sum(q * c for q, c, _ in legs) / qty, 10), legs[0][2]


def create_vest_lot(
    *,
    position_id: str,
    grant_id: str,
    tranche_id: str,
    units: float,
    unit_cost: float,
    cost_currency: str,
    acquired_at: str,
    txn_id: Optional[str],
    conn: Connection | None = None,
) -> str:
    """Create the FMV-basis lot for a vest. ``acquired_at``/``unit_cost`` are ALWAYS the
    vest-date facts, never the release date — delivery timing must not move cost basis.

    ``conn``, when given, mints the lot via the conn=-accepting core directly (Task 4,
    storage plan 4b) — bypassing ``apply_lot_change``'s OWN transaction, so a caller
    composing this call under its own ``locked_unit_of_work`` (``equity._vest_apply``,
    ``apply_release`` below) does not re-acquire the lots/positions locks already held by
    this thread and self-deadlock (``threading.Lock`` is not reentrant). Omitting it
    (``None``, every pre-Task-4 call site) is byte-identical: goes through the full
    ``apply_lot_change("acquire")`` choke, unchanged."""
    if conn is not None:
        return _mint_vest_lot(
            position_id=position_id,
            grant_id=grant_id,
            tranche_id=tranche_id,
            units=units,
            unit_cost=unit_cost,
            cost_currency=cost_currency,
            acquired_at=acquired_at,
            txn_id=txn_id,
            conn=conn,
        )

    from backend.services import lots as lots_service

    result = lots_service.apply_lot_change(
        {
            "type": "acquire",
            "position_id": position_id,
            "quantity": units,
            "unit_cost": unit_cost,
            "cost_currency": cost_currency,
            "acquired_at": acquired_at,
            "kind": "grant",
            "txn_id": txn_id,
            "note": f"vest:{grant_id}:{tranche_id}",
        }
    )
    if result.get("error"):
        raise ValidationError(f"failed to create vest lot: {result['error']}")
    return result.get("lot_id")


def _mint_vest_lot(
    *,
    position_id: str,
    grant_id: str,
    tranche_id: str,
    units: float,
    unit_cost: float,
    cost_currency: str,
    acquired_at: str,
    txn_id: Optional[str],
    conn: Connection,
) -> str:
    """The conn=-threaded core of create_vest_lot — mints the FMV lot directly via
    ``lots_service._mint_lot``, replicating ``_apply_acquire``'s fx_rate/base_currency
    stamp (the historical-rate lock, ADR 2026-07-15) without re-entering its transaction.
    No idempotency/duplicate-lot validation here: those guards are gated on
    ``kind in ("buy", "drp")`` in ``lots._find_identical_open_lot`` and never fire for a
    ``kind="grant"`` lot anyway (verified against the real code) — the caller
    (``_vest_apply``/``apply_release``) already guards duplicate vest lots via
    ``assert_no_existing_vest_lot``."""
    from backend.services import lots as lots_service

    pos = _svc().positions.find_by_id(position_id, conn=conn) or {}
    base = base_currency().upper()
    cost_ccy = (cost_currency or base).upper()
    fx_rate = lots_service._rate_between(cost_ccy, base, lots_service._load_rates())
    lot_data = {
        "position_id": position_id,
        "instrument_id": pos.get("instrument_id", ""),
        "account_id": pos.get("account_id", ""),
        "acquired_at": acquired_at,
        "original_qty": units,
        "remaining_qty": units,
        "unit_cost": unit_cost,
        "cost_currency": cost_currency,
        "fees": 0,
        "fx_rate": fx_rate,
        "base_currency": base,
        "kind": "grant",
        "txn_id": txn_id,
        "idempotency_key": None,
        "note": f"vest:{grant_id}:{tranche_id}",
    }
    lot, _agg = lots_service._mint_lot(lot_data, conn=conn)
    return lot["id"]


# ── Pending position (called by the vest choke in services/equity.py) ───────────────


def resolve_pending_position(grant: dict, account_id: str) -> str:
    """Find-or-create the grant's pending-release position SHELL (zero quantity) and, on
    first use, stamp ``grant.pending_position_id`` — the side-effect-bearing half of
    parking a vest's units (Task 4 split of the old combined ``park_pending``, so a
    composing choke — ``equity._vest_apply`` — can call this BEFORE its own transaction
    opens). An empty shell created here is inert if a LATER step in the SAME vest attempt
    fails and rolls back — qty=0 is invisible to hydrated()/net worth — so this step does
    not need to be atomic with the vest's economic facts. More importantly, it MUST run
    standalone: it writes ``grants`` (the pending_position_id stamp) through the
    uncomposed ``update_grant`` path, which would self-deadlock if called from inside a
    transaction that already holds the grants lock.
    """
    pending_pos_id = grant.get("pending_position_id")
    if pending_pos_id and _svc().positions.find_by_id(pending_pos_id):
        return pending_pos_id
    pending_pos_id = find_or_create_position(grant["instrument_id"], account_id, "pending")
    _svc().grants.update_grant(grant["id"], {"pending_position_id": pending_pos_id})
    return pending_pos_id


# ── release_apply ────────────────────────────────────────────────────────────────────


def apply_release(change: dict) -> dict:
    """Deliver a pending vest: pending position → FMV lot on the liquid position.

    All-or-nothing — the units delivered are exactly the units that vested. No money moves.

    TASK 4 (storage plan 4b — not in the brief's binding compositions list, flagged there
    as "if apply_release composes ≥2 wave tables — verify"; it does: lots + positions +
    vest_events): the lot mint + the pending-position drain + the vest-event annotation
    now commit as ONE transaction.
    """
    ev, grant, release_mode = _validate_release(change)
    release_date = change.get("release_date") or user_today().isoformat()
    if release_date < ev["date"]:
        raise ValidationError(
            "release_date cannot be before the vest date",
            release_date=release_date,
            vest_date=ev["date"],
        )

    units = float(ev.get("units") or 0)
    if release_mode == "record_only":
        # Legacy one-step vests already minted their vest-date lot. Delivery was not a
        # persisted fact at the time, so recording it now must annotate only — moving
        # shares or minting another lot would double-count the holding.
        with locked_unit_of_work(vest_events_tbl) as conn:
            _svc().vest_events.update_event(
                ev["id"],
                {
                    "pending_release": False,
                    "released_on": release_date,
                    "note": change.get("note") or ev.get("note", ""),
                },
                conn=conn,
            )
        from backend.db.lot_store import LotStore

        lot_id = ev.get("lot_id") or ""
        lot = LotStore().find_by_id(lot_id) or {}
        return {
            "ok": True,
            "vest_event_id": ev["id"],
            "grant_id": ev["grant_id"],
            "tranche_id": ev["tranche_id"],
            "released_on": release_date,
            "units": units,
            "lot_id": lot_id,
            "position_id": lot.get("position_id") or "",
            "release_mode": release_mode,
        }

    account_id = _account_for(grant, ev)

    # PRE-transaction (Task 4): resolve (or lazily create) the liquid position shell —
    # same inert-if-orphaned reasoning as resolve_pending_position's own docstring.
    liquid_pos_id = find_or_create_liquid_position(grant, account_id)
    assert_no_existing_vest_lot(liquid_pos_id, ev["tranche_id"])

    with locked_unit_of_work(lots_tbl, positions_tbl, vest_events_tbl) as conn:
        # Step 1: the lot lands on the liquid position, struck at VEST-date facts.
        lot_id = create_vest_lot(
            position_id=liquid_pos_id,
            grant_id=ev["grant_id"],
            tranche_id=ev["tranche_id"],
            units=units,
            unit_cost=float(ev.get("fmv_per_unit") or 0),
            cost_currency=ev.get("fmv_currency") or "",
            acquired_at=ev["date"],
            txn_id=ev.get("txn_id"),
            conn=conn,
        )

        # Step 2: annotate the vest event — no longer parked.
        _svc().vest_events.update_event(
            ev["id"],
            {
                "pending_release": False,
                "released_on": release_date,
                "lot_id": lot_id,
                "note": change.get("note") or ev.get("note", ""),
            },
            conn=conn,
        )

        # Step 3: the pending position follows the parked events (this one just left).
        if ev.get("pending_position_id"):
            position_figures.rederive(ev["pending_position_id"], conn=conn)

    return {
        "ok": True,
        "vest_event_id": ev["id"],
        "grant_id": ev["grant_id"],
        "tranche_id": ev["tranche_id"],
        "released_on": release_date,
        "units": units,
        "lot_id": lot_id,
        "position_id": liquid_pos_id,
        "release_mode": release_mode,
    }


def _validate_release(change: dict) -> tuple[dict, dict, str]:
    """Resolve the vest event + grant and refuse anything not awaiting delivery."""
    ev_id = change.get("vest_event_id") or change.get("id")
    ev = _svc().vest_events.find_by_id(ev_id) if ev_id else None
    if not ev and change.get("grant_id") and change.get("tranche_id"):
        ev = _svc().vest_events.find_by_grant_tranche(change["grant_id"], change["tranche_id"])
    if not ev:
        raise NotFoundError("vest event not found", vest_event_id=ev_id)

    if ev.get("released_on"):
        raise ValidationError(
            "this vest was already released",
            vest_event_id=ev["id"],
            released_on=ev["released_on"],
        )
    if ev.get("pending_release") is True:
        release_mode = "move_pending"
    elif ev.get("pending_release") is None and ev.get("lot_id"):
        release_mode = "record_only"
    else:
        raise ValidationError(
            "this vest is not awaiting release — its shares were delivered at vest",
            vest_event_id=ev["id"],
            hint="only grants with release_required=true have a release step",
        )

    grant = _svc().grants.find_by_id(ev.get("grant_id", ""))
    if not grant:
        raise NotFoundError("grant not found", grant_id=ev.get("grant_id"))
    return ev, grant, release_mode


def _account_for(grant: dict, ev: dict) -> str:
    """Derive the delivery account the same way the vest choke does — from the position's
    CURRENT account_id, so a grant moved between custodians delivers to the right place."""
    for pos_id in (ev.get("pending_position_id"), grant.get("locked_position_id")):
        pos = _svc().positions.find_by_id(pos_id or "")
        if pos and pos.get("account_id"):
            return pos["account_id"]
    account_id = grant.get("account_id")
    if not account_id:
        raise ValidationError(
            "cannot determine the delivery account for this release",
            grant_id=grant.get("id"),
        )
    return account_id


# ── Reads ────────────────────────────────────────────────────────────────────────────


def pending_releases(grant_id: Optional[str] = None) -> list[dict]:
    """Vests without a recorded delivery date, soonest-expected first.

    Includes two-step pending positions and legacy vests with an existing lot but no
    explicit release fact. Annotates indicative value at the current price.
    """
    from backend.services import lots as lots_service

    base = base_currency()
    rates = lots_service._load_rates()
    price_map = _svc().price_history.latest_map()
    insts = {i["id"]: i for i in _svc().instruments.all()}
    today = user_today().isoformat()

    result = []
    for ev in _svc().vest_events.query_pending_releases(grant_id):
        grant = _svc().grants.find_by_id(ev.get("grant_id", "")) or {}
        inst = insts.get(grant.get("instrument_id", "")) or {}
        units = float(ev.get("units") or 0)
        pid = inst.get("price_source_id") or ""
        last_price = price_map.get(pid) if pid else None
        indicative_value_base = None
        quote_currency = (inst.get("quote_currency") or base).upper()
        conversion_available = quote_currency == base or bool(
            rates.get(quote_currency) and rates.get(base)
        )
        if last_price is not None and units and conversion_available:
            indicative_value_base = round(
                to_base(last_price * units, quote_currency, base, rates=rates), 2
            )
        expected = ev.get("expected_release_date")
        result.append(
            {
                "vest_event_id": ev["id"],
                "grant_id": ev.get("grant_id"),
                "tranche_id": ev.get("tranche_id"),
                "symbol": inst.get("symbol", ""),
                "instrument_id": grant.get("instrument_id"),
                "vest_date": ev.get("date"),
                "units": units,
                "fmv_per_unit": ev.get("fmv_per_unit"),
                "fmv_currency": ev.get("fmv_currency"),
                "expected_release_date": expected,
                # `overdue` only when an expectation was actually recorded — a blank
                # expected date means "unknown", never "late".
                "overdue": bool(expected and expected < today),
                "indicative_value_base": indicative_value_base,
                "base_currency": base,
                "release_mode": (
                    "move_pending" if ev.get("pending_release") is True else "record_only"
                ),
            }
        )
    return result
