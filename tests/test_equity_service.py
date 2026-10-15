"""
Unit tests for backend.services.equity — equity-compensation write choke point.

Exercises REAL stores in tmp_path (no live data). Monkeypatches store module-level
path constants and rebinds module-level store handles (mirrors test_income_apply.py).

Tests cover: EQ-GRANT-CREATE, EQ-VEST-POST, EQ-TAX-DEFERRED, EQ-FMV-PREFILL,
EQ-UNDO (full reversal + partial-lot refusal), FX (non-base fmv_currency → base via
txn_base_amount), idempotency (re-apply no-op), double-count guard.
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
    """Redirect every backing file to tmp_path + identity FX (SGD base).

    InstrumentStore is DB-backed (plan 2, task 5) — db_isolated gives it a fresh
    per-test database instead of a tmp_path file redirect.
    """
    # accounts/transactions are DB-backed now (plan 3 task 4) — db_isolated (above) redirects
    # the process-wide DB singleton, so the fresh AccountStore()/TransactionStore() instances
    # wired below resolve against it with no per-instance path-sync needed.
    # Rebind module-level store handles.
    monkeypatch.setattr(svc, "grants", GrantStore())
    monkeypatch.setattr(svc, "vest_events", VestEventStore())
    monkeypatch.setattr(svc, "instruments", InstrumentStore())
    monkeypatch.setattr(svc, "positions", PositionStore())
    monkeypatch.setattr(lots_service, "instruments", InstrumentStore())
    monkeypatch.setattr(ledger_service, "accounts", AccountStore())
    monkeypatch.setattr(ledger_service, "transactions", TransactionStore())
    # Identity FX: SGD base, 1 USD = 1.35 SGD.
    monkeypatch.setattr(svc, "base_currency", lambda: "SGD")
    monkeypatch.setattr(lots_service, "base_currency", lambda: "SGD")
    monkeypatch.setattr(ledger_service, "base_currency", lambda: "SGD")
    monkeypatch.setattr("backend.core.fx.base_currency", lambda: "SGD")
    rates = {"SGD": 1.0, "USD": 1.35}
    monkeypatch.setattr(lots_service, "_load_rates", lambda: rates)
    monkeypatch.setattr(
        "backend.services.ledger.to_base",
        lambda a, c, b=None, rates=None: round(float(a or 0) * (1.35 if c == "USD" else 1.0), 2),
    )
    monkeypatch.setattr(
        svc,
        "to_base",
        lambda a, c, b=None, rates=None: round(float(a or 0) * (1.35 if c == "USD" else 1.0), 2),
    )
    return tmp_path


def _seed_inst_and_acct(symbol="ONWAI", ccy="SGD"):
    inst = InstrumentStore().create(
        {
            "symbol": symbol,
            "name": f"{symbol} Corp",
            "quote_currency": ccy,
            "price_source_id": symbol,
            "price_source": "yahoo",
        }
    )
    acct = AccountStore().create(
        {"name": "Employer Equity", "kind": "asset", "type": "investment", "currency": ccy}
    )
    return inst, acct


def _make_grant_change(inst, acct, tranches=None, tax_event="at_vest"):
    if tranches is None:
        tranches = [
            {"vest_date": "2025-03-15", "units": 300.0},
            {"vest_date": "2026-03-15", "units": 300.0},
            {"vest_date": "2027-03-15", "units": 300.0},
            {"vest_date": "2028-03-15", "units": 300.0},
        ]
    return {
        "type": "grant_create",
        "instrument_id": inst["id"],
        "account_id": acct["id"],
        "grant_date": "2024-03-15",
        "total_units": sum(t["units"] for t in tranches),
        "tax_event": tax_event,
        "tranches": tranches,
    }


# ── EQ-GRANT-CREATE ─────────────────────────────────────────────────────────────────


def test_grant_create_creates_locked_position(isolated):
    inst, acct = _seed_inst_and_acct()
    result = svc.apply_equity_change(_make_grant_change(inst, acct))
    assert result["ok"] is True

    grants = GrantStore().all()
    assert len(grants) == 1
    g = grants[0]
    assert g["status"] == "active"
    assert g["total_units"] == 1200.0
    assert len(g["tranches"]) == 4
    assert all(t.get("id") for t in g["tranches"]), "each tranche must have a stable id"

    # Locked position must exist with total units.
    locked_id = g["locked_position_id"]
    assert locked_id
    pos = PositionStore().find_by_id(locked_id)
    assert pos["vesting_state"] == "unvested"
    assert pos["quantity"] == pytest.approx(1200.0)
    assert pos["instrument_id"] == inst["id"]

    # No lots created (unvested units must not auto-create a lot).
    assert LotStore().all() == []


def test_grant_create_idempotent_on_idempotency_key(isolated):
    """A double-submitted grant_create with the same idempotency_key must NOT mint a
    second grant + second locked position (grants were the one money-fact store
    without a dedupe key)."""
    inst, acct = _seed_inst_and_acct()
    change = {**_make_grant_change(inst, acct), "idempotency_key": "email-grant-123"}

    first = svc.apply_equity_change(change)
    second = svc.apply_equity_change(change)

    assert second["ok"] is True
    assert second["grant_id"] == first["grant_id"]
    assert second["locked_position_id"] == first["locked_position_id"]
    assert second.get("deduped") is True
    assert len(GrantStore().all()) == 1
    locked = [p for p in PositionStore().all() if p.get("vesting_state") == "unvested"]
    assert len(locked) == 1


def test_grant_create_without_key_still_creates(isolated):
    """No idempotency_key → two creates are two grants (unchanged manual behavior)."""
    inst, acct = _seed_inst_and_acct()
    svc.apply_equity_change(_make_grant_change(inst, acct))
    svc.apply_equity_change(_make_grant_change(inst, acct))
    assert len(GrantStore().all()) == 2


def test_grant_create_locked_position_in_hydrated(isolated):
    """EQ-NW-SPLIT: after grant_create, positions_service.hydrated() includes the locked
    position with the grant's instrument and unvested quantity as vesting_state='unvested',
    so net-worth/accessibility consumers see it without any code changes."""
    from backend.services import positions as positions_service

    inst, acct = _seed_inst_and_acct()
    svc.apply_equity_change(_make_grant_change(inst, acct))
    grant = GrantStore().all()[0]

    rows = positions_service.hydrated()

    locked_rows = [
        r
        for r in rows
        if r.get("vesting_state") == "unvested" and r.get("instrument_id") == inst["id"]
    ]
    assert len(locked_rows) == 1, f"Expected one locked row for the grant, got {locked_rows}"
    row = locked_rows[0]
    assert row["quantity"] == pytest.approx(grant["total_units"])
    assert row["position_id"] == grant["locked_position_id"]


def test_grant_create_validates_tranche_sum(isolated):
    inst, acct = _seed_inst_and_acct()
    with pytest.raises(Exception, match="tranche"):
        svc.apply_equity_change(
            {
                "type": "grant_create",
                "instrument_id": inst["id"],
                "account_id": acct["id"],
                "grant_date": "2024-03-15",
                "total_units": 1200.0,
                "tax_event": "at_vest",
                "tranches": [{"vest_date": "2025-03-15", "units": 100.0}],  # sum ≠ total
            }
        )


def test_grant_create_validates_instrument_exists(isolated):
    _seed_inst_and_acct()
    with pytest.raises(Exception, match="instrument not found"):
        svc.apply_equity_change(
            {
                "type": "grant_create",
                "instrument_id": "nonexistent",
                "grant_date": "2024-03-15",
                "total_units": 100.0,
                "tax_event": "at_vest",
                "tranches": [{"vest_date": "2025-03-15", "units": 100.0}],
            }
        )


# ── grant_update tax_event validation ──────────────────────────────────────────────


def test_grant_update_invalid_tax_event_raises(isolated):
    """_grant_update must reject invalid tax_event values (MINOR 3 guard matches _grant_create)."""
    inst, acct = _seed_inst_and_acct()
    svc.apply_equity_change(_make_grant_change(inst, acct))
    grant = GrantStore().all()[0]

    with pytest.raises(Exception, match="tax_event"):
        svc.apply_equity_change(
            {
                "type": "grant_update",
                "grant_id": grant["id"],
                "tax_event": "invalid_value",
            }
        )


def test_grant_update_valid_tax_event_succeeds(isolated):
    inst, acct = _seed_inst_and_acct()
    svc.apply_equity_change(_make_grant_change(inst, acct))
    grant = GrantStore().all()[0]

    result = svc.apply_equity_change(
        {"type": "grant_update", "grant_id": grant["id"], "tax_event": "deferred"}
    )
    assert result["ok"] is True
    updated = GrantStore().find_by_id(grant["id"])
    assert updated["tax_event"] == "deferred"


# ── EQ-VEST-POST ────────────────────────────────────────────────────────────────────


def test_vest_apply_writes_no_txn_and_mints_the_lot(isolated):
    inst, acct = _seed_inst_and_acct()
    svc.apply_equity_change(_make_grant_change(inst, acct))
    grant = GrantStore().all()[0]
    tranche_id = grant["tranches"][0]["id"]

    vest_result = svc.apply_equity_change(
        {
            "type": "vest_apply",
            "grant_id": grant["id"],
            "tranche_id": tranche_id,
            "fmv_per_unit": 30.0,
            "fmv_currency": "SGD",
            "vest_date": "2025-03-15",
            "source": "manual",
        }
    )
    assert vest_result["ok"] is True

    # NO ledger transaction: a vest delivers shares, not cash. Booking an inflow to the
    # custodian account counted the vest twice in net worth (once as money that never
    # arrived, once as the shares) — ADR 2026-09-22-a-vest-books-no-income-transaction.
    assert TransactionStore().all() == []
    assert vest_result["txn_id"] is None

    # One FMV-basis lot on the liquid position.
    lots = LotStore().all()
    assert len(lots) == 1
    lot = lots[0]
    assert lot["kind"] == "grant"
    assert lot["original_qty"] == pytest.approx(300.0)
    assert lot["unit_cost"] == pytest.approx(30.0)
    assert lot["acquired_at"] == "2025-03-15"
    assert lot["txn_id"] is None

    # Liquid sibling position created and holds 300 units via resync.
    liq_positions = [
        p
        for p in PositionStore().all()
        if p.get("vesting_state") == "vested" and p.get("instrument_id") == inst["id"]
    ]
    assert len(liq_positions) == 1

    # Locked position decremented by 300.
    locked_id = grant["locked_position_id"]
    locked_pos = PositionStore().find_by_id(locked_id)
    assert locked_pos["quantity"] == pytest.approx(900.0)

    # Tranche marked vested.
    g_after = GrantStore().find_by_id(grant["id"])
    vested_t = next(t for t in g_after["tranches"] if t["id"] == tranche_id)
    assert vested_t["vested"] is True
    assert vested_t["vest_event_id"] == vest_result["vest_event_id"]

    # One vest_event appended.
    evts = VestEventStore().all()
    assert len(evts) == 1
    ev = evts[0]
    assert ev["grant_id"] == grant["id"]
    assert ev["txn_id"] is None
    assert ev["lot_id"] == lot["id"]
    assert ev["gross_gain_native"] == pytest.approx(9000.0)
    # The tax facts survive on the event — that is what equity_report reads.
    assert ev["fmv_per_unit"] == pytest.approx(30.0)
    assert ev["fmv_currency"] == "SGD"
    assert ev["units"] == pytest.approx(300.0)


def test_vest_apply_all_tranches_marks_fully_vested(isolated):
    inst, acct = _seed_inst_and_acct()
    svc.apply_equity_change(
        _make_grant_change(inst, acct, tranches=[{"vest_date": "2025-03-15", "units": 100.0}])
    )
    grant = GrantStore().all()[0]
    tranche_id = grant["tranches"][0]["id"]

    svc.apply_equity_change(
        {
            "type": "vest_apply",
            "grant_id": grant["id"],
            "tranche_id": tranche_id,
            "fmv_per_unit": 10.0,
            "fmv_currency": "SGD",
            "vest_date": "2025-03-15",
        }
    )
    g = GrantStore().find_by_id(grant["id"])
    assert g["status"] == "fully_vested"


# ── EQ-TAX-DEFERRED ─────────────────────────────────────────────────────────────────


def test_vest_deferred_skips_txn_but_creates_lot(isolated):
    inst, acct = _seed_inst_and_acct()
    svc.apply_equity_change(_make_grant_change(inst, acct, tax_event="deferred"))
    grant = GrantStore().all()[0]
    tranche_id = grant["tranches"][0]["id"]

    result = svc.apply_equity_change(
        {
            "type": "vest_apply",
            "grant_id": grant["id"],
            "tranche_id": tranche_id,
            "fmv_per_unit": 30.0,
            "fmv_currency": "SGD",
            "vest_date": "2025-03-15",
        }
    )
    assert result["ok"] is True
    assert result["txn_id"] is None

    # NO income transaction.
    assert TransactionStore().all() == []

    # But the lot IS created.
    assert len(LotStore().all()) == 1

    # And the tranche is marked vested.
    g = GrantStore().find_by_id(grant["id"])
    t = next(t for t in g["tranches"] if t["id"] == tranche_id)
    assert t["vested"] is True

    # Vest event has txn_id=None.
    ev = VestEventStore().all()[0]
    assert ev["txn_id"] is None
    assert ev["lot_id"] is not None


# ── EQ-UNDO ─────────────────────────────────────────────────────────────────────────


def test_vest_undo_reverses_all_artifacts(isolated):
    inst, acct = _seed_inst_and_acct()
    svc.apply_equity_change(_make_grant_change(inst, acct))
    grant = GrantStore().all()[0]
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
    vest_event_id = vest_result["vest_event_id"]

    # Undo
    undo_result = svc.apply_equity_change({"type": "vest_undo", "vest_event_id": vest_event_id})
    assert undo_result["ok"] is True

    # No vest_events remain.
    assert VestEventStore().all() == []

    # Income txn is soft-deleted.
    txns = [t for t in TransactionStore().all() if not t.get("deleted")]
    assert txns == []

    # Lot is deleted.
    assert LotStore().all() == []

    # Locked position restored to original quantity.
    locked_pos = PositionStore().find_by_id(grant["locked_position_id"])
    assert locked_pos["quantity"] == pytest.approx(1200.0)

    # Tranche flipped back to unvested.
    g = GrantStore().find_by_id(grant["id"])
    t = next(tr for tr in g["tranches"] if tr["id"] == tranche_id)
    assert t["vested"] is False
    assert t["vest_event_id"] is None


def test_vest_undo_refuses_when_lot_partially_consumed(isolated):
    """Undo must fail with a typed error when the vest lot has been partially sold."""
    inst, acct = _seed_inst_and_acct()
    svc.apply_equity_change(_make_grant_change(inst, acct))
    grant = GrantStore().all()[0]
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
    # Partially consume the lot (simulate a sell of 100 units out of 300).
    lot = LotStore().all()[0]
    lot_store_obj = LotStore()
    lot_store_obj.bulk_consume([(lot["id"], 100.0)])

    with pytest.raises(Exception, match="cannot undo|partially consumed"):
        svc.apply_equity_change(
            {"type": "vest_undo", "vest_event_id": vest_result["vest_event_id"]}
        )


# ── Idempotency ──────────────────────────────────────────────────────────────────────


def test_vest_apply_idempotent_on_idempotency_key(isolated):
    inst, acct = _seed_inst_and_acct()
    svc.apply_equity_change(_make_grant_change(inst, acct))
    grant = GrantStore().all()[0]
    tranche_id = grant["tranches"][0]["id"]

    change = {
        "type": "vest_apply",
        "grant_id": grant["id"],
        "tranche_id": tranche_id,
        "fmv_per_unit": 30.0,
        "fmv_currency": "SGD",
        "vest_date": "2025-03-15",
        "idempotency_key": "vest-idempotent-1",
    }
    r1 = svc.apply_equity_change(change)
    assert r1["ok"] is True
    r2 = svc.apply_equity_change(change)
    assert r2.get("idempotent") is True
    # Still no txn, one lot, one event.
    assert TransactionStore().all() == []
    assert len(LotStore().all()) == 1
    assert len(VestEventStore().all()) == 1


def test_vest_apply_idempotent_on_grant_tranche_key(isolated):
    """Re-applying to the same grant+tranche returns the existing event even without idempotency_key."""
    inst, acct = _seed_inst_and_acct()
    svc.apply_equity_change(_make_grant_change(inst, acct))
    grant = GrantStore().all()[0]
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
    assert r2.get("idempotent") is True


# ── FX (non-base fmv_currency) ───────────────────────────────────────────────────────


def test_vest_apply_fx_base_recompute(isolated):
    """FMV entered in USD → gross_gain_base is in SGD via to_base."""
    inst, acct = _seed_inst_and_acct(ccy="USD")
    svc.apply_equity_change(
        _make_grant_change(inst, acct, tranches=[{"vest_date": "2025-03-15", "units": 100.0}])
    )
    grant = GrantStore().all()[0]
    tranche_id = grant["tranches"][0]["id"]

    result = svc.apply_equity_change(
        {
            "type": "vest_apply",
            "grant_id": grant["id"],
            "tranche_id": tranche_id,
            "fmv_per_unit": 10.0,
            "fmv_currency": "USD",  # non-base
            "vest_date": "2025-03-15",
        }
    )
    # gross_gain_native = 100 × (10 - 0) = 1000 USD
    # gross_gain_base = 1000 × 1.35 = 1350 SGD
    assert result["gross_gain_native"] == pytest.approx(1000.0)
    assert result["gross_gain_base"] == pytest.approx(1350.0)
    assert result["base_currency"] == "SGD"


# ── EQ-FMV-PREFILL ───────────────────────────────────────────────────────────────────


def test_fmv_prefill_exact_date(isolated, monkeypatch):
    inst, acct = _seed_inst_and_acct()
    svc.apply_equity_change(_make_grant_change(inst, acct))
    grant = GrantStore().all()[0]
    tranche_id = grant["tranches"][0]["id"]

    # Simulate price history with an exact price point on the vest date.
    monkeypatch.setattr(
        svc.price_history,
        "as_of_maps",
        lambda days: {"2025-03-15": {"ONWAI": 28.50}},
    )
    monkeypatch.setattr(
        svc.price_history,
        "series",
        lambda pid: [{"date": "2025-03-15", "price": 28.50}],
    )

    result = svc.fmv_prefill(grant["id"], tranche_id)
    assert result["fmv_per_unit"] == pytest.approx(28.50)
    assert result["fmv_currency"] == "SGD"
    # as_of must report the actual price date (the exact match: "2025-03-15")
    assert result["as_of"] == "2025-03-15"
    assert result["source"] == "price_history"


def test_fmv_prefill_prior_close_reports_actual_date(isolated, monkeypatch):
    """When no price on the vest date, as_of must be the prior close's date (RFC §7)."""
    inst, acct = _seed_inst_and_acct()
    svc.apply_equity_change(_make_grant_change(inst, acct))
    grant = GrantStore().all()[0]
    tranche_id = grant["tranches"][0]["id"]  # vest_date = "2025-03-15"

    # Price only on 2025-03-14 (prior close, not the vest date).
    monkeypatch.setattr(
        svc.price_history,
        "as_of_maps",
        lambda days: {"2025-03-15": {"ONWAI": 27.80}},
    )
    monkeypatch.setattr(
        svc.price_history,
        "series",
        lambda pid: [{"date": "2025-03-14", "price": 27.80}],
    )

    result = svc.fmv_prefill(grant["id"], tranche_id)
    assert result["fmv_per_unit"] == pytest.approx(27.80)
    # as_of must be the prior close's date, not vest_date
    assert result["as_of"] == "2025-03-14"
    assert result["source"] == "price_history"


def test_fmv_prefill_no_price_returns_none(isolated, monkeypatch):
    inst, acct = _seed_inst_and_acct()
    svc.apply_equity_change(_make_grant_change(inst, acct))
    grant = GrantStore().all()[0]
    tranche_id = grant["tranches"][0]["id"]

    monkeypatch.setattr(
        svc.price_history,
        "as_of_maps",
        lambda days: {"2025-03-15": {}},  # no price for this instrument
    )

    result = svc.fmv_prefill(grant["id"], tranche_id)
    assert result["fmv_per_unit"] is None
    assert result["source"] == "none"


# ── Double-count guard (vest income ↔ tax._employment_income_section) ─────────────


def test_vest_writes_no_income_row_at_all(isolated):
    """A vest can never be miscounted as income of any kind — dividend, interest or
    employment — because it writes no ledger row at all. Supersedes the old
    "vest txn must not look like investment income" guard, which policed the tags and
    income-event links of a transaction that no longer exists
    (ADR 2026-09-22-a-vest-books-no-income-transaction)."""
    inst, acct = _seed_inst_and_acct()
    svc.apply_equity_change(_make_grant_change(inst, acct))
    grant = GrantStore().all()[0]

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
    # The compensation fact is not lost — it lives on the vest event.
    ev = VestEventStore().all()[0]
    assert ev["gross_gain_native"] == pytest.approx(9000.0)


def test_vest_over_units_refused(isolated):
    """Requesting more units than the tranche holds must raise ValidationError."""
    inst, acct = _seed_inst_and_acct()
    svc.apply_equity_change(
        _make_grant_change(inst, acct, tranches=[{"vest_date": "2025-03-15", "units": 100.0}])
    )
    grant = GrantStore().all()[0]
    tranche_id = grant["tranches"][0]["id"]

    with pytest.raises(Exception, match="cannot vest more units than the tranche"):
        svc.apply_equity_change(
            {
                "type": "vest_apply",
                "grant_id": grant["id"],
                "tranche_id": tranche_id,
                "units": 200.0,  # tranche only has 100
                "fmv_per_unit": 10.0,
                "fmv_currency": "SGD",
                "vest_date": "2025-03-15",
            }
        )


def test_vest_nan_fmv_refused(isolated):
    """NaN fmv_per_unit must be rejected with a typed ValidationError."""
    inst, acct = _seed_inst_and_acct()
    svc.apply_equity_change(
        _make_grant_change(inst, acct, tranches=[{"vest_date": "2025-03-15", "units": 100.0}])
    )
    grant = GrantStore().all()[0]
    tranche_id = grant["tranches"][0]["id"]

    with pytest.raises(Exception, match="finite"):
        svc.apply_equity_change(
            {
                "type": "vest_apply",
                "grant_id": grant["id"],
                "tranche_id": tranche_id,
                "fmv_per_unit": float("nan"),
                "fmv_currency": "SGD",
                "vest_date": "2025-03-15",
            }
        )


def test_vest_inf_fmv_refused(isolated):
    """inf fmv_per_unit must be rejected."""
    inst, acct = _seed_inst_and_acct()
    svc.apply_equity_change(
        _make_grant_change(inst, acct, tranches=[{"vest_date": "2025-03-15", "units": 100.0}])
    )
    grant = GrantStore().all()[0]
    tranche_id = grant["tranches"][0]["id"]

    with pytest.raises(Exception, match="finite"):
        svc.apply_equity_change(
            {
                "type": "vest_apply",
                "grant_id": grant["id"],
                "tranche_id": tranche_id,
                "fmv_per_unit": float("inf"),
                "fmv_currency": "SGD",
                "vest_date": "2025-03-15",
            }
        )


def test_vest_nan_price_paid_refused(isolated):
    """NaN price_paid_per_unit must be rejected."""
    inst, acct = _seed_inst_and_acct()
    svc.apply_equity_change(
        _make_grant_change(inst, acct, tranches=[{"vest_date": "2025-03-15", "units": 100.0}])
    )
    grant = GrantStore().all()[0]
    tranche_id = grant["tranches"][0]["id"]

    with pytest.raises(Exception, match="finite"):
        svc.apply_equity_change(
            {
                "type": "vest_apply",
                "grant_id": grant["id"],
                "tranche_id": tranche_id,
                "fmv_per_unit": 10.0,
                "price_paid_per_unit": float("nan"),
                "fmv_currency": "SGD",
                "vest_date": "2025-03-15",
            }
        )


def test_vest_negative_fmv_refused(isolated):
    """Negative fmv_per_unit must be rejected."""
    inst, acct = _seed_inst_and_acct()
    svc.apply_equity_change(
        _make_grant_change(inst, acct, tranches=[{"vest_date": "2025-03-15", "units": 100.0}])
    )
    grant = GrantStore().all()[0]
    tranche_id = grant["tranches"][0]["id"]

    with pytest.raises(Exception, match="finite|≥ 0"):
        svc.apply_equity_change(
            {
                "type": "vest_apply",
                "grant_id": grant["id"],
                "tranche_id": tranche_id,
                "fmv_per_unit": -1.0,
                "fmv_currency": "SGD",
                "vest_date": "2025-03-15",
            }
        )


# ── preview_vest (pure) ──────────────────────────────────────────────────────────────


def test_preview_vest_pure_no_writes(isolated):
    inst, acct = _seed_inst_and_acct()
    svc.apply_equity_change(_make_grant_change(inst, acct))
    grant = GrantStore().all()[0]
    tranche_id = grant["tranches"][0]["id"]

    preview = svc.preview_vest(
        {
            "grant_id": grant["id"],
            "tranche_id": tranche_id,
            "fmv_per_unit": 30.0,
            "fmv_currency": "SGD",
        }
    )
    assert "error" not in preview
    assert preview["gross_gain_native"] == pytest.approx(9000.0)
    # The preview must render exactly what the write stores — and a vest writes no
    # ledger row, so it may not promise one (the old `income_txn_posted` flag did).
    assert "income_txn_posted" not in preview
    assert preview["tax_event"] == "at_vest"

    # No side effects.
    assert TransactionStore().all() == []
    assert VestEventStore().all() == []


def test_preview_vest_carries_tax_event_as_information_only(isolated):
    """tax_event no longer changes what a vest books; the preview passes it through as a
    fact for the card to show, and promises no transaction either way."""
    inst, acct = _seed_inst_and_acct()
    svc.apply_equity_change(_make_grant_change(inst, acct, tax_event="deferred"))
    grant = GrantStore().all()[0]
    tranche_id = grant["tranches"][0]["id"]

    preview = svc.preview_vest(
        {
            "grant_id": grant["id"],
            "tranche_id": tranche_id,
            "fmv_per_unit": 30.0,
            "fmv_currency": "SGD",
        }
    )
    assert preview["tax_event"] == "deferred"
    assert "income_txn_posted" not in preview


# ── grant_price_per_unit — reference field, no cost-basis / tax impact ─────────────


def test_grant_create_with_price_persists(isolated):
    """grant_price_per_unit is stored on the grant and returned via hydrated_grants."""
    inst, acct = _seed_inst_and_acct()
    change = _make_grant_change(inst, acct)
    change["grant_price_per_unit"] = 20.36
    svc.apply_equity_change(change)

    grant = GrantStore().all()[0]
    assert grant["grant_price_per_unit"] == pytest.approx(20.36)

    # hydrated_grants passes the field through.
    hydrated = svc.hydrated_grants()
    assert hydrated[0]["grant_price_per_unit"] == pytest.approx(20.36)


def test_grant_create_without_price_defaults_none(isolated):
    inst, acct = _seed_inst_and_acct()
    svc.apply_equity_change(_make_grant_change(inst, acct))
    grant = GrantStore().all()[0]
    assert grant.get("grant_price_per_unit") is None


def test_grant_update_sets_price(isolated):
    """grant_update can add or change grant_price_per_unit on an existing grant."""
    inst, acct = _seed_inst_and_acct()
    svc.apply_equity_change(_make_grant_change(inst, acct))
    grant = GrantStore().all()[0]

    result = svc.apply_equity_change(
        {"type": "grant_update", "grant_id": grant["id"], "grant_price_per_unit": 37.85}
    )
    assert result["ok"] is True
    updated = GrantStore().find_by_id(grant["id"])
    assert updated["grant_price_per_unit"] == pytest.approx(37.85)


def test_grant_update_clears_price_to_none(isolated):
    """grant_update accepts None to clear grant_price_per_unit."""
    inst, acct = _seed_inst_and_acct()
    change = _make_grant_change(inst, acct)
    change["grant_price_per_unit"] = 20.36
    svc.apply_equity_change(change)
    grant = GrantStore().all()[0]

    svc.apply_equity_change(
        {"type": "grant_update", "grant_id": grant["id"], "grant_price_per_unit": None}
    )
    updated = GrantStore().find_by_id(grant["id"])
    assert updated.get("grant_price_per_unit") is None


def test_grant_price_nan_refused(isolated):
    """NaN grant_price_per_unit must be rejected at create."""
    inst, acct = _seed_inst_and_acct()
    change = _make_grant_change(inst, acct)
    change["grant_price_per_unit"] = float("nan")
    with pytest.raises(Exception, match="finite"):
        svc.apply_equity_change(change)


def test_grant_price_negative_refused(isolated):
    """Negative grant_price_per_unit must be rejected at create."""
    inst, acct = _seed_inst_and_acct()
    change = _make_grant_change(inst, acct)
    change["grant_price_per_unit"] = -5.0
    with pytest.raises(Exception, match="≥ 0|finite"):
        svc.apply_equity_change(change)


def test_grant_price_nan_refused_on_update(isolated):
    """NaN grant_price_per_unit must be rejected at update."""
    inst, acct = _seed_inst_and_acct()
    svc.apply_equity_change(_make_grant_change(inst, acct))
    grant = GrantStore().all()[0]
    with pytest.raises(Exception, match="finite"):
        svc.apply_equity_change(
            {"type": "grant_update", "grant_id": grant["id"], "grant_price_per_unit": float("nan")}
        )


def test_grant_price_does_not_enter_vest_lot_cost_basis(isolated):
    """grant_price_per_unit must NOT affect the FMV-basis lot created at vest.
    The lot's unit_cost must equal fmv_per_unit, not grant_price_per_unit."""
    inst, acct = _seed_inst_and_acct()
    change = _make_grant_change(inst, acct, tranches=[{"vest_date": "2025-03-15", "units": 100.0}])
    change["grant_price_per_unit"] = 20.36  # grant price — must NOT affect vest lot
    svc.apply_equity_change(change)
    grant = GrantStore().all()[0]

    svc.apply_equity_change(
        {
            "type": "vest_apply",
            "grant_id": grant["id"],
            "tranche_id": grant["tranches"][0]["id"],
            "fmv_per_unit": 37.85,  # vest-date FMV — this is the correct basis
            "fmv_currency": "SGD",
            "vest_date": "2025-03-15",
        }
    )
    lot = LotStore().all()[0]
    assert lot["unit_cost"] == pytest.approx(37.85), (
        "lot basis must be vest-date FMV, not grant price"
    )
    assert lot["unit_cost"] != pytest.approx(20.36)


# ── grant_create: account required ──────────────────────────────────────────────


def test_grant_create_without_account_refuses(isolated):
    """grant_create without account_id or account_name must raise ValidationError."""
    inst, _ = _seed_inst_and_acct()
    with pytest.raises(Exception, match="account is required"):
        svc.apply_equity_change(
            {
                "type": "grant_create",
                "instrument_id": inst["id"],
                "grant_date": "2024-03-15",
                "total_units": 100.0,
                "tax_event": "at_vest",
                "tranches": [{"vest_date": "2025-03-15", "units": 100.0}],
                # neither account_id nor account_name
            }
        )


def test_grant_create_refuses_an_unknown_account_id_before_writing(isolated):
    # 2026-10-13 audit: grant.account_id was stored as given, and the locked position path
    # minted an empty "Brokerage" account for an id it couldn't find.
    from backend.core.errors import ValidationError

    inst, acct = _seed_inst_and_acct()
    change = {**_make_grant_change(inst, acct), "account_id": "acc_gone"}
    with pytest.raises(ValidationError, match="Unknown account 'acc_gone'"):
        svc.apply_equity_change(change)
    assert GrantStore().all() == []
    assert PositionStore().all() == []
    assert [a["id"] for a in AccountStore().all()] == [acct["id"]]


def test_grant_create_by_account_name_records_the_custodian_id(isolated):
    # It used to leave grant.account_id None — vests had nothing to fall back to.
    inst, acct = _seed_inst_and_acct()
    change = {
        **_make_grant_change(inst, acct),
        "account_id": None,
        "account_name": "Employer Equity",
    }
    change.pop("account_id")
    svc.apply_equity_change(change)
    assert GrantStore().all()[0]["account_id"] == acct["id"]


def test_grant_update_refuses_an_unknown_account_id(isolated):
    from backend.core.errors import ValidationError

    inst, acct = _seed_inst_and_acct()
    svc.apply_equity_change(_make_grant_change(inst, acct))
    grant = GrantStore().all()[0]
    with pytest.raises(ValidationError):
        svc.apply_equity_change(
            {"type": "grant_update", "grant_id": grant["id"], "account_id": "acc_gone"}
        )
    assert GrantStore().find_by_id(grant["id"])["account_id"] == acct["id"]
    assert len(AccountStore().all()) == 1


def test_grant_create_with_locked_position_id_skips_account_requirement(isolated):
    """When adopting an existing locked position, account is derived from it (no check)."""
    inst, acct = _seed_inst_and_acct()
    # A pre-existing locked row (a book from before grants owned it) — planted raw: only
    # the equity choke may create an unvested position now.
    locked_pos_id = PositionStore().create(
        {
            "instrument_id": inst["id"],
            "account_id": acct["id"],
            "quantity": 37.0,
            "vesting_state": "unvested",
        }
    )["id"]

    # No account_id/account_name provided — but locked_position_id is present.
    r = svc.apply_equity_change(
        {
            "type": "grant_create",
            "instrument_id": inst["id"],
            "grant_date": "2024-03-15",
            "total_units": 100.0,
            "tax_event": "at_vest",
            "tranches": [{"vest_date": "2025-03-15", "units": 100.0}],
            "locked_position_id": locked_pos_id,
        }
    )
    assert r["ok"] is True
    assert r["locked_position_id"] == locked_pos_id
    # Adopted, its units now follow the grant's schedule — not whatever it held before.
    assert PositionStore().find_by_id(locked_pos_id)["quantity"] == 100.0


# ── grant_update: account move ────────────────────────────────────────────────────


def test_grant_update_account_id_moves_grant_and_locked_position(isolated):
    """grant_update with account_id must update grant.account_id AND locked position.account_id."""
    inst, acct_a = _seed_inst_and_acct()
    acct_b = AccountStore().create(
        {"name": "Crestpoint", "kind": "asset", "type": "investment", "currency": "SGD"}
    )
    svc.apply_equity_change(_make_grant_change(inst, acct_a))
    grant = GrantStore().all()[0]
    locked_id = grant["locked_position_id"]

    # Confirm locked position starts in acct_a.
    locked_before = PositionStore().find_by_id(locked_id)
    assert locked_before["account_id"] == acct_a["id"]

    result = svc.apply_equity_change(
        {"type": "grant_update", "grant_id": grant["id"], "account_id": acct_b["id"]}
    )
    assert result["ok"] is True

    # grant.account_id updated.
    updated_grant = GrantStore().find_by_id(grant["id"])
    assert updated_grant["account_id"] == acct_b["id"]

    # locked position account_id updated.
    locked_after = PositionStore().find_by_id(locked_id)
    assert locked_after["account_id"] == acct_b["id"]


def test_grant_update_account_name_resolves_and_moves(isolated):
    """grant_update with account_name creates/reuses the account and moves both records."""
    inst, acct_a = _seed_inst_and_acct()
    svc.apply_equity_change(_make_grant_change(inst, acct_a))
    grant = GrantStore().all()[0]
    locked_id = grant["locked_position_id"]

    result = svc.apply_equity_change(
        {"type": "grant_update", "grant_id": grant["id"], "account_name": "Vanguard"}
    )
    assert result["ok"] is True

    # A new account "Vanguard" was created.
    all_accts = AccountStore().all()
    vanguard = next((a for a in all_accts if a["name"] == "Vanguard"), None)
    assert vanguard is not None

    # locked position moved there.
    locked = PositionStore().find_by_id(locked_id)
    assert locked["account_id"] == vanguard["id"]


# ── vest_apply: account derived from locked position's CURRENT account ────────────


def test_vest_after_position_move_lands_in_moved_account(isolated):
    """Regression for the production bug: when a locked position is moved to a different
    account (via update_position), vest_apply must vest into that new account — NOT the
    original grant.account_id."""
    from backend.services import positions as positions_service

    inst, acct_a = _seed_inst_and_acct()
    acct_b = AccountStore().create(
        {"name": "Crestpoint", "kind": "asset", "type": "investment", "currency": "SGD"}
    )
    svc.apply_equity_change(_make_grant_change(inst, acct_a))
    grant = GrantStore().all()[0]
    locked_id = grant["locked_position_id"]

    # Move the locked position to acct_b WITHOUT updating the grant record.
    positions_service.apply_position_change(
        {
            "type": "update",
            "position_id": locked_id,
            "changes": {"account_id": acct_b["id"]},
        }
    )

    # Vest the first tranche.
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
    assert vest_result["ok"] is True

    assert TransactionStore().all() == []  # a vest books no ledger row

    # The liquid sibling position must also be in acct_b.
    liq_positions = [
        p
        for p in PositionStore().all()
        if p.get("vesting_state") == "vested" and p.get("instrument_id") == inst["id"]
    ]
    assert len(liq_positions) == 1
    assert liq_positions[0]["account_id"] == acct_b["id"]


# ── hydrated_grants: account_id + account_name in output ───────────────────────


def test_hydrated_grants_includes_account_info(isolated):
    """hydrated_grants must expose account_id and account_name derived from locked position."""
    inst, acct = _seed_inst_and_acct()
    svc.apply_equity_change(_make_grant_change(inst, acct))

    hydrated = svc.hydrated_grants()
    assert len(hydrated) == 1
    g = hydrated[0]
    assert g["account_id"] == acct["id"]
    assert g["account_name"] == "Employer Equity"


def test_hydrated_grants_account_reflects_moved_position(isolated):
    """After moving the locked position, hydrated_grants must report the new account."""
    from backend.services import positions as positions_service

    inst, acct_a = _seed_inst_and_acct()
    acct_b = AccountStore().create(
        {"name": "Crestpoint", "kind": "asset", "type": "investment", "currency": "SGD"}
    )
    svc.apply_equity_change(_make_grant_change(inst, acct_a))
    grant = GrantStore().all()[0]
    locked_id = grant["locked_position_id"]

    positions_service.apply_position_change(
        {"type": "update", "position_id": locked_id, "changes": {"account_id": acct_b["id"]}}
    )

    hydrated = svc.hydrated_grants()
    g = hydrated[0]
    assert g["account_id"] == acct_b["id"]
    assert g["account_name"] == "Crestpoint"


# ── EQ-NW-ONCE: a vest moves net worth by the share value, exactly once ──────────────


def test_vest_moves_net_worth_by_the_shares_only(isolated):
    """The bug this rule exists to prevent (owner report, 2026-09-22): vesting booked an
    income row into the custodian account AND minted the share lot, so net worth rose by
    twice the vest — once as money that never arrived, once as the shares actually
    delivered. On a real book that overstated net worth by ~S$26k for a single vest.

    Before the vest the units are unvested and excluded from net worth entirely
    (ADR 2026-07-31); after it they are owned. So the delta must be exactly the vest's
    market value — no more."""
    from backend.services import ledger as led

    inst, acct = _seed_inst_and_acct()
    svc.apply_equity_change(_make_grant_change(inst, acct))
    grant = GrantStore().all()[0]

    # Price the instrument so the vested position carries a real market value.
    from backend.db.price_history_store import PriceHistoryStore

    PriceHistoryStore().record("ONWAI", 30.0)

    before = led.net_worth(base="SGD")
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
    after = led.net_worth(base="SGD")

    vest_value = 300.0 * 30.0
    delta = after["net_worth"] - before["net_worth"]
    assert delta == pytest.approx(vest_value), (
        f"a {vest_value} vest moved net worth by {delta} — "
        "2x means the income row is back and the vest is double-counted"
    )
    # And specifically: the custodian account gained no phantom cash.
    assert after["breakdown"]["accounts_asset"] == pytest.approx(
        before["breakdown"]["accounts_asset"]
    )
