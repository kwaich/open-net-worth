"""
Error / boundary tests for backend.services.lots (P2-INV1).

AC-7  specific-ID over-allocation / unknown lot → InvalidLotSelection, no mutation.
AC-12 unknown cost basis → realized_gain None (not 0), cost_basis_known False.
AC-13 oversell whole position → InsufficientLots.
AC-14 empty realized ledger → [] + realized_total_base 0.0 (no crash).
AC-15 zero-proceeds / transfer-out → realized_gain None, is_taxable False.
Offline; no stores, no network.
"""

from __future__ import annotations

import pytest

from backend.core.errors import InsufficientLots, InvalidLotSelection
from backend.services import lots as svc

_USD = {"USD": 1.0}
_POS = {"id": "pos-1", "instrument_id": "inst-1", "account_id": "acc-1", "cost_currency": "USD"}


def _lot(lot_id, qty, unit_cost, acquired_at="2026-01-01", ccy="USD"):
    return {
        "id": lot_id,
        "position_id": "pos-1",
        "instrument_id": "inst-1",
        "account_id": "acc-1",
        "acquired_at": acquired_at,
        "original_qty": qty,
        "remaining_qty": qty,
        "unit_cost": unit_cost,
        "cost_currency": ccy,
        "fees": 0.0,
        "kind": "buy",
        "created_at": f"{acquired_at}T00:00:00Z",
    }


def _dispose(qty, price, **kw):
    d = {"quantity": qty, "proceeds_per_unit": price, "proceeds_currency": "USD", "method": "fifo"}
    d.update(kw)
    return d


# ── AC-12 unknown cost basis ─────────────────────────────────────────────────────


def test_ac12_unknown_cost_books_none_gain_not_zero():
    lots = [_lot("L1", 10, None)]  # migrated row with unknown cost
    draft = svc.compute_disposal(_POS, lots, _dispose(10, 130.0), rates=_USD, base="USD")
    assert draft["realized_gain"] is None
    assert draft["cost_basis_known"] is False
    assert draft["consumptions"][0]["realized_gain"] is None
    # Proceeds are still recorded (the disposal happened).
    assert draft["proceeds_per_unit"] == 130.0
    assert draft["quantity"] == 10.0


def test_ac12_mixed_known_and_unknown_lots_is_unknown_aggregate():
    lots = [_lot("L1", 10, 100.0), _lot("L2", 10, None, "2026-03-01")]
    draft = svc.compute_disposal(_POS, lots, _dispose(15, 130.0), rates=_USD, base="USD")
    # One consumed lot has unknown cost ⇒ the whole disposal gain is unknown.
    assert draft["cost_basis_known"] is False
    assert draft["realized_gain"] is None
    assert draft["realized_gain_base"] is None


# ── AC-13 oversell ───────────────────────────────────────────────────────────────


def test_ac13_oversell_raises_insufficient_lots():
    lots = [_lot("L1", 5, 100.0)]
    with pytest.raises(InsufficientLots):
        svc.compute_disposal(_POS, lots, _dispose(6, 130.0), rates=_USD, base="USD")


def test_oversell_no_open_lots_raises():
    with pytest.raises(InsufficientLots):
        svc.compute_disposal(_POS, [], _dispose(1, 130.0), rates=_USD, base="USD")


# ── AC-7 specific-ID guards ──────────────────────────────────────────────────────


def test_ac7_specific_qty_must_sum_to_disposal_qty():
    lots = [_lot("L1", 10, 100.0), _lot("L2", 10, 120.0, "2026-03-01")]
    with pytest.raises(InvalidLotSelection):
        svc.compute_disposal(
            _POS,
            lots,
            _dispose(10, 130.0, method="specific_id", lot_selection=[{"lot_id": "L1", "qty": 3}]),
            rates=_USD,
            base="USD",
        )


def test_specific_id_requires_selection():
    lots = [_lot("L1", 10, 100.0)]
    with pytest.raises(InvalidLotSelection):
        svc.compute_disposal(
            _POS, lots, _dispose(5, 130.0, method="specific_id"), rates=_USD, base="USD"
        )


def test_unknown_method_rejected():
    lots = [_lot("L1", 10, 100.0)]
    with pytest.raises(InvalidLotSelection):
        svc.compute_disposal(_POS, lots, _dispose(5, 130.0, method="hifo"), rates=_USD, base="USD")


def test_nonpositive_quantity_rejected():
    lots = [_lot("L1", 10, 100.0)]
    with pytest.raises(InvalidLotSelection):
        svc.compute_disposal(_POS, lots, _dispose(0, 130.0), rates=_USD, base="USD")


# ── AC-15 zero-proceeds / transfer-out ───────────────────────────────────────────


def test_ac15_transfer_out_books_none_gain_and_not_taxable():
    lots = [_lot("L1", 10, 100.0)]
    draft = svc.compute_disposal(
        _POS, lots, _dispose(10, 0.0, is_taxable=False), rates=_USD, base="USD"
    )
    assert draft["is_taxable"] is False
    assert draft["realized_gain"] is None  # carries basis out, realizes nothing
    assert draft["realized_gain_base"] is None
    # The consumed lots + holding period are still recorded for audit.
    assert draft["consumptions"][0]["lot_id"] == "L1"
    assert draft["consumptions"][0]["qty"] == 10.0


# ── P2-INV1c: taxable zero-proceeds money guard (no fabricated loss) ──────────────


def test_taxable_zero_proceeds_is_rejected_not_a_fake_loss():
    # A taxable sale with no proceeds would book the full cost basis (-1000) as a loss.
    # The service must refuse it BEFORE any write rather than silently fabricate the loss.
    lots = [_lot("L1", 10, 100.0)]
    with pytest.raises(InvalidLotSelection):
        svc.compute_disposal(_POS, lots, _dispose(10, 0.0), rates=_USD, base="USD")


def test_taxable_proceeds_fully_eaten_by_fees_is_rejected():
    # Net proceeds = 10 × 5 − fees 50 = 0 ⇒ still a fabricated-loss trap; refuse it.
    lots = [_lot("L1", 10, 100.0)]
    with pytest.raises(InvalidLotSelection):
        svc.compute_disposal(
            _POS, lots, _dispose(10, 5.0, disposal_fees=50.0), rates=_USD, base="USD"
        )


def test_transfer_out_zero_proceeds_still_allowed():
    # The intentional $0 path (gift / write-off) is is_taxable=False — must still pass.
    lots = [_lot("L1", 10, 100.0)]
    draft = svc.compute_disposal(
        _POS, lots, _dispose(10, 0.0, is_taxable=False), rates=_USD, base="USD"
    )
    assert draft["realized_gain"] is None


# ── AC-14 empty ledger ───────────────────────────────────────────────────────────


def test_ac14_empty_realized_ledger(monkeypatch):
    class _Empty:
        def query_events(self, **kw):
            return []

    monkeypatch.setattr(svc, "RealizedEventStore", lambda: _Empty())
    monkeypatch.setattr(svc, "_load_rates", lambda: _USD)
    out = svc.realized_pnl(base="USD")
    assert out["events"] == []
    assert out["realized_total_base"] == 0.0
    assert out["by_term"] == {"long": 0.0, "short": 0.0, "unknown": 0.0}
    assert out["by_instrument"] == {}
