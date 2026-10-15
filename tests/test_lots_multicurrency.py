"""
Multi-currency cost-basis tests for backend.services.lots (P2-INV1 AC-10, AC-11).

Realized gain is computed in the PROCEEDS currency (the lot basis converted INTO it
via core.fx — never mixing currencies in a subtraction), then the aggregate gain is
normalized to the portfolio base via core.fx.to_base. The event stores the fx_rate +
realized_gain_base snapshot. Offline, FX monkeypatched (mirrors test_char_ledger_math).
"""

from __future__ import annotations

import pytest

from backend.services import lots as svc

# Base SGD. 1 USD = 1.35 SGD, 1 EUR = 1.50 SGD. Pivoted on SGD.
_FX = {"SGD": 1.0, "USD": 1.35, "EUR": 1.50}


def _lot(lot_id, qty, unit_cost, ccy, acquired_at="2026-01-01"):
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


_POS = {"id": "pos-1", "instrument_id": "inst-1", "account_id": "acc-1", "cost_currency": "USD"}


def test_ac10_usd_lot_sgd_base():
    """Buy 10 @ USD $100, sell 10 @ USD $130, base = SGD, 1 USD = 1.35 SGD.
    Gain in proceeds ccy = +USD 300.00; gain in base = +SGD 405.00; fx_rate = 1.35."""
    lots = [_lot("L1", 10, 100.0, "USD")]
    draft = svc.compute_disposal(
        _POS,
        lots,
        {
            "quantity": 10,
            "proceeds_per_unit": 130.0,
            "proceeds_currency": "USD",
            "method": "fifo",
            "disposed_at": "2026-06-01",
        },
        rates=_FX,
        base="SGD",
    )
    assert draft["realized_gain"] == 300.00  # USD (proceeds ccy)
    assert draft["proceeds_currency"] == "USD"
    assert draft["realized_gain_base"] == 405.00  # 300 × 1.35 SGD
    assert draft["base_currency"] == "SGD"
    assert draft["fx_rate"] == 1.35


def test_ac10_worked_example_fifo_350_to_472_50():
    """RFC appendix: FIFO 15 @ $130 over L1@100/L2@120 ⇒ +USD 350.00; base SGD = 472.50."""
    lots = [_lot("L1", 10, 100.0, "USD", "2026-01-01"), _lot("L2", 10, 120.0, "USD", "2026-03-01")]
    draft = svc.compute_disposal(
        _POS,
        lots,
        {"quantity": 15, "proceeds_per_unit": 130.0, "proceeds_currency": "USD", "method": "fifo"},
        rates=_FX,
        base="SGD",
    )
    assert draft["realized_gain"] == 350.00
    assert draft["realized_gain_base"] == 472.50  # 350 × 1.35


def test_ac11_lot_currency_differs_from_proceeds_currency():
    """A EUR-cost lot disposed for USD proceeds: convert the EUR basis INTO USD via FX
    before subtracting — currencies never mixed in a subtraction.

    Lot cost 100 EUR; 1 EUR = 1.50 SGD, 1 USD = 1.35 SGD ⇒ 1000 EUR (the FULL take basis)
    = 1500 SGD = 1500/1.35 USD = 1111.11 USD. Sell 10 @ 130 USD ⇒ 1300 − 1111.11 =
    +188.89 USD (proceeds ccy). The basis is converted on the FULL take (not per-unit
    then ×qty), so the cent-rounding is NOT magnified by quantity.
    """
    lots = [_lot("L1", 10, 100.0, "EUR")]
    draft = svc.compute_disposal(
        _POS,
        lots,
        {"quantity": 10, "proceeds_per_unit": 130.0, "proceeds_currency": "USD", "method": "fifo"},
        rates=_FX,
        base="SGD",
    )
    # FULL basis 1000 EUR → USD via SGD pivot: 1000 × 1.50 / 1.35 = 1111.11; 1300 − 1111.11.
    total_cost_in_usd = svc.to_base(1000.0, "EUR", "USD", rates=_FX)
    assert total_cost_in_usd == pytest.approx(1111.11, abs=0.01)
    expected = round(130.0 * 10 - total_cost_in_usd, 2)
    assert draft["realized_gain"] == 188.89
    assert draft["realized_gain"] == expected
    assert draft["proceeds_currency"] == "USD"
    # And the base figure converts the USD gain into SGD.
    assert draft["realized_gain_base"] == pytest.approx(round(expected * 1.35, 2), abs=0.01)


def test_basis_converted_on_full_take_not_rounded_per_unit_same_currency():
    """REGRESSION (P2-INV1 review): the lot basis must be applied on the FULL take, not
    rounded to 2dp per-unit then ×qty. A non-2dp unit cost (e.g. a crypto / migrated
    weighted-avg basis) with a large qty would otherwise magnify the cent-rounding.

    Same currency (USD base, USD proceeds — no FX): unit_cost 0.0123, sell 1,000,000 @
    0.0150. EXACT gain = 1,000,000 × (0.0150 − 0.0123) = +$2,700.00. The old per-unit
    round path booked 0.0123 → round(0.0123,2)=0.01, gain 1,000,000×(0.015−0.01)=+$5,000.
    """
    lots = [_lot("L1", 1_000_000, 0.0123, "USD")]
    draft = svc.compute_disposal(
        _POS,
        lots,
        {
            "quantity": 1_000_000,
            "proceeds_per_unit": 0.0150,
            "proceeds_currency": "USD",
            "method": "fifo",
        },
        rates=_FX,
        base="SGD",
    )
    assert draft["realized_gain"] == 2700.00  # NOT 5000.00 (the magnified-round bug)


def test_basis_converted_on_full_take_not_rounded_per_unit_cross_currency():
    """REGRESSION (P2-INV1 review): cross-currency variant. EUR-cost lot, USD proceeds,
    fractional unit cost, large qty. The EUR basis is converted on the FULL take once.

    unit_cost 0.0123 EUR, qty 1,000,000, proceeds 0.0150 USD. FULL EUR basis = 12,300 EUR
    → USD via SGD pivot: 12,300 × 1.50/1.35 = 13,666.67 USD. Proceeds = 15,000 USD.
    EXACT gain = 15,000 − 13,666.67 = +$1,333.33 USD. The old per-unit-round path booked
    round(0.0123 EUR→USD, 2) = round(0.013666…,2) = 0.01 → 1,000,000×(0.015−0.01)=+$5,000.
    """
    lots = [_lot("L1", 1_000_000, 0.0123, "EUR")]
    draft = svc.compute_disposal(
        _POS,
        lots,
        {
            "quantity": 1_000_000,
            "proceeds_per_unit": 0.0150,
            "proceeds_currency": "USD",
            "method": "fifo",
        },
        rates=_FX,
        base="SGD",
    )
    total_cost_in_usd = svc.to_base(12_300.0, "EUR", "USD", rates=_FX)
    expected = round(0.0150 * 1_000_000 - total_cost_in_usd, 2)
    assert draft["realized_gain"] == 1333.33  # NOT 5000.00 (the magnified-round bug)
    assert draft["realized_gain"] == expected


def test_realized_pnl_recomputes_base_from_native_not_stored(monkeypatch):
    """realized_pnl recomputes *_base from the native gain + CURRENT fx (D4), so a stored
    realized_gain_base snapshot taken under a different base does NOT leak through."""

    class _FakeEvents:
        def query_events(self, **kw):
            return [
                {
                    "id": "e1",
                    "instrument_id": "inst-1",
                    "disposed_at": "2026-06-01",
                    "proceeds_currency": "USD",
                    "realized_gain": 300.0,
                    "realized_gain_base": 99999.0,  # STALE snapshot — must be ignored
                    "consumptions": [{"qty": 10, "term": "long", "realized_gain": 300.0}],
                }
            ]

    monkeypatch.setattr(svc, "RealizedEventStore", lambda: _FakeEvents())
    monkeypatch.setattr(svc, "_load_rates", lambda: _FX)
    out = svc.realized_pnl(base="SGD")
    # 300 USD × 1.35 = 405 SGD, recomputed — NOT the stale 99999.
    assert out["realized_total_base"] == 405.00
    assert out["events"][0]["realized_gain_base"] == 405.00
    assert out["by_term"]["long"] == 405.00


def test_realized_pnl_hydrates_instrument_name(monkeypatch):
    """The read path joins instrument_id → name/symbol so a FULLY-SOLD holding (which has
    dropped out of the live portfolio, so the UI can't resolve the id from held rows) still
    labels by name instead of showing the raw UUID (operator-reported regression)."""

    class _FakeEvents:
        def query_events(self, **kw):
            return [
                {
                    "id": "e1",
                    "instrument_id": "inst-aapl",
                    "disposed_at": "2026-06-05",
                    "proceeds_currency": "USD",
                    "realized_gain": 100.0,
                    "consumptions": [{"qty": 10, "term": "unknown", "realized_gain": 100.0}],
                }
            ]

    class _FakeInstruments:
        def all(self):
            return [{"id": "inst-aapl", "symbol": "AAPL", "name": "Apple Inc."}]

    monkeypatch.setattr(svc, "RealizedEventStore", lambda: _FakeEvents())
    monkeypatch.setattr(svc, "instruments", _FakeInstruments())
    monkeypatch.setattr(svc, "_load_rates", lambda: _FX)
    out = svc.realized_pnl(base="USD")
    ev = out["events"][0]
    assert ev["instrument_name"] == "Apple Inc."
    assert ev["instrument_symbol"] == "AAPL"
