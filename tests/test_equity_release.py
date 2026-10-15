"""
Tests for the two-step vest — vested shares that are not DELIVERED yet (ADR 2026-07-31).

The feature exists because a vest and the delivery of its shares are two different events
on many plans (broker settlement lag, country delivery rules, blackout windows). Between
them the units are owned but NOT sellable, and booking them straight to the liquid
position claims otherwise.

What these tests pin, in order of what would hurt most if it broke:

  1. OPT-IN. A grant without ``release_required`` is byte-for-byte the old one-step path.
     This is the regression guard for every existing user on earth.
  2. NO MONEY MOVES AT RELEASE. The release writes no transaction and changes no amount;
     the lot it creates carries the VEST-date cost basis and acquisition date.
  3. LIQUIDITY IS HONEST. Pending units sit in their own position, derived-semi_liquid,
     with NO lot — so nothing reports them as sellable. They are still `vested`:
     they ARE the holder's, which is why net worth keeps counting them.
  4. The pending position is grant-managed (hand-edits refused) and undo works in BOTH
     shapes (before and after delivery).
"""

from __future__ import annotations

import pytest

from backend.core.errors import ValidationError
from backend.db.account_store import AccountStore
from backend.db.grant_store import GrantStore
from backend.db.instrument_store import InstrumentStore
from backend.db.lot_store import LotStore
from backend.db.position_store import PositionStore
from backend.db.transaction_store import TransactionStore
from backend.db.vest_event_store import VestEventStore
from backend.services import equity as svc
from backend.services import equity_release as release_svc
from backend.services import ledger as ledger_service
from backend.services import lots as lots_service
from backend.services import positions as positions_service


@pytest.fixture
def isolated(tmp_path, monkeypatch, db_isolated):
    # accounts/transactions are DB-backed now (plan 3 task 4) — db_isolated (above) redirects
    # the process-wide DB singleton, so the fresh AccountStore()/TransactionStore() instances
    # wired below resolve against it with no per-instance path-sync needed.
    monkeypatch.setattr(svc, "grants", GrantStore())
    monkeypatch.setattr(svc, "vest_events", VestEventStore())
    monkeypatch.setattr(svc, "instruments", InstrumentStore())
    monkeypatch.setattr(svc, "positions", PositionStore())
    monkeypatch.setattr(lots_service, "instruments", InstrumentStore())
    monkeypatch.setattr(ledger_service, "accounts", AccountStore())
    monkeypatch.setattr(ledger_service, "transactions", TransactionStore())
    monkeypatch.setattr(svc, "base_currency", lambda: "SGD")
    monkeypatch.setattr(lots_service, "base_currency", lambda: "SGD")
    monkeypatch.setattr(ledger_service, "base_currency", lambda: "SGD")
    monkeypatch.setattr("backend.core.fx.base_currency", lambda: "SGD")
    monkeypatch.setattr("backend.services.equity_release.base_currency", lambda: "SGD")
    monkeypatch.setattr(lots_service, "_load_rates", lambda: {"SGD": 1.0})
    monkeypatch.setattr(
        "backend.services.ledger.to_base",
        lambda a, c, b=None, rates=None: round(float(a or 0), 2),
    )
    monkeypatch.setattr(svc, "to_base", lambda a, c, b=None, rates=None: round(float(a or 0), 2))
    return tmp_path


def _seed(*, release_required: bool):
    inst = InstrumentStore().create(
        {
            "symbol": "ONWAI",
            "name": "ONWAI Corp",
            "quote_currency": "SGD",
            "price_source_id": "ONWAI",
            "price_source": "yahoo",
        }
    )
    acct = AccountStore().create(
        {"name": "Equity Account", "kind": "asset", "type": "investment", "currency": "SGD"}
    )
    svc.apply_equity_change(
        {
            "type": "grant_create",
            "instrument_id": inst["id"],
            "account_id": acct["id"],
            "grant_date": "2024-03-15",
            "total_units": 600.0,
            "tax_event": "at_vest",
            "release_required": release_required,
            "tranches": [
                {"vest_date": "2025-03-15", "units": 300.0},
                {"vest_date": "2026-03-15", "units": 300.0},
            ],
        }
    )
    return inst, acct, GrantStore().all()[0]


def _vest(grant, tranche_idx=0, **extra):
    return svc.apply_equity_change(
        {
            "type": "vest_apply",
            "grant_id": grant["id"],
            "tranche_id": grant["tranches"][tranche_idx]["id"],
            "fmv_per_unit": 30.0,
            "fmv_currency": "SGD",
            "vest_date": "2025-03-15",
            **extra,
        }
    )


def _positions_by_role(grant_id, instrument_id):
    """Positions for this instrument keyed by their GRANT ROLE.

    Since the ownership/liquidity split (ADR 2026-07-31-split-position-bucket) the pending
    and liquid siblings are BOTH `vesting_state="vested"` — they are told apart by which
    position the grant points at, never by a stored value. Keying this helper on identity
    is the same discipline the service uses.
    """
    grant = GrantStore().find_by_id(grant_id) or {}
    locked_id = grant.get("locked_position_id")
    pending_id = grant.get("pending_position_id")
    out = {}
    for pos in PositionStore().all():
        if pos.get("instrument_id") != instrument_id:
            continue
        if pos["id"] == locked_id:
            out["locked"] = pos
        elif pos["id"] == pending_id:
            out["pending"] = pos
        else:
            out["liquid"] = pos
    return out


# ── 1. Opt-in: the default path must not change ──────────────────────────────────────


def test_grant_without_release_required_vests_straight_to_liquid(isolated):
    """The regression guard for every existing grant: one step, lot immediately, no pending."""
    inst, _acct, grant = _seed(release_required=False)
    result = _vest(grant)

    assert result["lot_id"] is not None
    assert result["pending_release"] is False

    roles = _positions_by_role(grant["id"], inst["id"])
    assert "pending" not in roles, "a one-step vest must never create a pending position"
    assert roles["liquid"]["quantity"] == pytest.approx(300.0)
    assert GrantStore().find_by_id(grant["id"]).get("pending_position_id") is None
    event = VestEventStore().find_by_id(result["vest_event_id"])
    assert event["released_on"] == "2025-03-15"
    assert release_svc.pending_releases() == []


def test_release_is_refused_when_the_grant_has_no_release_step(isolated):
    _inst, _acct, grant = _seed(release_required=False)
    ev_id = _vest(grant)["vest_event_id"]

    with pytest.raises(ValidationError, match="already released"):
        svc.apply_equity_change({"type": "release_apply", "vest_event_id": ev_id})


def test_legacy_lotted_vest_can_record_release_without_moving_shares(isolated):
    _inst, _acct, grant = _seed(release_required=False)
    vest = _vest(grant)
    event_id = vest["vest_event_id"]
    VestEventStore().update_event(
        event_id,
        {"pending_release": None, "released_on": None},
    )
    lots_before = LotStore().all()
    quantities_before = {
        row["id"]: float(row.get("quantity") or 0) for row in PositionStore().all()
    }

    pending = release_svc.pending_releases()

    assert [row["vest_event_id"] for row in pending] == [event_id]
    assert pending[0]["release_mode"] == "record_only"

    result = svc.apply_equity_change(
        {
            "type": "release_apply",
            "vest_event_id": event_id,
            "release_date": "2025-04-20",
        }
    )

    assert result["release_mode"] == "record_only"
    assert result["released_on"] == "2025-04-20"
    assert LotStore().all() == lots_before
    assert {
        row["id"]: float(row.get("quantity") or 0) for row in PositionStore().all()
    } == quantities_before
    assert release_svc.pending_releases() == []


# ── 2. The pending window ────────────────────────────────────────────────────────────


def test_vest_parks_units_in_pending_position_and_creates_no_lot(isolated):
    inst, _acct, grant = _seed(release_required=True)
    result = _vest(grant, expected_release_date="2025-04-15")

    assert result["pending_release"] is True
    assert result["lot_id"] is None, "undelivered shares must not have a lot — a lot is sellable"

    roles = _positions_by_role(grant["id"], inst["id"])
    assert roles["pending"]["quantity"] == pytest.approx(300.0)
    assert roles["locked"]["quantity"] == pytest.approx(300.0), "locked still decremented"
    assert "liquid" not in roles or roles["liquid"]["quantity"] == pytest.approx(0.0)
    assert LotStore().all() == []


def test_no_income_transaction_is_written_at_vest(isolated):
    """A vest books no ledger row, delivery lag or not — it hands over SHARES, never
    cash (ADR 2026-09-22-a-vest-books-no-income-transaction). The compensation fact is
    kept on the vest event, which is what equity_report reads."""
    _inst, _acct, grant = _seed(release_required=True)
    result = _vest(grant)

    assert result["txn_id"] is None
    assert TransactionStore().all() == []
    ev = VestEventStore().all()[0]
    assert ev["date"] == "2025-03-15"
    assert ev["gross_gain_native"] == pytest.approx(300.0 * 30.0)


def test_undo_still_reverses_a_legacy_vests_income_row(isolated):
    """Vests recorded BEFORE 2026-09-22 carry a real txn_id. Dropping the income row from
    the write path must not strand those: undo still soft-deletes the old transaction."""
    from backend.db.transaction_store import TransactionStore as _TS
    from backend.services import ledger as ledger_service

    _inst, acct, grant = _seed(release_required=False)
    result = _vest(grant)
    ev_id = result["vest_event_id"]

    # Simulate a pre-change event: a real income txn, linked from the vest event.
    legacy = ledger_service.create_transaction(
        {
            "date": "2025-03-15",
            "kind": "income",
            "description": "RSU vest — ONWAI",
            "to_account_id": acct["id"],
            "to_amount": 9000.0,
            "to_currency": "SGD",
            "tags": ["vest"],
        }
    )
    svc.vest_events.update_event(ev_id, {"txn_id": legacy["id"]})
    assert svc.vest_events.find_by_id(ev_id)["txn_id"] == legacy["id"], "link must be set"
    assert not (_TS().find_by_id(legacy["id"]) or {}).get("deleted"), "live before the undo"

    svc.apply_equity_change({"type": "vest_undo", "vest_event_id": ev_id})

    txn = _TS().find_by_id(legacy["id"])
    assert txn is None or txn.get("deleted") is True, "a legacy vest's income row must reverse"


def test_pending_releases_lists_the_wait_and_flags_overdue_only_when_dated(isolated):
    _inst, _acct, grant = _seed(release_required=True)
    _vest(grant, expected_release_date="2025-04-15")
    _vest(grant, tranche_idx=1, vest_date="2026-03-15")  # no expected date

    rows = release_svc.pending_releases()
    assert len(rows) == 2
    dated = next(r for r in rows if r["expected_release_date"] == "2025-04-15")
    undated = next(r for r in rows if r["expected_release_date"] is None)
    assert dated["overdue"] is True, "an expected date in the past is overdue"
    assert undated["overdue"] is False, "no expected date means unknown, never late"
    assert dated["units"] == pytest.approx(300.0)


def test_pending_position_quantity_cannot_be_hand_edited(isolated):
    """It mirrors grant state; a hand-edit would desync it from the vest events."""
    inst, _acct, grant = _seed(release_required=True)
    _vest(grant)
    pending_id = _positions_by_role(grant["id"], inst["id"])["pending"]["id"]

    with pytest.raises(ValidationError, match="managed by equity grant"):
        positions_service.update_position(pending_id, {"quantity": 999.0})


# ── 3. Release: shares move, money does not ──────────────────────────────────────────


def test_release_moves_shares_to_liquid_at_vest_date_basis(isolated):
    inst, _acct, grant = _seed(release_required=True)
    ev_id = _vest(grant)["vest_event_id"]
    txns_before = len(TransactionStore().all())

    result = svc.apply_equity_change(
        {"type": "release_apply", "vest_event_id": ev_id, "release_date": "2025-04-20"}
    )

    roles = _positions_by_role(grant["id"], inst["id"])
    assert roles["pending"]["quantity"] == pytest.approx(0.0)
    assert roles["liquid"]["quantity"] == pytest.approx(300.0)

    lot = LotStore().find_by_id(result["lot_id"])
    assert lot["acquired_at"] == "2025-03-15", "holding period runs from VEST, not delivery"
    assert lot["unit_cost"] == pytest.approx(30.0), "basis is vest-date FMV, not release-day"
    assert lot["original_qty"] == pytest.approx(300.0)
    assert lot["remaining_qty"] == pytest.approx(300.0), "delivered shares are fully sellable"

    assert len(TransactionStore().all()) == txns_before, "a release must write NO transaction"

    ev = VestEventStore().find_by_id(ev_id)
    assert ev["pending_release"] is False
    assert ev["released_on"] == "2025-04-20"
    assert ev["lot_id"] == result["lot_id"]
    assert release_svc.pending_releases() == []


def test_release_cannot_predate_the_vest(isolated):
    _inst, _acct, grant = _seed(release_required=True)
    ev_id = _vest(grant)["vest_event_id"]

    with pytest.raises(ValidationError, match="cannot be before the vest date"):
        svc.apply_equity_change(
            {"type": "release_apply", "vest_event_id": ev_id, "release_date": "2025-01-01"}
        )


def test_releasing_twice_is_refused(isolated):
    _inst, _acct, grant = _seed(release_required=True)
    ev_id = _vest(grant)["vest_event_id"]
    svc.apply_equity_change({"type": "release_apply", "vest_event_id": ev_id})

    with pytest.raises(ValidationError, match="already released"):
        svc.apply_equity_change({"type": "release_apply", "vest_event_id": ev_id})


# ── 4. Undo, in both shapes ──────────────────────────────────────────────────────────


def test_undoing_a_pending_vest_drains_the_pending_position(isolated):
    inst, _acct, grant = _seed(release_required=True)
    ev_id = _vest(grant)["vest_event_id"]

    svc.apply_equity_change({"type": "vest_undo", "vest_event_id": ev_id})

    roles = _positions_by_role(grant["id"], inst["id"])
    assert roles["pending"]["quantity"] == pytest.approx(0.0)
    assert roles["locked"]["quantity"] == pytest.approx(600.0), "units returned to locked"
    assert LotStore().all() == []
    assert VestEventStore().find_by_id(ev_id) is None
    tranche = GrantStore().find_by_id(grant["id"])["tranches"][0]
    assert tranche["vested"] is False


def test_undoing_a_released_vest_deletes_the_delivered_lot(isolated):
    inst, _acct, grant = _seed(release_required=True)
    ev_id = _vest(grant)["vest_event_id"]
    svc.apply_equity_change({"type": "release_apply", "vest_event_id": ev_id})

    svc.apply_equity_change({"type": "vest_undo", "vest_event_id": ev_id})

    roles = _positions_by_role(grant["id"], inst["id"])
    assert roles["locked"]["quantity"] == pytest.approx(600.0)
    assert roles["liquid"]["quantity"] == pytest.approx(0.0)
    assert LotStore().all() == []


# ── 5. Grant surface ─────────────────────────────────────────────────────────────────


def test_hydrated_grant_reports_pending_units_and_tranche_status(isolated):
    _inst, _acct, grant = _seed(release_required=True)
    _vest(grant)

    hydrated = next(g for g in svc.hydrated_grants() if g["id"] == grant["id"])
    assert hydrated["pending_release_units"] == pytest.approx(300.0)
    assert hydrated["vested_units"] == pytest.approx(300.0), "pending units are still vested"
    assert hydrated["tranches"][0]["status"] == "pending_release"

    svc.apply_equity_change(
        {"type": "release_apply", "vest_event_id": VestEventStore().all()[0]["id"]}
    )
    hydrated = next(g for g in svc.hydrated_grants() if g["id"] == grant["id"])
    assert hydrated["pending_release_units"] == pytest.approx(0.0)
    assert hydrated["tranches"][0]["status"] == "vested"


def test_flipping_release_required_does_not_rewrite_recorded_vests(isolated):
    """Only FUTURE vests change shape — a flag flip must never move delivered shares."""
    inst, _acct, grant = _seed(release_required=False)
    _vest(grant)

    svc.apply_equity_change(
        {"type": "grant_update", "grant_id": grant["id"], "release_required": True}
    )

    roles = _positions_by_role(grant["id"], inst["id"])
    assert roles["liquid"]["quantity"] == pytest.approx(300.0)
    assert release_svc.pending_releases() == [], "an already-delivered vest stays delivered"

    # ...but the NEXT vest parks.
    grant = GrantStore().find_by_id(grant["id"])
    _vest(grant, tranche_idx=1, vest_date="2026-03-15")
    assert len(release_svc.pending_releases()) == 1


# ── 5. Undelivered units still have a basis: the FMV they vested at ──────────────────


def test_pending_units_carry_the_vest_date_fmv_as_cost_basis(isolated):
    """Scar (2026-09-23): KSTL's 15 Sep tranche vested with release required. The pending
    position parked 488.75 units with ``cost_basis=None`` even though the vest event knew
    the FMV, so the Holdings row — which merges the pending units with the broker-held
    shares of the same instrument — read "—" for P&L on the WHOLE holding. A parked unit
    is not sellable (no lot), but its basis is known: the vest-date FMV."""
    inst, _acct, grant = _seed(release_required=True)
    _vest(grant)

    pending = _positions_by_role(grant["id"], inst["id"])["pending"]
    assert pending["cost_basis"] == pytest.approx(30.0)
    assert pending["cost_currency"] == "SGD"
    assert LotStore().all() == [], "a basis is not a lot — the units are still not sellable"

    _vest(grant, tranche_idx=1, vest_date="2026-03-15", fmv_per_unit=40.0)
    pending = _positions_by_role(grant["id"], inst["id"])["pending"]
    assert pending["quantity"] == pytest.approx(600.0)
    assert pending["cost_basis"] == pytest.approx(35.0), "quantity-weighted across parked vests"


def test_pending_basis_follows_the_units_out_on_release_and_undo(isolated):
    inst, _acct, grant = _seed(release_required=True)
    first = _vest(grant)["vest_event_id"]
    second = _vest(grant, tranche_idx=1, vest_date="2026-03-15", fmv_per_unit=40.0)["vest_event_id"]

    svc.apply_equity_change({"type": "release_apply", "vest_event_id": first})
    pending = _positions_by_role(grant["id"], inst["id"])["pending"]
    assert pending["quantity"] == pytest.approx(300.0)
    assert pending["cost_basis"] == pytest.approx(40.0), "only the still-parked vest remains"

    svc.apply_equity_change({"type": "vest_undo", "vest_event_id": second})
    pending = _positions_by_role(grant["id"], inst["id"])["pending"]
    assert pending["quantity"] == pytest.approx(0.0)
    assert pending["cost_basis"] is None, "nothing parked, nothing to price"


def test_pending_basis_is_unknown_when_parked_vests_disagree_on_currency(isolated):
    """Averaging raw unit costs across currencies fabricates a number; unknown is honest."""
    inst, _acct, grant = _seed(release_required=True)
    _vest(grant)
    _vest(grant, tranche_idx=1, vest_date="2026-03-15", fmv_per_unit=25.0, fmv_currency="USD")

    pending = _positions_by_role(grant["id"], inst["id"])["pending"]
    assert pending["quantity"] == pytest.approx(600.0)
    assert pending["cost_basis"] is None


def _legacy_holding(inst, acct, qty, basis):
    """A pre-lots liquid holding as revision 202610111200 leaves it: the position plus ONE
    undated opening lot of exactly its quantity and basis."""
    pos = PositionStore().create(
        {
            "instrument_id": inst["id"],
            "account_id": acct["id"],
            "quantity": qty,
            "cost_basis": basis,
            "cost_currency": "SGD",
            "vesting_state": "vested",
        }
    )
    LotStore().create(
        {
            "position_id": pos["id"],
            "instrument_id": inst["id"],
            "account_id": acct["id"],
            "original_qty": qty,
            "unit_cost": basis,
            "cost_currency": "SGD",
            "kind": "opening",
        }
    )
    return pos


@pytest.mark.parametrize("release_required", [False, True])
@pytest.mark.parametrize("original_basis", [37.75, None])
def test_vest_into_unlotted_holding_preserves_snapshot_and_undo(
    isolated, release_required, original_basis
):
    inst, acct, grant = _seed(release_required=release_required)
    original_qty = 1.456311
    pos = _legacy_holding(inst, acct, original_qty, original_basis)
    transactions_before = TransactionStore().all()
    event_id = _vest(grant)["vest_event_id"]
    if release_required:
        svc.apply_equity_change({"type": "release_apply", "vest_event_id": event_id})

    holding = PositionStore().find_by_id(pos["id"])
    assert holding["quantity"] == pytest.approx(original_qty + 300)
    opening = next(lot for lot in LotStore().all() if lot["kind"] == "opening")
    assert opening["remaining_qty"] == original_qty
    assert opening["unit_cost"] == original_basis
    assert opening["acquired_at"] == ""
    assert not opening.get("txn_id")
    if original_basis is not None:
        assert holding["cost_basis"] == pytest.approx(
            (original_qty * original_basis + 300 * 30) / (original_qty + 300)
        )
    else:
        assert holding["cost_basis"] is None, "unknown opening cost cannot price the whole holding"

    svc.apply_equity_change({"type": "vest_undo", "vest_event_id": event_id})

    restored = PositionStore().find_by_id(pos["id"])
    assert restored["quantity"] == pytest.approx(original_qty)
    assert restored["cost_basis"] == original_basis
    assert restored["cost_currency"] == "SGD"
    assert LotStore().all() == [opening]
    assert TransactionStore().all() == transactions_before


def test_failed_release_rolls_back_and_keeps_the_opening_lot(isolated, monkeypatch):
    inst, acct, grant = _seed(release_required=True)
    pos = _legacy_holding(inst, acct, 2.5, 37.0)
    opening = LotStore().all()
    event_id = _vest(grant)["vest_event_id"]
    positions_before = PositionStore().all()
    events_before = VestEventStore().all()
    transactions_before = TransactionStore().all()
    original_insert = LotStore.insert

    def fail_grant_insert(self, row, *, conn=None):
        if row.get("kind") == "grant":
            assert self.lots_for_position(pos["id"], conn=conn)[0]["kind"] == "opening"
            raise RuntimeError("simulated grant insert failure")
        return original_insert(self, row, conn=conn)

    monkeypatch.setattr(LotStore, "insert", fail_grant_insert)
    with pytest.raises(RuntimeError, match="simulated grant insert failure"):
        svc.apply_equity_change({"type": "release_apply", "vest_event_id": event_id})

    assert LotStore().all() == opening
    assert PositionStore().all() == positions_before
    assert VestEventStore().all() == events_before
    assert TransactionStore().all() == transactions_before
