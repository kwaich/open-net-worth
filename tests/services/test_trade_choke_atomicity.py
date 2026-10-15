"""The trade-atomicity choke seam (storage plan 4b, task 4): the genuinely multi-table
investment writes — a dispose/acquire/dispose_undo (lots), a vest_apply/vest_undo/
release_apply (equity), an option open/close (options) — now compose through
``backend.db.engine.locked_unit_of_work`` exactly like the money chokes (plan 3,
``services/ledger.py``) and the planning chokes (plan 4a, ``tests/services/
test_planning_choke_atomicity.py``, which this file mirrors structurally).

A forced mid-op failure must roll back EVERY table the choke touches (never "lots
persists, the realized event never happens") and a vest's FMV-lot mint must not
re-enter ``apply_lot_change``'s own top-level transaction — doing so would try to
re-acquire the lots/positions locks already held by this thread and hang forever
(``threading.Lock`` is not reentrant). Every test that could hang on a real deadlock
bug runs its risky section on a background thread with a bounded ``join(timeout=...)``
— a regression must fail loud within seconds, never hang the suite (project
hang-safety rule)."""

from __future__ import annotations

import threading
import time

import pytest

from backend.db.account_store import AccountStore
from backend.db.automation_store import AutomationStore
from backend.db.engine import locked_unit_of_work
from backend.db.grant_store import GrantStore
from backend.db.instrument_store import InstrumentStore
from backend.db.lot_store import LotStore
from backend.db.option_contract_store import OptionContractStore
from backend.db.payee_store import PayeeStore
from backend.db.position_store import PositionStore
from backend.db.realized_event_store import RealizedEventStore
from backend.db.tables import grants as grants_tbl
from backend.db.tables import lots as lots_tbl
from backend.db.tables import option_contracts as option_contracts_tbl
from backend.db.tables import positions as positions_tbl
from backend.db.tables import realized_events as realized_tbl
from backend.db.tables import transactions as transactions_table
from backend.db.tables import vest_events as vest_events_tbl
from backend.db.transaction_store import TransactionStore
from backend.db.vest_event_store import VestEventStore
from backend.services import equity as equity_service
from backend.services import equity_release as equity_release_service
from backend.services import ledger as ledger_service
from backend.services import lots as lots_service
from backend.services import options as options_service


@pytest.fixture
def env(tmp_path, monkeypatch, db_isolated):
    """Isolated DB (``db_isolated``) + module-level store handles rebound onto it +
    identity FX at SGD base — mirrors tests/test_equity_service.py's own ``isolated``
    fixture (no ``options_service`` module-level store handles to rebind — it
    instantiates fresh per call)."""
    monkeypatch.setattr(equity_service, "grants", GrantStore())
    monkeypatch.setattr(equity_service, "vest_events", VestEventStore())
    monkeypatch.setattr(equity_service, "instruments", InstrumentStore())
    monkeypatch.setattr(equity_service, "positions", PositionStore())
    monkeypatch.setattr(lots_service, "instruments", InstrumentStore())
    monkeypatch.setattr(ledger_service, "accounts", AccountStore())
    monkeypatch.setattr(ledger_service, "transactions", TransactionStore())
    monkeypatch.setattr(equity_service, "base_currency", lambda: "SGD")
    monkeypatch.setattr(lots_service, "base_currency", lambda: "SGD")
    monkeypatch.setattr(ledger_service, "base_currency", lambda: "SGD")
    monkeypatch.setattr("backend.core.fx.base_currency", lambda: "SGD")
    monkeypatch.setattr(equity_release_service, "base_currency", lambda: "SGD")
    monkeypatch.setattr(lots_service, "_load_rates", lambda: {"SGD": 1.0})
    monkeypatch.setattr(
        "backend.services.ledger.to_base",
        lambda a, c, b=None, rates=None: round(float(a or 0), 2),
    )
    monkeypatch.setattr(
        equity_service, "to_base", lambda a, c, b=None, rates=None: round(float(a or 0), 2)
    )
    return tmp_path


def _seed_inst_and_acct(symbol="ACME"):
    inst = InstrumentStore().create(
        {
            "symbol": symbol,
            "name": f"{symbol} Corp",
            "quote_currency": "SGD",
            "price_source_id": symbol,
            "price_source": "yahoo",
        }
    )
    acct = AccountStore().create(
        {"name": "Brokerage", "kind": "asset", "type": "investment", "currency": "SGD"}
    )
    return inst, acct


def _seed_position(inst, acct, quantity=0.0):
    return PositionStore().create(
        {
            "instrument_id": inst["id"],
            "account_id": acct["id"],
            "quantity": quantity,
            "cost_currency": "SGD",
        }
    )


def _acquire(position_id, qty=10.0, unit_cost=100.0, acquired_at="2026-01-01", **extra):
    change = {
        "type": "acquire",
        "position_id": position_id,
        "quantity": qty,
        "unit_cost": unit_cost,
        "cost_currency": "SGD",
        "acquired_at": acquired_at,
        "kind": "buy",
    }
    change.update(extra)
    return lots_service.apply_lot_change(change)


def _dispose(position_id, qty=4.0, price=130.0, **extra):
    change = {
        "type": "dispose",
        "position_id": position_id,
        "quantity": qty,
        "proceeds_per_unit": price,
        "proceeds_currency": "SGD",
        "disposed_at": "2026-06-01",
    }
    change.update(extra)
    return lots_service.apply_lot_change(change)


def _make_grant_change(inst, acct, tranches=None, **extra):
    if tranches is None:
        tranches = [
            {"vest_date": "2025-03-15", "units": 300.0},
            {"vest_date": "2026-03-15", "units": 300.0},
        ]
    change = {
        "type": "grant_create",
        "instrument_id": inst["id"],
        "account_id": acct["id"],
        "grant_date": "2024-03-15",
        "total_units": sum(t["units"] for t in tranches),
        "tax_event": "at_vest",
        "tranches": tranches,
    }
    change.update(extra)
    return change


def _vest(grant, tranche_idx=0, **extra):
    change = {
        "type": "vest_apply",
        "grant_id": grant["id"],
        "tranche_id": grant["tranches"][tranche_idx]["id"],
        "fmv_per_unit": 30.0,
        "fmv_currency": "SGD",
        "vest_date": "2025-03-15",
    }
    change.update(extra)
    return equity_service.apply_equity_change(change)


def _txn(txn_id: str) -> dict:
    return {
        "id": txn_id,
        "date": "2026-06-01",
        "kind": "expense",
        "category": None,
        "source": "manual",
        "description": "test",
    }


def _run_bounded(target, *, timeout=10) -> list[BaseException]:
    """Run ``target`` on a background thread with a bounded join — a hang (a real
    self-deadlock regression) fails loud in seconds instead of hanging the suite."""
    errors: list[BaseException] = []

    def _wrapped():
        try:
            target()
        except BaseException as exc:  # pragma: no cover - failure path only
            errors.append(exc)

    t = threading.Thread(target=_wrapped)
    t.start()
    t.join(timeout=timeout)
    assert not t.is_alive(), "deadlock: the writer thread never finished"
    return errors


# ── 1. dispose: lots + positions + realized_events (+ transactions when a proceeds
#      leg is booked) ──────────────────────────────────────────────────────────────


def test_dispose_is_atomic_across_lots_positions_realized(env, monkeypatch):
    """A forced failure on the realized-event append (injected AFTER bulk_consume, per
    the task brief) must roll back the lot consumption AND the position resync too —
    one transaction, not three independent writes."""
    inst, acct = _seed_inst_and_acct()
    pos = _seed_position(inst, acct)
    acquired = _acquire(pos["id"], qty=10.0, unit_cost=100.0)
    lot_id = acquired["lot_id"]
    original_remaining = LotStore().find_by_id(lot_id)["remaining_qty"]
    original_qty = PositionStore().find_by_id(pos["id"])["quantity"]

    def _boom(*_a, **_k):
        raise RuntimeError("simulated failure mid-dispose")

    monkeypatch.setattr(RealizedEventStore, "append", _boom)

    with pytest.raises(RuntimeError, match="simulated failure mid-dispose"):
        _dispose(pos["id"], qty=4.0, price=130.0)

    assert LotStore().find_by_id(lot_id)["remaining_qty"] == original_remaining
    assert RealizedEventStore().query_events(position_id=pos["id"]) == []
    assert PositionStore().find_by_id(pos["id"])["quantity"] == original_qty


def test_dispose_with_proceeds_leg_rolls_back_the_transaction_too(env, monkeypatch):
    """A forced failure AFTER the cash leg is booked (the position resync, the LAST
    write in the sequence) must roll back the lot consumption, the realized event, AND
    the just-created proceeds-deposit transaction — proof the ledger leg rides inside
    the SAME transaction as the rest of the disposal, not a separate commit."""
    inst, acct = _seed_inst_and_acct()
    cash_acct = AccountStore().create(
        {"name": "Cash", "kind": "asset", "type": "cash", "currency": "SGD"}
    )
    pos = _seed_position(inst, acct)
    acquired = _acquire(pos["id"], qty=10.0, unit_cost=100.0)
    lot_id = acquired["lot_id"]
    original_remaining = LotStore().find_by_id(lot_id)["remaining_qty"]

    def _boom(*_a, **_k):
        raise RuntimeError("simulated failure after the cash leg")

    monkeypatch.setattr(lots_service.position_figures, "rederive", _boom)

    with pytest.raises(RuntimeError, match="simulated failure after the cash leg"):
        _dispose(pos["id"], qty=4.0, price=130.0, proceeds_account_id=cash_acct["id"])

    assert LotStore().find_by_id(lot_id)["remaining_qty"] == original_remaining
    assert RealizedEventStore().query_events(position_id=pos["id"]) == []
    assert TransactionStore().all() == [], "the proceeds-deposit txn must not have persisted"


def test_dispose_succeeds_and_persists_all_four_when_not_interrupted(env):
    """Sanity/regression companion: the happy path still consumes the lot, books the
    realized event, deposits proceeds, and resyncs the position in one call, unchanged
    by the conn= composition."""
    inst, acct = _seed_inst_and_acct()
    cash_acct = AccountStore().create(
        {"name": "Cash", "kind": "asset", "type": "cash", "currency": "SGD"}
    )
    pos = _seed_position(inst, acct)
    acquired = _acquire(pos["id"], qty=10.0, unit_cost=100.0)
    lot_id = acquired["lot_id"]

    result = _dispose(pos["id"], qty=4.0, price=130.0, proceeds_account_id=cash_acct["id"])

    assert result["ok"] is True
    assert result["realized_gain"] == pytest.approx(120.0)  # 4 × (130 − 100)
    assert LotStore().find_by_id(lot_id)["remaining_qty"] == pytest.approx(6.0)
    events = RealizedEventStore().query_events(position_id=pos["id"])
    assert len(events) == 1 and events[0]["quantity"] == pytest.approx(4.0)
    assert PositionStore().find_by_id(pos["id"])["quantity"] == pytest.approx(6.0)
    txns = TransactionStore().all()
    assert len(txns) == 1 and txns[0]["to_account_id"] == cash_acct["id"]
    assert result["cash_leg_id"] == txns[0]["id"]


# ── 2. acquire: lots + positions ────────────────────────────────────────────────────


def test_acquire_is_atomic_across_lots_and_positions(env, monkeypatch):
    """A forced failure on the position resync must roll back the lot insert too — no
    orphaned lot with no matching position aggregate."""
    inst, acct = _seed_inst_and_acct()
    pos = _seed_position(inst, acct)

    def _boom(*_a, **_k):
        raise RuntimeError("simulated failure mid-acquire")

    monkeypatch.setattr(PositionStore, "write_figures", _boom)

    with pytest.raises(RuntimeError, match="simulated failure mid-acquire"):
        _acquire(pos["id"], qty=10.0, unit_cost=100.0)

    assert LotStore().all() == []
    assert PositionStore().find_by_id(pos["id"])["quantity"] == 0.0


def test_acquire_succeeds_and_persists_both_when_not_interrupted(env):
    inst, acct = _seed_inst_and_acct()
    pos = _seed_position(inst, acct)

    result = _acquire(pos["id"], qty=10.0, unit_cost=100.0)

    assert result["ok"] is True
    assert len(LotStore().all()) == 1
    assert PositionStore().find_by_id(pos["id"])["quantity"] == pytest.approx(10.0)


# ── 3. dispose_undo: lots + positions + realized_events + transactions ─────────────


def _dispose_with_linked_txn(pos, cash_acct, **extra):
    """dispose_undo reverses the event's OWN ``txn_id`` link — a caller-supplied id
    (e.g. an already-existing import-derived transaction), NOT proceeds_account_id's
    auto-deposited leg (that one is a separate, unlinked transaction — cash_leg_id in
    the dispose tests above; a pre-existing gap, out of Task 4's atomicity scope).
    Books a real transaction first and passes its id as change["txn_id"] so the
    reversal path is genuinely exercised."""
    txn = ledger_service.create_transaction(
        {
            "date": "2026-06-01",
            "kind": "income",
            "description": "linked proceeds",
            "to_account_id": cash_acct["id"],
            "to_amount": 520.0,
            "to_currency": "SGD",
        }
    )
    disposal = _dispose(pos["id"], qty=4.0, price=130.0, txn_id=txn["id"], **extra)
    return txn, disposal


def test_dispose_undo_is_atomic_across_all_four_tables(env, monkeypatch):
    inst, acct = _seed_inst_and_acct()
    cash_acct = AccountStore().create(
        {"name": "Cash", "kind": "asset", "type": "cash", "currency": "SGD"}
    )
    pos = _seed_position(inst, acct)
    acquired = _acquire(pos["id"], qty=10.0, unit_cost=100.0)
    lot_id = acquired["lot_id"]
    txn, disposal = _dispose_with_linked_txn(pos, cash_acct)
    event_id = disposal["event_id"]

    remaining_before_undo = LotStore().find_by_id(lot_id)["remaining_qty"]
    qty_before_undo = PositionStore().find_by_id(pos["id"])["quantity"]

    def _boom(*_a, **_k):
        raise RuntimeError("simulated failure mid-undo")

    monkeypatch.setattr(RealizedEventStore, "delete", _boom)

    with pytest.raises(RuntimeError, match="simulated failure mid-undo"):
        lots_service.apply_lot_change({"type": "dispose_undo", "event_id": event_id})

    assert LotStore().find_by_id(lot_id)["remaining_qty"] == remaining_before_undo
    assert PositionStore().find_by_id(pos["id"])["quantity"] == qty_before_undo
    assert RealizedEventStore().find_by_id(event_id) is not None
    assert TransactionStore().find_by_id(txn["id"])["deleted"] is False


def test_dispose_undo_succeeds_and_reverses_everything_when_not_interrupted(env):
    inst, acct = _seed_inst_and_acct()
    cash_acct = AccountStore().create(
        {"name": "Cash", "kind": "asset", "type": "cash", "currency": "SGD"}
    )
    pos = _seed_position(inst, acct)
    acquired = _acquire(pos["id"], qty=10.0, unit_cost=100.0)
    lot_id = acquired["lot_id"]
    txn, disposal = _dispose_with_linked_txn(pos, cash_acct)

    result = lots_service.apply_lot_change(
        {"type": "dispose_undo", "event_id": disposal["event_id"]}
    )

    assert result["ok"] is True
    assert LotStore().find_by_id(lot_id)["remaining_qty"] == pytest.approx(10.0)
    assert PositionStore().find_by_id(pos["id"])["quantity"] == pytest.approx(10.0)
    assert RealizedEventStore().find_by_id(disposal["event_id"]) is None
    assert TransactionStore().find_by_id(txn["id"])["deleted"] is True


def test_dispose_undo_does_not_self_deadlock_on_its_linked_cash_leg(env):
    """The live-instance hang of 2026-09-21 20:50: _undo_dispose holds the ``transactions``
    lock for its whole locked_unit_of_work and composes bulk_delete_transactions(conn=)
    into it. That choke's Starting-Balance pre-scan reads the row through the store's
    ``find_by_id`` — which, since 918bfb5ea, took the SAME non-reentrant table lock again
    when called without ``conn``. The server thread sat at 0% CPU forever and the port
    stopped answering. A regression must fail within seconds, never hang the suite."""
    inst, acct = _seed_inst_and_acct()
    cash_acct = AccountStore().create(
        {"name": "Cash", "kind": "asset", "type": "cash", "currency": "SGD"}
    )
    pos = _seed_position(inst, acct)
    _acquire(pos["id"], qty=10.0, unit_cost=100.0)
    txn, disposal = _dispose_with_linked_txn(pos, cash_acct)

    result_box: dict = {}
    errors = _run_bounded(
        lambda: result_box.update(
            result=lots_service.apply_lot_change(
                {"type": "dispose_undo", "event_id": disposal["event_id"]}
            )
        )
    )

    assert errors == []
    assert result_box["result"]["ok"] is True
    assert TransactionStore().find_by_id(txn["id"])["deleted"] is True


# ── 3a. automation-event publish timing for the OTHER composed choke (storage plan 4d
#       task 2): bulk_delete_transactions(conn=...) — dispose_undo's reversal of its
#       linked cash leg — must defer/skip its publish exactly like create_transaction's
#       own composed path above, proving the fix covers BOTH conn=-accepting ledger writes.


def test_dispose_undo_composed_commit_publishes_delete_event_exactly_once(env, monkeypatch):
    inst, acct = _seed_inst_and_acct()
    cash_acct = AccountStore().create(
        {"name": "Cash", "kind": "asset", "type": "cash", "currency": "SGD"}
    )
    pos = _seed_position(inst, acct)
    _acquire(pos["id"], qty=10.0, unit_cost=100.0)
    txn, disposal = _dispose_with_linked_txn(pos, cash_acct)

    calls: list[tuple[str, list[dict]]] = []
    monkeypatch.setattr(
        ledger_service,
        "_publish_committed_transaction_events",
        lambda operation, rows, **_k: calls.append((operation, [dict(r) for r in rows])),
    )

    result = lots_service.apply_lot_change(
        {"type": "dispose_undo", "event_id": disposal["event_id"]}
    )

    assert result["ok"] is True
    assert len(calls) == 1
    operation, rows = calls[0]
    assert operation == "delete"
    assert len(rows) == 1
    assert rows[0]["id"] == txn["id"]


def test_dispose_undo_composed_rollback_publishes_no_delete_event(env, monkeypatch):
    inst, acct = _seed_inst_and_acct()
    cash_acct = AccountStore().create(
        {"name": "Cash", "kind": "asset", "type": "cash", "currency": "SGD"}
    )
    pos = _seed_position(inst, acct)
    _acquire(pos["id"], qty=10.0, unit_cost=100.0)
    txn, disposal = _dispose_with_linked_txn(pos, cash_acct)

    calls: list = []
    monkeypatch.setattr(
        ledger_service,
        "_publish_committed_transaction_events",
        lambda *a, **k: calls.append((a, k)),
    )

    def _boom(*_a, **_k):
        raise RuntimeError("simulated failure mid-undo")

    monkeypatch.setattr(RealizedEventStore, "delete", _boom)

    with pytest.raises(RuntimeError, match="simulated failure mid-undo"):
        lots_service.apply_lot_change({"type": "dispose_undo", "event_id": disposal["event_id"]})

    assert calls == []
    assert TransactionStore().find_by_id(txn["id"])["deleted"] is False


# ── 3b. delete (a lot): lots + positions (sweep finding — not in the brief's list) ──


def test_delete_lot_is_atomic_across_lots_and_positions(env, monkeypatch):
    """delete_lot removes an unconsumed lot AND resyncs the position aggregate — a
    2-table write the brief's binding list doesn't name explicitly (found via the
    task's SWEEP instruction)."""
    inst, acct = _seed_inst_and_acct()
    pos = _seed_position(inst, acct)
    acquired = _acquire(pos["id"], qty=10.0, unit_cost=100.0)
    lot_id = acquired["lot_id"]

    def _boom(*_a, **_k):
        raise RuntimeError("simulated failure mid-delete-lot")

    monkeypatch.setattr(PositionStore, "write_figures", _boom)

    with pytest.raises(RuntimeError, match="simulated failure mid-delete-lot"):
        lots_service.apply_lot_change({"type": "delete", "lot_id": lot_id})

    assert LotStore().find_by_id(lot_id) is not None
    assert PositionStore().find_by_id(pos["id"])["quantity"] == pytest.approx(10.0)


def test_delete_lot_succeeds_when_not_interrupted(env):
    inst, acct = _seed_inst_and_acct()
    pos = _seed_position(inst, acct)
    acquired = _acquire(pos["id"], qty=10.0, unit_cost=100.0)

    result = lots_service.apply_lot_change({"type": "delete", "lot_id": acquired["lot_id"]})

    assert result["ok"] is True
    assert LotStore().find_by_id(acquired["lot_id"]) is None
    assert PositionStore().find_by_id(pos["id"])["quantity"] == 0.0


# ── 4. vest_apply: grants + lots + positions + vest_events (NO transactions — a vest
#       books no ledger row, ADR 2026-09-22-a-vest-books-no-income-transaction) ─────────


def test_vest_apply_does_not_self_deadlock_on_lot_mint(env):
    """THE central risk of this task: _vest_apply composes (grants, lots, positions,
    transactions, vest_events) in ONE locked_unit_of_work and mints the FMV lot via a
    conn=-threaded core — never by re-entering apply_lot_change's own top-level
    transaction, which would try to re-acquire the lots/positions locks already held by
    this thread and hang forever (threading.Lock is not reentrant). A regression here
    must fail loud within seconds, never hang the suite."""
    inst, acct = _seed_inst_and_acct()
    equity_service.apply_equity_change(_make_grant_change(inst, acct))
    grant = GrantStore().all()[0]

    result_box: dict = {}
    errors = _run_bounded(lambda: result_box.update(result=_vest(grant)))

    assert errors == []
    assert result_box["result"]["ok"] is True
    assert len(LotStore().all()) == 1


def test_vest_apply_is_atomic_across_grants_lots_positions_vest_events(env, monkeypatch):
    """Force the LAST step (vest_events.append) to raise; assert the grant's tranche
    status, the vest_events store, the FMV lot, and the locked/liquid position rows are
    ALL unchanged — the whole sequence is one transaction. Injected at the tail rather
    than the old income leg, which no longer exists (ADR
    2026-09-22-a-vest-books-no-income-transaction): failing at the end is the strongest
    form of the claim, since every other write has already run."""
    inst, acct = _seed_inst_and_acct()
    equity_service.apply_equity_change(_make_grant_change(inst, acct))
    grant = GrantStore().all()[0]
    locked_id = grant["locked_position_id"]
    locked_qty_before = PositionStore().find_by_id(locked_id)["quantity"]

    def _boom(*_a, **_k):
        raise RuntimeError("simulated failure mid-vest")

    monkeypatch.setattr(VestEventStore, "append", _boom)

    with pytest.raises(RuntimeError, match="simulated failure mid-vest"):
        _vest(grant)

    assert LotStore().all() == []
    assert VestEventStore().all() == []
    assert PositionStore().find_by_id(locked_id)["quantity"] == locked_qty_before
    g_after = GrantStore().find_by_id(grant["id"])
    assert all(not t.get("vested") for t in g_after["tranches"])
    assert g_after["status"] == "active"
    # No liquid sibling position should have been created either.
    liquid = [
        p
        for p in PositionStore().all()
        if p["id"] != locked_id and p.get("instrument_id") == inst["id"]
    ]
    # A liquid position SHELL may have been pre-resolved (Task 4's inert-if-orphaned
    # design — see equity_release.find_or_create_liquid_position), but it must hold
    # ZERO quantity and own no lot: the economic facts never committed.
    for p in liquid:
        assert p["quantity"] == 0.0
    assert LotStore().all() == []


def test_vest_apply_succeeds_and_persists_all_four_when_not_interrupted(env):
    inst, acct = _seed_inst_and_acct()
    equity_service.apply_equity_change(_make_grant_change(inst, acct))
    grant = GrantStore().all()[0]
    tranche_id = grant["tranches"][0]["id"]

    result = _vest(grant)

    assert result["ok"] is True
    assert TransactionStore().all() == []  # a vest books no ledger row
    assert len(LotStore().all()) == 1
    assert len(VestEventStore().all()) == 1
    locked_pos = PositionStore().find_by_id(grant["locked_position_id"])
    assert locked_pos["quantity"] == pytest.approx(300.0)  # 600 total − 300 vested
    g_after = GrantStore().find_by_id(grant["id"])
    vested_t = next(t for t in g_after["tranches"] if t["id"] == tranche_id)
    assert vested_t["vested"] is True


def test_vest_apply_deferred_tax_is_atomic_across_grants_lots_positions(env, monkeypatch):
    """Kept as the deferred-grant twin of the test above. Since 2026-09-22 NO vest writes
    a ledger row, so both paths compose identically — this pins that a deferred grant did
    not acquire some other behaviour on the way."""
    inst, acct = _seed_inst_and_acct()
    equity_service.apply_equity_change(_make_grant_change(inst, acct, tax_event="deferred"))
    grant = GrantStore().all()[0]

    def _boom(*_a, **_k):
        raise RuntimeError("simulated failure mid-deferred-vest")

    monkeypatch.setattr(VestEventStore, "append", _boom)

    with pytest.raises(RuntimeError, match="simulated failure mid-deferred-vest"):
        _vest(grant)

    assert LotStore().all() == []
    assert TransactionStore().all() == []
    g_after = GrantStore().find_by_id(grant["id"])
    assert all(not t.get("vested") for t in g_after["tranches"])


# ── 4b. automation-event publish timing (storage plan 4d task 2 — the self-deadlock fix):
#       create_transaction(conn=...)'s automation-event publish must defer to AFTER the
#       OUTER composed transaction commits, never fire while it is still open, and never
#       fire at all if it rolls back.
#
#       VEHICLE CHANGE (2026-09-22): vest_apply used to be the composed choke that carried
#       a create_transaction(conn=...) call, and these tests rode it. A vest now books no
#       ledger row at all (ADR 2026-09-22-a-vest-books-no-income-transaction), so they ride
#       the structurally identical acquire-with-funding path instead: _apply_acquire opens
#       locked_unit_of_work(lots, positions, txns, txn_audit), mints the lot, and books the
#       "Share purchase" debit through _book_funding_debit -> create_transaction(conn=conn).
#       Same mechanism, same deferral, a choke that still exercises it.


def test_composed_funding_leg_commit_publishes_automation_event_exactly_once(env, monkeypatch):
    """The success half: a composed cash leg (create_transaction(conn=conn) inside
    _apply_acquire) must still publish its automation event — exactly once, carrying the
    created row — after deferring it past the outer transaction's commit."""
    inst, acct = _seed_inst_and_acct()
    pos = _seed_position(inst, acct)

    calls: list[tuple[str, list[dict]]] = []
    monkeypatch.setattr(
        ledger_service,
        "_publish_committed_transaction_events",
        lambda operation, rows, **_k: calls.append((operation, [dict(r) for r in rows])),
    )

    result = _acquire(pos["id"], qty=10.0, unit_cost=100.0, funding_account_id=acct["id"])

    assert result["ok"] is True
    assert result["cash_leg_id"] is not None, "the funding debit must have been booked"
    assert len(calls) == 1
    operation, rows = calls[0]
    assert operation == "create"
    assert len(rows) == 1
    assert rows[0]["id"] == result["cash_leg_id"]


def test_composed_funding_leg_rollback_publishes_no_automation_event(env, monkeypatch):
    """The phantom-txn-on-rollback half: the cash leg fully runs and would have registered
    a publish, but a LATER step in the SAME composed transaction (the LotStore.touch that
    stamps txn_id back onto the lot) fails — the whole acquire rolls back, so the
    automation event for a transaction that ends up never existing must never fire."""
    inst, acct = _seed_inst_and_acct()
    pos = _seed_position(inst, acct)

    calls: list = []
    monkeypatch.setattr(
        ledger_service,
        "_publish_committed_transaction_events",
        lambda *a, **k: calls.append((a, k)),
    )

    # Prove the leg actually RAN before the failure — otherwise `calls == []` below would
    # hold vacuously and the test would pass without exercising the deferral at all.
    leg_ran: list[str] = []
    real_create = ledger_service.create_transaction
    monkeypatch.setattr(
        ledger_service,
        "create_transaction",
        lambda data, **kw: (leg_ran.append("yes"), real_create(data, **kw))[1],
    )

    def _boom(*_a, **_k):
        raise RuntimeError("simulated failure after the cash leg")

    monkeypatch.setattr(LotStore, "touch", _boom)

    with pytest.raises(RuntimeError, match="simulated failure after the cash leg"):
        _acquire(pos["id"], qty=10.0, unit_cost=100.0, funding_account_id=acct["id"])

    assert leg_ran, "the cash leg must have run before the rollback — test is vacuous otherwise"
    assert calls == []
    assert TransactionStore().all() == []
    assert LotStore().all() == []


# ── 4c. automation match-usage + payee usage/category-learning timing (storage plan 4d
#       task 2 REVIEW-GATE BLOCKER fix): the SAME composed create_transaction(conn=...)
#       tail that publishes the automation event (4b above) ALSO runs
#       record_committed_matches (AutomationStore.stats) and payee_registry.note_usage /
#       learn_category (PayeeStore) — both DB-backed and, like the publish call, each opens
#       its OWN independent transaction when not given a conn. Pre-fix these two ran
#       IMMEDIATELY (never deferred), so on a composed path they raced the still-open outer
#       transaction, stalled ~5s on PRAGMA busy_timeout, hit "database is locked", and had
#       that exception SWALLOWED by their own best-effort guard — a silent, undetected write
#       loss. These tests assert the write actually LANDS, not merely that no exception
#       surfaced. Vehicle: the acquire funding debit (see 4b's note on why not the vest).

_CAT_FOOD = "cat_13095ba70000"  # a real BUILTIN_CATEGORIES id ("Food & Dining")


def _seed_builtin_categories():
    from backend.db.category_store import CategoryStore
    from backend.models.category_defs import BUILTIN_CATEGORIES

    CategoryStore().replace_all(list(BUILTIN_CATEGORIES))


def _seed_investment_transform_automation(automation_id: str = "automation_cash_leg_cat") -> dict:
    """A minimal, real transaction-transform Automation matching every investment cash leg
    — same shape as tests/test_ledger_automation_payee_no_self_deadlock.py's own
    ``_transform_automation`` helper (inserted raw via ``AutomationStore.insert()`` rather
    than through ``.create()``'s Lucius-only normalizer — see that helper's docstring for
    why a structured ``transaction_transform`` execution can only exist as a raw row).
    Matches on ``kind`` rather than description text so it fires for ANY cash leg,
    independent of the leg's own description wording."""
    return AutomationStore().insert(
        {
            "id": automation_id,
            "name": "Categorize investment cash legs",
            "description": "",
            "instructions": "Categorize matching transactions.",
            "status": "active",
            "trigger": {
                "kind": "event",
                "event": "txn",
                "when": {"kind": "investment"},
                "limit": "each",
            },
            "execution": {
                "kind": "transaction_transform",
                "set": {"category": _CAT_FOOD},
                "priority": 100,
                "stop": True,
                "override_existing": False,
            },
            "created_at": "2026-08-02T00:00:00+00:00",
            "updated_at": "2026-08-02T00:00:00+00:00",
            "provenance": {},
            "stats": {},
        }
    )


def _seed_funding_account_with_institution(name="Brokerage Cash", institution="IBKR"):
    """_book_funding_debit only mints a payee when the funding account names an
    institution (lots._resolve_deposit_payee_if_needed) — without one there is no payee
    write to assert on. `institution` is DERIVED from `institution_id`
    (account_store._hydrate_institutions), so the institution row has to exist first."""
    from backend.db.institution_store import InstitutionStore

    inst_row = InstitutionStore().resolve_or_create(institution)
    return AccountStore().create(
        {
            "name": name,
            "kind": "asset",
            "type": "investment",
            "currency": "SGD",
            "institution_id": inst_row["id"],
        }
    )


def test_composed_funding_leg_persists_payee_and_automation_writes_after_commit(env):
    """THE review-gate BLOCKER repro: a composed cash leg (create_transaction(conn=conn)
    inside _apply_acquire) matches an active Automation AND mints/uses a payee. Both
    record_committed_matches (AutomationStore.stats) and payee_registry.note_usage /
    learn_category (PayeeStore) must actually persist once the OUTER transaction commits —
    and the leg must not stall doing it (the reviewer measured WORKER_ELAPSED=5.24s
    pre-fix, one PRAGMA busy_timeout trip; a bug that also matched the Automation would
    trip up to three). Pre-fix: the automation's stats stay ``{}``, the payee's last_used
    stays None, and no default_category is learned — the writes silently dropped behind a
    swallowed ``database is locked``."""
    _seed_builtin_categories()
    automation = _seed_investment_transform_automation()
    inst, acct = _seed_inst_and_acct()
    funding = _seed_funding_account_with_institution()
    pos = _seed_position(inst, acct)

    start = time.monotonic()
    result = _acquire(
        pos["id"],
        qty=10.0,
        unit_cost=100.0,
        acquired_at="2025-03-15",
        funding_account_id=funding["id"],
    )
    elapsed = time.monotonic() - start

    assert result["ok"] is True
    txn = TransactionStore().find_by_id(result["cash_leg_id"])
    # The transform applies at CREATE time (resolve_transaction_row) — unaffected by the
    # bug either way; asserted here as the setup sanity check for what follows.
    assert txn["category"] == _CAT_FOOD

    automation_after = AutomationStore().find_by_id(automation["id"])
    assert automation_after["stats"].get("match_count") == 1
    assert automation_after["stats"].get("last_transaction_id") == result["cash_leg_id"]

    payee_id = txn["payee_id"]
    assert payee_id is not None
    payee = PayeeStore().find_by_id(payee_id)
    assert payee["last_used"] == "2025-03-15"  # the reviewer's exact repro field
    assert payee["default_categories"].get("investment") == _CAT_FOOD

    assert elapsed < 2.0, f"composed cash leg stalled: {elapsed:.2f}s (busy_timeout trip?)"


def test_composed_funding_leg_rollback_persists_no_payee_usage_or_automation_writes(
    env, monkeypatch
):
    """The rollback half: a LATER step in the same composed transaction fails after the
    cash leg already ran — the whole acquire rolls back, so neither the automation's stats
    nor the payee's usage/learned category may show any trace of a transaction that never
    ends up existing. The payee ENTITY mint itself is a separate, already-committed write
    (it runs before the outer transaction's own first write, so it is never in contention)
    and DOES survive the rollback; only its usage/learned-category fields, which ride the
    rolled-back outer transaction's deferred callbacks, must not be set."""
    _seed_builtin_categories()
    automation = _seed_investment_transform_automation()
    inst, acct = _seed_inst_and_acct()
    funding = _seed_funding_account_with_institution()
    pos = _seed_position(inst, acct)

    def _boom(*_a, **_k):
        raise RuntimeError("simulated failure after the cash leg")

    monkeypatch.setattr(LotStore, "touch", _boom)

    start = time.monotonic()
    with pytest.raises(RuntimeError, match="simulated failure after the cash leg"):
        _acquire(
            pos["id"],
            qty=10.0,
            unit_cost=100.0,
            acquired_at="2025-03-15",
            funding_account_id=funding["id"],
        )
    elapsed = time.monotonic() - start

    assert TransactionStore().all() == []
    assert AutomationStore().find_by_id(automation["id"])["stats"] == {}
    minted_payees = PayeeStore().all()
    assert len(minted_payees) == 1
    assert minted_payees[0]["last_used"] is None
    assert minted_payees[0]["default_categories"] == {}
    # DISCRIMINATING guard (review-gate follow-up): the empty-state assertions above ALSO hold
    # against the pre-fix tree — there, the eager-immediate record_committed_matches / payee-learn
    # calls trip PRAGMA busy_timeout (~5s, swallowed) BEFORE _boom raises, so the writes are
    # attempted-and-silently-dropped and the observable outcome is identical to correctly-never-fired.
    # Post-fix the two writes are deferred onto the outer transaction's on_commit list and never fire
    # on rollback, so the failure path is fast. Without this timing bound the test would pass for the
    # WRONG reason (and keep passing if the eager path were reintroduced); the bound makes it actually
    # guard the deferral.
    assert elapsed < 2.0, (
        f"composed rollback stalled: {elapsed:.2f}s (eager-immediate writes reintroduced?)"
    )


# ── 5. vest_undo: grants + lots + positions + vest_events ─────────────────────────


def test_vest_undo_does_not_self_deadlock_on_lot_delete(env):
    """vest_undo's delivered-lot branch must delete the FMV lot via the SAME
    conn=-threaded core _delete_lot uses — not by re-entering apply_lot_change's own
    transaction (the identical self-deadlock risk as the mint side)."""
    inst, acct = _seed_inst_and_acct()
    equity_service.apply_equity_change(_make_grant_change(inst, acct))
    grant = GrantStore().all()[0]
    vest_result = _vest(grant)

    result_box: dict = {}
    errors = _run_bounded(
        lambda: result_box.update(
            result=equity_service.apply_equity_change(
                {"type": "vest_undo", "vest_event_id": vest_result["vest_event_id"]}
            )
        )
    )

    assert errors == []
    assert result_box["result"]["ok"] is True
    assert LotStore().all() == []


def test_vest_undo_is_atomic_across_grants_lots_positions_vest_events(env, monkeypatch):
    inst, acct = _seed_inst_and_acct()
    equity_service.apply_equity_change(_make_grant_change(inst, acct))
    grant = GrantStore().all()[0]
    vest_result = _vest(grant)
    lot_id = vest_result["lot_id"]
    assert vest_result["txn_id"] is None  # a vest books no ledger row to reverse
    locked_id = grant["locked_position_id"]
    locked_qty_before = PositionStore().find_by_id(locked_id)["quantity"]

    def _boom(*_a, **_k):
        raise RuntimeError("simulated failure mid-vest-undo")

    monkeypatch.setattr(VestEventStore, "delete_event", _boom)

    with pytest.raises(RuntimeError, match="simulated failure mid-vest-undo"):
        equity_service.apply_equity_change(
            {"type": "vest_undo", "vest_event_id": vest_result["vest_event_id"]}
        )

    assert LotStore().find_by_id(lot_id) is not None
    assert PositionStore().find_by_id(locked_id)["quantity"] == locked_qty_before
    g_after = GrantStore().find_by_id(grant["id"])
    vested_t = next(t for t in g_after["tranches"] if t["id"] == grant["tranches"][0]["id"])
    assert vested_t["vested"] is True
    assert VestEventStore().find_by_id(vest_result["vest_event_id"]) is not None


def test_vest_undo_succeeds_and_reverses_everything_when_not_interrupted(env):
    inst, acct = _seed_inst_and_acct()
    equity_service.apply_equity_change(_make_grant_change(inst, acct))
    grant = GrantStore().all()[0]
    vest_result = _vest(grant)
    locked_id = grant["locked_position_id"]

    result = equity_service.apply_equity_change(
        {"type": "vest_undo", "vest_event_id": vest_result["vest_event_id"]}
    )

    assert result["ok"] is True
    assert LotStore().all() == []
    assert TransactionStore().all() == []
    assert PositionStore().find_by_id(locked_id)["quantity"] == pytest.approx(600.0)
    g_after = GrantStore().find_by_id(grant["id"])
    assert all(not t.get("vested") for t in g_after["tranches"])
    assert VestEventStore().find_by_id(vest_result["vest_event_id"]) is None


# ── 6. release_apply: lots + positions + vest_events (sweep finding) ───────────────


def _seed_release_required_grant():
    inst, acct = _seed_inst_and_acct()
    equity_service.apply_equity_change(_make_grant_change(inst, acct, release_required=True))
    grant = GrantStore().all()[0]
    vest_result = _vest(grant)
    return inst, acct, grant, vest_result


def test_release_apply_is_atomic_across_lots_positions_vest_events(env, monkeypatch):
    inst, acct, grant, vest_result = _seed_release_required_grant()
    pending_id = GrantStore().find_by_id(grant["id"])["pending_position_id"]
    pending_qty_before = PositionStore().find_by_id(pending_id)["quantity"]

    def _boom(*_a, **_k):
        raise RuntimeError("simulated failure mid-release")

    monkeypatch.setattr(VestEventStore, "update_event", _boom)

    with pytest.raises(RuntimeError, match="simulated failure mid-release"):
        equity_service.apply_equity_change(
            {"type": "release_apply", "vest_event_id": vest_result["vest_event_id"]}
        )

    assert LotStore().all() == []
    assert PositionStore().find_by_id(pending_id)["quantity"] == pending_qty_before
    ev_after = VestEventStore().find_by_id(vest_result["vest_event_id"])
    assert ev_after["pending_release"] is True
    assert ev_after["lot_id"] is None


def test_release_apply_succeeds_when_not_interrupted(env):
    inst, acct, grant, vest_result = _seed_release_required_grant()
    pending_id = GrantStore().find_by_id(grant["id"])["pending_position_id"]

    result = equity_service.apply_equity_change(
        {"type": "release_apply", "vest_event_id": vest_result["vest_event_id"]}
    )

    assert result["ok"] is True
    assert len(LotStore().all()) == 1
    assert PositionStore().find_by_id(pending_id)["quantity"] == 0.0
    ev_after = VestEventStore().find_by_id(vest_result["vest_event_id"])
    assert ev_after["pending_release"] is False
    assert ev_after["lot_id"] == result["lot_id"]


# ── 7. option open / close: option_contracts + transactions ────────────────────────


def _seed_option_account():
    inst, acct = _seed_inst_and_acct("XYZ")
    cash_acct = AccountStore().create(
        {"name": "Options Cash", "kind": "asset", "type": "cash", "currency": "SGD"}
    )
    return inst, acct, cash_acct


def _open_change(inst, acct, cash_acct, **extra):
    change = {
        "type": "open",
        "account_id": acct["id"],
        "underlying_instrument_id": inst["id"],
        "option_type": "call",
        "side": "short",
        "strike": 50.0,
        "expiry": "2027-01-15",
        "contracts": 2.0,
        "premium_per_share": 1.5,
        "premium_currency": "SGD",
        "premium_account_id": cash_acct["id"],
    }
    change.update(extra)
    return change


def test_option_open_is_atomic_across_contracts_and_transactions(env, monkeypatch):
    inst, acct, cash_acct = _seed_option_account()

    def _boom(*_a, **_k):
        raise RuntimeError("simulated failure mid-open")

    monkeypatch.setattr(ledger_service, "create_transaction", _boom)

    with pytest.raises(RuntimeError, match="simulated failure mid-open"):
        options_service.apply_option_change(_open_change(inst, acct, cash_acct))

    assert OptionContractStore().all() == []
    assert TransactionStore().all() == []


def test_option_open_succeeds_when_not_interrupted(env):
    inst, acct, cash_acct = _seed_option_account()

    result = options_service.apply_option_change(_open_change(inst, acct, cash_acct))

    assert result["ok"] is True
    assert len(OptionContractStore().all()) == 1
    txns = TransactionStore().all()
    assert len(txns) == 1
    assert txns[0]["kind"] == "income"  # short premium received


def test_option_close_is_atomic_and_leaves_contract_open_on_failure(env, monkeypatch):
    """Mirrors the pre-Task-4 contract: a failed close leg leaves the contract status
    untouched (still open) — but now via rollback (the write never lands) rather than
    the leg simply never being attempted."""
    inst, acct, cash_acct = _seed_option_account()
    opened = options_service.apply_option_change(_open_change(inst, acct, cash_acct))
    contract_id = opened["contract_id"]

    def _boom(*_a, **_k):
        raise RuntimeError("simulated failure mid-close")

    monkeypatch.setattr(ledger_service, "create_transaction", _boom)

    result = options_service.apply_option_change(
        {
            "type": "close",
            "contract_id": contract_id,
            "close_premium_per_share": 0.5,
            "premium_account_id": cash_acct["id"],
        }
    )

    assert "error" in result
    assert OptionContractStore().find_by_id(contract_id)["status"] == "open"
    # Only the ORIGINAL open txn exists — no close txn was left behind.
    assert len(TransactionStore().all()) == 1


def test_option_close_succeeds_when_not_interrupted(env):
    inst, acct, cash_acct = _seed_option_account()
    opened = options_service.apply_option_change(_open_change(inst, acct, cash_acct))
    contract_id = opened["contract_id"]

    result = options_service.apply_option_change(
        {
            "type": "close",
            "contract_id": contract_id,
            "close_premium_per_share": 0.5,
            "premium_account_id": cash_acct["id"],
        }
    )

    assert result["ok"] is True
    assert OptionContractStore().find_by_id(contract_id)["status"] == "closed"
    assert len(TransactionStore().all()) == 2


# ── 8. conn= additive contract: omitting it stays byte-identical ───────────────────


def test_position_touch_conn_omitted_still_locks_and_commits_independently(env):
    inst, acct = _seed_inst_and_acct()
    pos = _seed_position(inst, acct, quantity=1.0)
    updated = PositionStore().write_figures(pos["id"], {"quantity": 5.0})
    assert updated["quantity"] == 5.0
    assert PositionStore().find_by_id(pos["id"])["quantity"] == 5.0


def test_grant_update_conn_omitted_still_locks_and_commits_independently(env):
    inst, acct = _seed_inst_and_acct()
    equity_service.apply_equity_change(_make_grant_change(inst, acct))
    grant = GrantStore().all()[0]
    updated = GrantStore().update_grant(grant["id"], {"note": "hello"})
    assert updated["note"] == "hello"
    assert GrantStore().find_by_id(grant["id"])["note"] == "hello"


def test_option_create_contract_conn_omitted_still_locks_and_commits_independently(env):
    inst, acct, cash_acct = _seed_option_account()
    result = options_service.apply_option_change(_open_change(inst, acct, cash_acct))
    assert OptionContractStore().find_by_id(result["contract_id"]) is not None


# ── 9. deadlock-free cross-table composition (mirrors tests/db/test_choke_seam.py) ─


def test_locked_unit_of_work_deadlock_free_for_trade_tables(db_isolated):
    """Two threads composing (lots, positions, realized_events, transactions) in
    OPPOSITE argument order must both succeed, never deadlock — global alphabetical
    lock order proven generically in test_choke_seam.py, proven again here for THIS
    choke's specific table set (mirrors test_planning_choke_atomicity.py's own
    cross-table proof)."""
    lot_store = LotStore()
    position_store = PositionStore()
    realized_store = RealizedEventStore()
    txn_store = TransactionStore()

    barrier = threading.Barrier(2)
    order: list[str] = []
    errors: list[BaseException] = []

    def _writer(tables, label):
        try:
            barrier.wait(timeout=10)
            with locked_unit_of_work(*tables) as conn:
                order.append(f"{label}-start")
                time.sleep(0.05)
                lot_store.insert(
                    {"id": f"lot_{label}", "position_id": "p1", "original_qty": 1}, conn=conn
                )
                position_store.insert(
                    {
                        "id": f"pos_{label}",
                        "instrument_id": "i1",
                        "account_id": "a1",
                        "quantity": 1,
                    },
                    conn=conn,
                )
                realized_store.insert(
                    {"id": f"rlz_{label}", "position_id": "p1", "quantity": 1}, conn=conn
                )
                txn_store.insert(_txn(f"txn_{label}"), conn=conn)
                order.append(f"{label}-end")
        except BaseException as exc:  # pragma: no cover - failure path only
            errors.append(exc)

    t1 = threading.Thread(
        target=_writer, args=((lots_tbl, positions_tbl, realized_tbl, transactions_table), "t1")
    )
    t2 = threading.Thread(
        target=_writer, args=((transactions_table, realized_tbl, positions_tbl, lots_tbl), "t2")
    )
    t1.start()
    t2.start()
    t1.join(timeout=10)
    t2.join(timeout=10)

    assert not t1.is_alive() and not t2.is_alive(), "deadlock: a writer thread never finished"
    assert errors == []
    assert order in (
        ["t1-start", "t1-end", "t2-start", "t2-end"],
        ["t2-start", "t2-end", "t1-start", "t1-end"],
    )
    assert {r["id"] for r in lot_store.all()} == {"lot_t1", "lot_t2"}
    assert {r["id"] for r in position_store.all()} == {"pos_t1", "pos_t2"}
    assert {r["id"] for r in realized_store.all()} == {"rlz_t1", "rlz_t2"}
    assert {r["id"] for r in txn_store.all()} == {"txn_t1", "txn_t2"}


def test_locked_unit_of_work_deadlock_free_for_vest_tables(db_isolated):
    """Same proof over the vest choke's own 5-table set (grants, lots, positions,
    transactions, vest_events) — a distinct table combination from the dispose test
    above, so this choke's OWN lock set is independently proven deadlock-free."""
    grant_store = GrantStore()
    lot_store = LotStore()
    position_store = PositionStore()
    txn_store = TransactionStore()
    vest_store = VestEventStore()

    barrier = threading.Barrier(2)
    order: list[str] = []
    errors: list[BaseException] = []

    def _writer(tables, label):
        try:
            barrier.wait(timeout=10)
            with locked_unit_of_work(*tables) as conn:
                order.append(f"{label}-start")
                time.sleep(0.05)
                grant_store.insert({"id": f"grant_{label}", "status": "active"}, conn=conn)
                lot_store.insert(
                    {"id": f"lot_{label}", "position_id": "p1", "original_qty": 1}, conn=conn
                )
                position_store.insert(
                    {
                        "id": f"pos_{label}",
                        "instrument_id": "i1",
                        "account_id": "a1",
                        "quantity": 1,
                    },
                    conn=conn,
                )
                txn_store.insert(_txn(f"txn_{label}"), conn=conn)
                vest_store.insert({"id": f"vest_{label}", "grant_id": f"grant_{label}"}, conn=conn)
                order.append(f"{label}-end")
        except BaseException as exc:  # pragma: no cover - failure path only
            errors.append(exc)

    tables_forward = (grants_tbl, lots_tbl, positions_tbl, transactions_table, vest_events_tbl)
    tables_reverse = tuple(reversed(tables_forward))
    t1 = threading.Thread(target=_writer, args=(tables_forward, "t1"))
    t2 = threading.Thread(target=_writer, args=(tables_reverse, "t2"))
    t1.start()
    t2.start()
    t1.join(timeout=10)
    t2.join(timeout=10)

    assert not t1.is_alive() and not t2.is_alive(), "deadlock: a writer thread never finished"
    assert errors == []
    assert order in (
        ["t1-start", "t1-end", "t2-start", "t2-end"],
        ["t2-start", "t2-end", "t1-start", "t1-end"],
    )


def test_locked_unit_of_work_deadlock_free_for_option_tables(db_isolated):
    """Same proof over the option choke's (option_contracts, transactions) set."""
    contract_store = OptionContractStore()
    txn_store = TransactionStore()

    barrier = threading.Barrier(2)
    order: list[str] = []
    errors: list[BaseException] = []

    def _writer(tables, label):
        try:
            barrier.wait(timeout=10)
            with locked_unit_of_work(*tables) as conn:
                order.append(f"{label}-start")
                time.sleep(0.05)
                contract_store.insert({"id": f"opt_{label}", "status": "open"}, conn=conn)
                txn_store.insert(_txn(f"txn_{label}"), conn=conn)
                order.append(f"{label}-end")
        except BaseException as exc:  # pragma: no cover - failure path only
            errors.append(exc)

    t1 = threading.Thread(target=_writer, args=((option_contracts_tbl, transactions_table), "t1"))
    t2 = threading.Thread(target=_writer, args=((transactions_table, option_contracts_tbl), "t2"))
    t1.start()
    t2.start()
    t1.join(timeout=10)
    t2.join(timeout=10)

    assert not t1.is_alive() and not t2.is_alive(), "deadlock: a writer thread never finished"
    assert errors == []
    assert order in (
        ["t1-start", "t1-end", "t2-start", "t2-end"],
        ["t2-start", "t2-end", "t1-start", "t1-end"],
    )
