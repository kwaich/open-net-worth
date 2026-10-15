"""
Apply-path + idempotency tests for backend.services.lots (P2-INV1 AC-16).

These exercise the REAL stores in a tmp_path (no live data, per the project rule):
apply_lot_change writes lots.json + realized_events.json + resyncs positions.json,
and a second apply with the same idempotency_key is a no-op (no double-consume).
Also pins LotStore CRUD / FIFO ordering / bulk_consume.
"""

from __future__ import annotations

import pytest

from backend.db.lot_store import LotStore
from backend.db.position_store import PositionStore
from backend.db.realized_event_store import RealizedEventStore
from backend.services import lots as svc


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    """Redirect lot / realized-event / position files into a temp dir."""
    # Single-base USD so FX is identity (no fx_cache file needed).
    monkeypatch.setattr(svc, "_load_rates", lambda: {"USD": 1.0})
    monkeypatch.setattr(svc, "base_currency", lambda: "USD")
    return tmp_path


def _seed_position():
    return PositionStore().create(
        {"instrument_id": "inst-1", "account_id": "acc-1", "quantity": 0, "cost_currency": "USD"}
    )


# ── LotStore CRUD / ordering / consume ───────────────────────────────────────────


def test_lot_store_create_defaults_and_fifo_order(isolated):
    s = LotStore()
    s.create(
        {"position_id": "p1", "acquired_at": "2026-03-01", "original_qty": 10, "unit_cost": 120}
    )
    s.create(
        {"position_id": "p1", "acquired_at": "2026-01-01", "original_qty": 10, "unit_cost": 100}
    )
    order = [lot["acquired_at"] for lot in s.open_lots_for_position("p1")]
    assert order == ["2026-01-01", "2026-03-01"]  # FIFO by acquired_at


def test_lot_store_zero_cost_is_unknown(isolated):
    s = LotStore()
    lot = s.create({"position_id": "p1", "original_qty": 5, "unit_cost": 0})
    assert lot["unit_cost"] is None  # 0 → unknown


def test_lot_store_bulk_consume_closes_lot(isolated):
    s = LotStore()
    lot = s.create(
        {"position_id": "p1", "acquired_at": "2026-01-01", "original_qty": 10, "unit_cost": 100}
    )
    s.bulk_consume([(lot["id"], 10)])
    refreshed = s.find_by_id(lot["id"])
    assert refreshed["remaining_qty"] == 0.0
    assert s.open_lots_for_position("p1") == []  # closed lot dropped from open queue


# ── apply acquire + dispose (AC-16) ──────────────────────────────────────────────


def test_apply_acquire_creates_lot_and_resyncs_position(isolated):
    pos = _seed_position()
    out = svc.apply_lot_change(
        {
            "type": "acquire",
            "position_id": pos["id"],
            "quantity": 10,
            "unit_cost": 100,
            "cost_currency": "USD",
            "acquired_at": "2026-01-01",
        }
    )
    assert out["ok"] is True
    # Position aggregates resynced from the lot.
    refreshed = PositionStore().find_by_id(pos["id"])
    assert refreshed["quantity"] == 10.0
    assert refreshed["cost_basis"] == 100.0


def test_apply_dispose_books_event_consumes_lots_and_resyncs(isolated):
    pos = _seed_position()
    svc.apply_lot_change(
        {
            "type": "acquire",
            "position_id": pos["id"],
            "quantity": 10,
            "unit_cost": 100,
            "cost_currency": "USD",
            "acquired_at": "2026-01-01",
        }
    )
    out = svc.apply_lot_change(
        {
            "type": "dispose",
            "position_id": pos["id"],
            "quantity": 4,
            "proceeds_per_unit": 130,
            "proceeds_currency": "USD",
            "method": "fifo",
            "disposed_at": "2026-06-01",
        }
    )
    assert out["ok"] is True
    assert out["realized_gain"] == 120.00  # 4 × (130 − 100)
    # One realized event written.
    assert len(RealizedEventStore().all()) == 1
    # Lot consumed: remaining 6.
    open_lots = LotStore().open_lots_for_position(pos["id"])
    assert open_lots[0]["remaining_qty"] == 6.0
    # Position resynced.
    refreshed = PositionStore().find_by_id(pos["id"])
    assert refreshed["quantity"] == 6.0


def test_ac16_idempotent_dispose_no_double_consume(isolated):
    pos = _seed_position()
    svc.apply_lot_change(
        {
            "type": "acquire",
            "position_id": pos["id"],
            "quantity": 10,
            "unit_cost": 100,
            "cost_currency": "USD",
            "acquired_at": "2026-01-01",
        }
    )
    change = {
        "type": "dispose",
        "position_id": pos["id"],
        "quantity": 4,
        "proceeds_per_unit": 130,
        "proceeds_currency": "USD",
        "method": "fifo",
        "disposed_at": "2026-06-01",
        "idempotency_key": "abc-123",
    }
    first = svc.apply_lot_change(change)
    second = svc.apply_lot_change(change)  # re-apply (e.g. post-restore)

    assert second.get("idempotent") is True
    assert second["event_id"] == first["event_id"]
    # No double-consume: still exactly one event, lot remaining still 6 (not 2).
    assert len(RealizedEventStore().all()) == 1
    assert LotStore().open_lots_for_position(pos["id"])[0]["remaining_qty"] == 6.0


def test_apply_dispose_unknown_position(isolated):
    out = svc.apply_lot_change({"type": "dispose", "position_id": "nope", "quantity": 1})
    assert "error" in out


def test_apply_unknown_change_type(isolated):
    assert "error" in svc.apply_lot_change({"type": "bogus"})


def test_realized_pnl_end_to_end(isolated):
    pos = _seed_position()
    svc.apply_lot_change(
        {
            "type": "acquire",
            "position_id": pos["id"],
            "quantity": 10,
            "unit_cost": 100,
            "cost_currency": "USD",
            "acquired_at": "2026-01-01",
        }
    )
    svc.apply_lot_change(
        {
            "type": "dispose",
            "position_id": pos["id"],
            "quantity": 10,
            "proceeds_per_unit": 130,
            "proceeds_currency": "USD",
            "method": "fifo",
            "disposed_at": "2026-06-01",
        }
    )
    out = svc.realized_pnl(base="USD")
    assert out["realized_total_base"] == 300.00
    assert len(out["events"]) == 1
