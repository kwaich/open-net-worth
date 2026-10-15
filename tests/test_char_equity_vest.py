"""
Characterization tests for the equity-vest choke point (feat/equity-comp).

These tests assert the locked-quantity invariant and write-order correctness that make the
four-store atomic action safe. The invariant is:

    locked_position.quantity == Σ(tranche.units where not vested)

This must hold after every apply_equity_change op. If it ever drifts, the net-worth locked
bucket is wrong and the vest calendar overestimates unvested exposure.

Also verifies:
  - vest income appears exactly once in _employment_income_section (not caught by
    _is_investment_income_txn), asserting the double-count guard in tax.py.
  - A re-apply with the same grant+tranche (no idempotency_key) returns idempotent=True
    (the find_by_grant_tranche gate fires before any write).
"""

from __future__ import annotations

import pytest

from backend.db.account_store import AccountStore
from backend.db.grant_store import GrantStore
from backend.db.instrument_store import InstrumentStore
from backend.db.lot_store import LotStore
from backend.db.position_store import PositionStore
from backend.db.transaction_store import TransactionStore
from backend.db.vest_event_store import VestEventStore
from backend.services import equity as svc
from backend.services import ledger as ledger_service
from backend.services import lots as lots_service


@pytest.fixture
def isolated(tmp_path, monkeypatch, db_isolated):
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
    monkeypatch.setattr(lots_service, "_load_rates", lambda: {"SGD": 1.0})
    monkeypatch.setattr(
        "backend.services.ledger.to_base",
        lambda a, c, b=None, rates=None: round(float(a or 0), 2),
    )
    monkeypatch.setattr(
        svc,
        "to_base",
        lambda a, c, b=None, rates=None: round(float(a or 0), 2),
    )
    return tmp_path


def _assert_locked_invariant(grant_id: str, msg: str = "") -> None:
    """Assert locked_position.quantity == Σ(tranche.units where not vested)."""
    grant = GrantStore().find_by_id(grant_id)
    assert grant is not None, f"grant {grant_id} not found"
    unvested_sum = sum(
        float(t.get("units") or 0) for t in (grant.get("tranches") or []) if not t.get("vested")
    )
    locked_id = grant.get("locked_position_id")
    locked_pos = PositionStore().find_by_id(locked_id) if locked_id else None
    locked_qty = float((locked_pos or {}).get("quantity") or 0)
    assert locked_qty == pytest.approx(unvested_sum), (
        f"INVARIANT VIOLATED {msg}: locked_qty={locked_qty} ≠ unvested_sum={unvested_sum}"
    )


def _seed(isolated):
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
            "total_units": 900.0,
            "tax_event": "at_vest",
            "tranches": [
                {"vest_date": "2025-03-15", "units": 300.0},
                {"vest_date": "2026-03-15", "units": 300.0},
                {"vest_date": "2027-03-15", "units": 300.0},
            ],
        }
    )
    grant = GrantStore().all()[0]
    return inst, acct, grant


# ── Invariant: locked quantity == Σ unvested units after every op ─────────────────


def test_invariant_after_grant_create(isolated):
    inst, acct, grant = _seed(isolated)
    _assert_locked_invariant(grant["id"], "after grant_create")


def test_invariant_after_first_vest(isolated):
    inst, acct, grant = _seed(isolated)
    tranche_id = grant["tranches"][0]["id"]

    svc.apply_equity_change(
        {
            "type": "vest_apply",
            "grant_id": grant["id"],
            "tranche_id": tranche_id,
            "fmv_per_unit": 30.0,
            "fmv_currency": "SGD",
            "vest_date": "2025-03-15",
        }
    )
    _assert_locked_invariant(grant["id"], "after vest_apply tranche 0")


def test_invariant_after_two_vests(isolated):
    inst, acct, grant = _seed(isolated)
    for i in range(2):
        tranche_id = grant["tranches"][i]["id"]
        vest_date = grant["tranches"][i]["vest_date"]
        svc.apply_equity_change(
            {
                "type": "vest_apply",
                "grant_id": grant["id"],
                "tranche_id": tranche_id,
                "fmv_per_unit": 30.0,
                "fmv_currency": "SGD",
                "vest_date": vest_date,
            }
        )
        _assert_locked_invariant(grant["id"], f"after vest_apply tranche {i}")


def test_invariant_after_undo(isolated):
    inst, acct, grant = _seed(isolated)
    tranche_id = grant["tranches"][0]["id"]

    vest_result = svc.apply_equity_change(
        {
            "type": "vest_apply",
            "grant_id": grant["id"],
            "tranche_id": tranche_id,
            "fmv_per_unit": 30.0,
            "fmv_currency": "SGD",
            "vest_date": "2025-03-15",
        }
    )
    _assert_locked_invariant(grant["id"], "after vest_apply")

    svc.apply_equity_change({"type": "vest_undo", "vest_event_id": vest_result["vest_event_id"]})
    _assert_locked_invariant(grant["id"], "after vest_undo")


def test_invariant_after_full_vest_cycle(isolated):
    """Vest all three tranches, verify invariant reaches 0 locked (fully_vested)."""
    inst, acct, grant = _seed(isolated)

    for tranche in grant["tranches"]:
        svc.apply_equity_change(
            {
                "type": "vest_apply",
                "grant_id": grant["id"],
                "tranche_id": tranche["id"],
                "fmv_per_unit": 30.0,
                "fmv_currency": "SGD",
                "vest_date": tranche["vest_date"],
            }
        )
        _assert_locked_invariant(grant["id"], f"after vesting {tranche['id']}")

    # Final state: fully_vested, locked = 0.
    g = GrantStore().find_by_id(grant["id"])
    assert g["status"] == "fully_vested"
    locked_pos = PositionStore().find_by_id(g["locked_position_id"])
    assert locked_pos["quantity"] == pytest.approx(0.0)


# ── Double-count guard: vest income counted once, not as investment income ─────────


def test_vest_writes_no_income_row_to_double_count(isolated):
    """The original guard asserted the vest's income txn was counted once (employment)
    and never again as investment income. A vest now writes NO ledger row, so there is
    nothing to double count in either direction
    (ADR 2026-09-22-a-vest-books-no-income-transaction). The compensation fact lives on
    the vest event, which is what equity_report reads."""
    _inst, _acct, grant = _seed(isolated)

    svc.apply_equity_change(
        {
            "type": "vest_apply",
            "grant_id": grant["id"],
            "tranche_id": grant["tranches"][0]["id"],
            "fmv_per_unit": 30.0,
            "fmv_currency": "SGD",
            "vest_date": "2025-03-15",
        }
    )

    assert TransactionStore().all() == []
    assert VestEventStore().all()[0]["gross_gain_native"] == pytest.approx(9000.0)


def test_partial_vest_invariant_and_remainder(isolated):
    """Partial vest (units < tranche.units) must:
    - keep locked_position.quantity == Σ(tranche.units where not vested) (invariant)
    - split the tranche: vested portion = vest_units, remainder tranche still unvested
    - remainder tranche visible in upcoming_vests
    """
    inst, acct, grant = _seed(isolated)
    # Vest only 100 of the 300-unit first tranche.
    tranche = grant["tranches"][0]
    tranche_id = tranche["id"]

    svc.apply_equity_change(
        {
            "type": "vest_apply",
            "grant_id": grant["id"],
            "tranche_id": tranche_id,
            "units": 100.0,  # partial: only 100 out of 300
            "fmv_per_unit": 30.0,
            "fmv_currency": "SGD",
            "vest_date": tranche["vest_date"],
        }
    )

    # Invariant must hold.
    _assert_locked_invariant(grant["id"], "after partial vest (100 of 300)")

    # Grant should still be active (not fully_vested).
    g = GrantStore().find_by_id(grant["id"])
    assert g["status"] == "active"

    # The original tranche_id is now vested with units=100.
    vested_t = next((t for t in g["tranches"] if t.get("id") == tranche_id), None)
    assert vested_t is not None
    assert vested_t["vested"] is True
    assert vested_t["units"] == pytest.approx(100.0)

    # A new remainder tranche exists with units=200, vested=False.
    remainder_tranches = [
        t for t in g["tranches"] if not t.get("vested") and t.get("id") != tranche_id
    ]
    remainder_for_original = [
        t for t in remainder_tranches if t.get("vest_date") == tranche["vest_date"]
    ]
    assert len(remainder_for_original) == 1, (
        f"Expected 1 remainder tranche, got: {remainder_tranches}"
    )
    rem = remainder_for_original[0]
    assert rem["units"] == pytest.approx(200.0)
    assert rem["vest_date"] == tranche["vest_date"]

    # Remainder tranche is visible in upcoming_vests.
    upcoming = svc.upcoming_vests(window_days=None)
    upcoming_ids = {u["tranche_id"] for u in upcoming}
    assert rem["id"] in upcoming_ids, (
        f"Remainder tranche {rem['id']} not in upcoming: {upcoming_ids}"
    )


def test_partial_vest_then_full_vest_remainder(isolated):
    """After a partial vest, vesting the remainder tranche fully clears locked and marks
    the grant fully_vested (after all other tranches are also vested)."""
    inst, acct, grant = _seed(isolated)
    tranche_0 = grant["tranches"][0]

    # Partial vest of first tranche.
    svc.apply_equity_change(
        {
            "type": "vest_apply",
            "grant_id": grant["id"],
            "tranche_id": tranche_0["id"],
            "units": 100.0,
            "fmv_per_unit": 30.0,
            "fmv_currency": "SGD",
            "vest_date": tranche_0["vest_date"],
        }
    )
    _assert_locked_invariant(grant["id"], "after partial vest tranche 0")

    # Reload grant to find the remainder tranche.
    g = GrantStore().find_by_id(grant["id"])
    rem_tranche = next(
        t
        for t in g["tranches"]
        if not t.get("vested") and t.get("vest_date") == tranche_0["vest_date"]
    )

    # Vest the remainder.
    svc.apply_equity_change(
        {
            "type": "vest_apply",
            "grant_id": grant["id"],
            "tranche_id": rem_tranche["id"],
            "units": 200.0,
            "fmv_per_unit": 30.0,
            "fmv_currency": "SGD",
            "vest_date": tranche_0["vest_date"],
        }
    )
    _assert_locked_invariant(grant["id"], "after remainder vest")

    # Vest the other two tranches.
    g = GrantStore().find_by_id(grant["id"])
    for t in g["tranches"]:
        if not t.get("vested"):
            svc.apply_equity_change(
                {
                    "type": "vest_apply",
                    "grant_id": grant["id"],
                    "tranche_id": t["id"],
                    "fmv_per_unit": 30.0,
                    "fmv_currency": "SGD",
                    "vest_date": t["vest_date"],
                }
            )
    _assert_locked_invariant(grant["id"], "after all tranches vested")

    g = GrantStore().find_by_id(grant["id"])
    assert g["status"] == "fully_vested"
    locked_pos = PositionStore().find_by_id(g["locked_position_id"])
    assert locked_pos["quantity"] == pytest.approx(0.0)


# ── Idempotency: retry without idempotency_key still noop via grant+tranche key ───


def test_retry_same_grant_tranche_is_noop(isolated):
    inst, acct, grant = _seed(isolated)
    tranche_id = grant["tranches"][0]["id"]

    change = {
        "type": "vest_apply",
        "grant_id": grant["id"],
        "tranche_id": tranche_id,
        "fmv_per_unit": 30.0,
        "fmv_currency": "SGD",
        "vest_date": "2025-03-15",
    }
    svc.apply_equity_change(change)
    r2 = svc.apply_equity_change(change)

    # Second apply is idempotent — no new txn/lot/event.
    assert r2.get("idempotent") is True
    assert len(VestEventStore().all()) == 1
    assert TransactionStore().all() == []
    assert len(LotStore().all()) == 1
    _assert_locked_invariant(grant["id"], "after idempotent re-apply")
