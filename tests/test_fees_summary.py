"""
services.lots.fees_summary unit tests (P2-INV5).

Offline + isolated (stores monkeypatched to tmp_path, FX pinned), mirroring
tests/test_lots_service.py / test_char_ledger_math.py. Pins:
  - Σ acquisition (lot) fees + Σ disposal-event fees, totalled in base.
  - multi-currency fees convert to base via RECOMPUTED FX (not a stored snapshot), so a
    base-currency change re-converts (no stale figure).
  - empty book → all-zeros (no div-by-zero).
  - date / instrument filters narrow.
  - REGRESSION: a disposal's fee is counted ONCE in fees_summary AND already reduces
    proceeds in realized_pnl — fees are NOT double-subtracted from the realized gain.
"""

from __future__ import annotations

import pytest

from backend.services import lots as svc


@pytest.fixture
def isolated(tmp_path, monkeypatch, db_isolated):
    # accounts are DB-backed now (plan 3 task 4) — db_isolated (above) redirects the
    # process-wide DB singleton; no per-instance path-sync needed.
    # Default base USD with a EUR rate of 1.10 USD/EUR (rates are vs base=USD).
    rates = {"USD": 1.0, "EUR": 1.10}
    monkeypatch.setattr(svc, "_load_rates", lambda: rates)
    monkeypatch.setattr(svc, "base_currency", lambda: "USD")
    return tmp_path


def _seed_position(qty=0, ccy="USD"):
    from backend.db.position_store import PositionStore

    return PositionStore().create(
        {"instrument_id": "inst-1", "account_id": "acc-1", "quantity": qty, "cost_currency": ccy}
    )


def _acquire(pos_id, qty, unit_cost, fees, acquired_at="2026-01-01", ccy="USD"):
    svc.apply_lot_change(
        {
            "type": "acquire",
            "position_id": pos_id,
            "quantity": qty,
            "unit_cost": unit_cost,
            "cost_currency": ccy,
            "fees": fees,
            "acquired_at": acquired_at,
        }
    )


def test_empty_book_all_zeros(isolated):
    out = svc.fees_summary()
    assert out["acquisition_fees_base"] == 0.0
    assert out["disposal_fees_base"] == 0.0
    assert out["total_fees_base"] == 0.0
    assert out["by_instrument"] == {}
    assert out["base_currency"] == "USD"


def test_acquisition_and_disposal_fees_summed(isolated):
    pos = _seed_position()
    _acquire(pos["id"], qty=10, unit_cost=100, fees=5)  # USD 5 acq fee
    # Sell 4 units @ 120 with a USD 3 disposal fee.
    svc.apply_lot_change(
        {
            "type": "dispose",
            "position_id": pos["id"],
            "quantity": 4,
            "proceeds_per_unit": 120,
            "proceeds_currency": "USD",
            "disposal_fees": 3,
            "disposed_at": "2026-02-01",
        }
    )
    out = svc.fees_summary()
    assert out["acquisition_fees_base"] == 5.0
    assert out["disposal_fees_base"] == 3.0
    assert out["total_fees_base"] == 8.0
    assert out["by_instrument"]["inst-1"]["total_fees_base"] == 8.0


def test_multi_currency_fee_recomputed_on_base_change(isolated, monkeypatch):
    pos = _seed_position(ccy="EUR")
    _acquire(pos["id"], qty=10, unit_cost=100, fees=10, ccy="EUR")  # EUR 10 fee
    # Under USD base: EUR 10 × 1.10 = 11.0 USD.
    out_usd = svc.fees_summary()
    assert out_usd["acquisition_fees_base"] == 11.0

    # Switch base to EUR — the SAME native EUR fee now reports as EUR 10 (recomputed, not a
    # stored USD snapshot). Proves the figure is computed from the native leg + current FX.
    monkeypatch.setattr(svc, "base_currency", lambda: "EUR")
    out_eur = svc.fees_summary()
    assert out_eur["base_currency"] == "EUR"
    assert out_eur["acquisition_fees_base"] == 10.0


def test_date_and_instrument_filter_narrow(isolated):
    pos = _seed_position()
    _acquire(pos["id"], qty=5, unit_cost=100, fees=2, acquired_at="2026-01-01")
    _acquire(pos["id"], qty=5, unit_cost=110, fees=4, acquired_at="2026-03-01")
    # Date window excludes the January lot.
    out = svc.fees_summary(date_from="2026-02-01")
    assert out["acquisition_fees_base"] == 4.0
    # Instrument filter on a different id → nothing.
    out2 = svc.fees_summary(instrument_id="other")
    assert out2["acquisition_fees_base"] == 0.0


def test_disposal_fee_counted_once_not_double_subtracted(isolated):
    """REGRESSION: a disposal fee appears in fees_summary AND already reduces the realized
    gain in realized_pnl. Assert both, so a refactor can't start double-booking the fee."""
    pos = _seed_position()
    _acquire(pos["id"], qty=10, unit_cost=100, fees=0)
    svc.apply_lot_change(
        {
            "type": "dispose",
            "position_id": pos["id"],
            "quantity": 10,
            "proceeds_per_unit": 120,
            "proceeds_currency": "USD",
            "disposal_fees": 50,
            "disposed_at": "2026-02-01",
        }
    )
    # Realized gain = (120 - 100) × 10 - 50 fee = 150 (fee reduces proceeds ONCE).
    pnl = svc.realized_pnl()
    assert pnl["realized_total_base"] == 150.0

    # fees_summary reports the SAME 50 as "fees paid" — informational, independent tally.
    fees = svc.fees_summary()
    assert fees["disposal_fees_base"] == 50.0
    # Sanity: the gain is NOT further reduced by re-subtracting the fee.
    assert pnl["realized_total_base"] != 100.0


# ── by_account venue rollup + fee rate (fee-capture sweep 2026-07-13) ──────────


def test_by_account_rollup_names_venue_and_derives_rate(isolated):
    from backend.db.account_store import AccountStore
    from backend.db.institution_store import InstitutionStore
    from backend.db.lot_store import LotStore
    from backend.db.realized_event_store import RealizedEventStore

    institution = InstitutionStore().resolve_or_create("Vantage Brokerage")
    assert institution is not None
    vantage = AccountStore().create(
        {
            "name": "Vantage Brokerage",
            "kind": "asset",
            "type": "investment",
            "institution_id": institution["id"],
        }
    )
    # Buy: 10 × 100 USD notional 1000, fee 4 → rate contribution.
    LotStore().create(
        {
            "position_id": "pos-1",
            "instrument_id": "inst-1",
            "account_id": vantage["id"],
            "kind": "buy",
            "original_qty": 10.0,
            "remaining_qty": 10.0,
            "unit_cost": 100.0,
            "cost_currency": "USD",
            "fees": 4.0,
            "acquired_at": "2026-01-10",
        }
    )
    # Sell on the same venue: 2 × 150 USD notional 300, fee 2.
    RealizedEventStore().append(
        {
            "position_id": "pos-1",
            "instrument_id": "inst-1",
            "account_id": vantage["id"],
            "disposed_at": "2026-02-01",
            "quantity": 2.0,
            "proceeds_per_unit": 150.0,
            "proceeds_currency": "USD",
            "disposal_fees": 2.0,
        }
    )
    out = svc.fees_summary()
    ab = out["by_account"][vantage["id"]]
    assert ab["account_name"] == "Vantage Brokerage"
    assert ab["institution"] == "Vantage Brokerage"
    assert ab["trade_count"] == 2
    assert ab["fees_base"] == pytest.approx(6.0)
    assert ab["notional_base"] == pytest.approx(1300.0)
    assert ab["fee_rate_pct"] == pytest.approx(0.4615, abs=1e-4)


def test_by_account_rate_none_when_notional_unknown(isolated):
    from backend.db.lot_store import LotStore

    LotStore().create(
        {
            "position_id": "pos-1",
            "instrument_id": "inst-1",
            "account_id": "acc-ghost",
            "kind": "buy",
            "original_qty": 5.0,
            "remaining_qty": 5.0,
            "unit_cost": None,  # unknown cost → no notional
            "cost_currency": "USD",
            "fees": 3.0,
            "acquired_at": "2026-01-10",
        }
    )
    out = svc.fees_summary()
    ab = out["by_account"]["acc-ghost"]
    assert ab["fees_base"] == pytest.approx(3.0)
    assert ab["notional_base"] == 0.0
    assert ab["fee_rate_pct"] is None  # unknown is never fabricated
    assert ab["account_name"] == "acc-ghost"  # venue record missing → id fallback
