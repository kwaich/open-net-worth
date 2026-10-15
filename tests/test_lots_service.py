"""
Unit tests for backend.services.lots — the realized-P&L core (P2-INV1).

Money correctness is the bar: these assert EXACT realized gains to the cent for the
RFC's acceptance criteria (AC-1…AC-9, AC-17, AC-18). Pure + offline — no stores, no
network: compute_disposal is fed fixture lots directly, mirroring
tests/test_char_ledger_math.py.

Worked examples (RFC Appendix):
  Lots L1 = 10 @ $100 (Jan), L2 = 10 @ $120 (Mar).
  Sell 15 @ $130 FIFO  -> 10×(130−100) + 5×(130−120) = +$350.00   (AC-3)
  Sell 10 @ $130 spec=L2 -> 10×(130−120) = +$100.00, same sell FIFO = +$300.00 (AC-5)
"""

from __future__ import annotations

import pytest

from backend.core.errors import InsufficientLots, InvalidLotSelection
from backend.services import lots as svc

# Single-base table: everything in USD, base USD ⇒ to_base is an identity round.
_USD = {"USD": 1.0}


def _lot(lot_id, qty, unit_cost, acquired_at, created_at=None, ccy="USD", fees=0.0):
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
        "fees": fees,
        "kind": "buy",
        "created_at": created_at or f"{acquired_at}T00:00:00Z",
    }


_POS = {"id": "pos-1", "instrument_id": "inst-1", "account_id": "acc-1", "cost_currency": "USD"}


def _dispose(qty, price, method="fifo", lot_selection=None, disposed_at="2026-06-01", **kw):
    d = {
        "quantity": qty,
        "proceeds_per_unit": price,
        "proceeds_currency": "USD",
        "method": method,
        "disposed_at": disposed_at,
    }
    if lot_selection is not None:
        d["lot_selection"] = lot_selection
    d.update(kw)
    return d


# ── A. FIFO realized gain — exact arithmetic ─────────────────────────────────────


def test_ac1_single_lot_full_sell():
    lots = [_lot("L1", 10, 100.0, "2026-01-01")]
    draft = svc.compute_disposal(_POS, lots, _dispose(10, 130.0), rates=_USD, base="USD")
    assert draft["realized_gain"] == 300.00
    assert [(c["lot_id"], c["qty"]) for c in draft["consumptions"]] == [("L1", 10.0)]


def test_ac2_single_lot_partial_sell():
    lots = [_lot("L1", 10, 100.0, "2026-01-01")]
    draft = svc.compute_disposal(_POS, lots, _dispose(4, 130.0), rates=_USD, base="USD")
    assert draft["realized_gain"] == 120.00  # 4 × (130 − 100)
    assert draft["consumptions"][0]["qty"] == 4.0


def test_ac3_multi_lot_fifo_spans_two_lots():
    lots = [
        _lot("L1", 10, 100.0, "2026-01-01"),
        _lot("L2", 10, 120.0, "2026-03-01"),
    ]
    draft = svc.compute_disposal(_POS, lots, _dispose(15, 130.0), rates=_USD, base="USD")
    # 10×(130−100) + 5×(130−120) = 300 + 50 = 350
    assert draft["realized_gain"] == 350.00
    assert [(c["lot_id"], c["qty"]) for c in draft["consumptions"]] == [("L1", 10.0), ("L2", 5.0)]


def test_ac4_loss_sign_preserved():
    lots = [_lot("L1", 10, 100.0, "2026-01-01")]
    draft = svc.compute_disposal(_POS, lots, _dispose(10, 80.0), rates=_USD, base="USD")
    assert draft["realized_gain"] == -200.00  # negative, not abs


# ── B. FIFO vs specific-ID DIFFER (same sequence, different method ⇒ diff gain) ───


def test_ac5_fifo_vs_specific_id_differ_on_same_fixture():
    """The keystone: ONE fixture, two methods, asserts both numbers so the divergence
    can never silently regress (100 ≠ 300)."""
    lots = [
        _lot("L1", 10, 100.0, "2026-01-01"),
        _lot("L2", 10, 120.0, "2026-03-01"),
    ]
    fifo = svc.compute_disposal(
        _POS, lots, _dispose(10, 130.0, method="fifo"), rates=_USD, base="USD"
    )
    specific = svc.compute_disposal(
        _POS,
        lots,
        _dispose(10, 130.0, method="specific_id", lot_selection=[{"lot_id": "L2", "qty": 10}]),
        rates=_USD,
        base="USD",
    )
    assert fifo["realized_gain"] == 300.00  # consumes L1 @ 100
    assert specific["realized_gain"] == 100.00  # picks L2 @ 120
    assert fifo["realized_gain"] != specific["realized_gain"]


def test_ac6_specific_id_split_across_named_lots():
    lots = [
        _lot("L1", 10, 100.0, "2026-01-01"),
        _lot("L2", 10, 120.0, "2026-03-01"),
    ]
    draft = svc.compute_disposal(
        _POS,
        lots,
        _dispose(
            10,
            130.0,
            method="specific_id",
            lot_selection=[{"lot_id": "L1", "qty": 3}, {"lot_id": "L2", "qty": 7}],
        ),
        rates=_USD,
        base="USD",
    )
    # 3×(130−100) + 7×(130−120) = 90 + 70 = 160
    assert draft["realized_gain"] == 160.00
    by_lot = {c["lot_id"]: c["qty"] for c in draft["consumptions"]}
    assert by_lot == {"L1": 3.0, "L2": 7.0}


def test_ac7_specific_id_over_allocation_rejected_no_mutation():
    lots = [_lot("L1", 10, 100.0, "2026-01-01")]
    with pytest.raises(InvalidLotSelection):
        svc.compute_disposal(
            _POS,
            lots,
            _dispose(11, 130.0, method="specific_id", lot_selection=[{"lot_id": "L1", "qty": 11}]),
            rates=_USD,
            base="USD",
        )
    # Pure function: the fixture lot is untouched.
    assert lots[0]["remaining_qty"] == 10


def test_specific_id_unknown_lot_rejected():
    lots = [_lot("L1", 10, 100.0, "2026-01-01")]
    with pytest.raises(InvalidLotSelection):
        svc.compute_disposal(
            _POS,
            lots,
            _dispose(5, 130.0, method="specific_id", lot_selection=[{"lot_id": "NOPE", "qty": 5}]),
            rates=_USD,
            base="USD",
        )


# ── C. Holding period (long vs short) ───────────────────────────────────────────


def test_ac8_short_term():
    # 2026-01-01 → 2026-06-01 = 151 days < 365.
    assert svc.classify_term("2026-01-01", "2026-06-01") == "short"
    lots = [_lot("L1", 10, 100.0, "2026-01-01")]
    draft = svc.compute_disposal(
        _POS, lots, _dispose(10, 130.0, disposed_at="2026-06-01"), rates=_USD, base="USD"
    )
    assert draft["consumptions"][0]["term"] == "short"


def test_ac9_long_term_and_mixed_per_line_term():
    lots = [
        _lot("L1", 10, 100.0, "2025-01-01"),  # > 365d at 2026-06-01
        _lot("L2", 10, 120.0, "2026-03-01"),  # < 365d
    ]
    draft = svc.compute_disposal(
        _POS, lots, _dispose(15, 130.0, disposed_at="2026-06-01"), rates=_USD, base="USD"
    )
    terms = {c["lot_id"]: c["term"] for c in draft["consumptions"]}
    assert terms == {"L1": "long", "L2": "short"}
    # Aggregate gain is the sum regardless of term.
    assert draft["realized_gain"] == 350.00


def test_classify_term_boundary_exactly_threshold_is_long():
    # 365 days held → long (>= threshold).
    assert svc.classify_term("2025-06-01", "2026-06-01") == "long"  # 365 days
    assert svc.classify_term("", "2026-06-01") == "unknown"
    assert svc.classify_term("2026-01-01", "") == "unknown"


# ── effective_unit_cost (fees amortization) ──────────────────────────────────────


def test_effective_unit_cost_amortizes_fees():
    lot = _lot("L1", 10, 100.0, "2026-01-01", fees=50.0)
    # 100 + 50/10 = 105
    assert svc.effective_unit_cost(lot) == 105.0


def test_effective_unit_cost_none_when_unknown():
    lot = _lot("L1", 10, None, "2026-01-01", fees=50.0)
    assert svc.effective_unit_cost(lot) is None


def test_fees_reduce_realized_gain():
    lot = _lot("L1", 10, 100.0, "2026-01-01", fees=50.0)  # eff cost 105
    draft = svc.compute_disposal(_POS, [lot], _dispose(10, 130.0), rates=_USD, base="USD")
    # 10 × (130 − 105) = 250
    assert draft["realized_gain"] == 250.00


def test_disposal_fees_reduce_proceeds():
    lot = _lot("L1", 10, 100.0, "2026-01-01")
    draft = svc.compute_disposal(
        _POS, [lot], _dispose(10, 130.0, disposal_fees=30.0), rates=_USD, base="USD"
    )
    # proceeds 130 − fee/unit (30/10=3) = 127; 10 × (127 − 100) = 270
    assert draft["realized_gain"] == 270.00


# ── Historical-rate lock (ADR 2026-07-15): per-leg base gain ─────────────────────


def test_per_leg_base_gain_uses_acquisition_stamp():
    """A stamped foreign lot's basis converts at its ACQUISITION-date rate; proceeds at
    the disposal-date rate. Worked example (base USD): 10 units @ 135 SGD stamped at
    0.75 SGD→USD (basis_base 1,012.50); sold 10 @ 150 SGD when SGD→USD = 0.80
    (proceeds_base 1,200) → gain_base 187.50. The single-rate conversion (native 150
    SGD × 0.80 = 120) misses the 67.50 FX gain on the basis."""
    lot = {
        **_lot("L1", 10, 135.0, "2026-01-01", ccy="SGD"),
        "fx_rate": 0.75,
        "base_currency": "USD",
    }
    rates = {"SGD": 0.8, "USD": 1.0}
    draft = svc.compute_disposal(
        _POS, [lot], _dispose(10, 150.0, proceeds_currency="SGD"), rates=rates, base="USD"
    )
    assert draft["realized_gain"] == 150.00  # native (SGD): 1,500 − 1,350
    assert draft["realized_gain_base"] == 187.50  # per-leg, NOT 120
    assert draft["consumptions"][0]["fx_rate_acquire"] == 0.75


def test_unstamped_lot_falls_back_to_disposal_rate_conversion():
    """A legacy lot (no acquire stamp) keeps the pre-lock behavior: native gain
    converted at the disposal-date rate."""
    lot = _lot("L1", 10, 135.0, "2026-01-01", ccy="SGD")  # no fx_rate/base_currency
    rates = {"SGD": 0.8, "USD": 1.0}
    draft = svc.compute_disposal(
        _POS, [lot], _dispose(10, 150.0, proceeds_currency="SGD"), rates=rates, base="USD"
    )
    assert draft["realized_gain"] == 150.00
    assert draft["realized_gain_base"] == 120.00  # to_base(150 SGD → USD @ 0.8)
    assert draft["consumptions"][0]["fx_rate_acquire"] is None


def test_stamp_for_other_base_is_not_trusted():
    """A lot stamped against a DIFFERENT base (user changed base currency since) must
    not feed the per-leg path — fall back to the disposal-rate conversion."""
    lot = {
        **_lot("L1", 10, 135.0, "2026-01-01", ccy="SGD"),
        "fx_rate": 0.99,
        "base_currency": "EUR",
    }
    rates = {"SGD": 0.8, "USD": 1.0}
    draft = svc.compute_disposal(
        _POS, [lot], _dispose(10, 150.0, proceeds_currency="SGD"), rates=rates, base="USD"
    )
    assert draft["realized_gain_base"] == 120.00


def test_realized_pnl_trusts_stamped_base_while_base_matches(monkeypatch):
    """The read side of the lock: a stamped event's realized_gain_base is a historical
    fact while base_currency matches — never revalued at current FX. On a base change
    the stamp is stale → recompute from the native gain."""
    ev = {
        "id": "ev1",
        "instrument_id": "inst-1",
        "disposed_at": "2026-06-01",
        "quantity": 10.0,
        "realized_gain": 150.0,
        "proceeds_currency": "SGD",
        "realized_gain_base": 187.50,
        "base_currency": "USD",
        "is_taxable": True,
        "consumptions": [],
    }

    class _FakeEvents:
        def query_events(self, **kw):
            return [ev]

    class _FakeInstruments:
        def all(self):
            return [{"id": "inst-1", "symbol": "BTC", "name": "Bitcoin"}]

    monkeypatch.setattr(svc, "RealizedEventStore", lambda: _FakeEvents())
    monkeypatch.setattr(svc, "instruments", _FakeInstruments())
    monkeypatch.setattr(svc, "_load_rates", lambda: {"SGD": 0.9, "USD": 1.0})

    trusted = svc.realized_pnl(base="USD")
    assert trusted["events"][0]["realized_gain_base"] == 187.50  # stamp, not 150×0.9
    assert trusted["realized_total_base"] == 187.50

    restamped = svc.realized_pnl(base="SGD")  # base changed → stamp stale → recompute
    assert restamped["events"][0]["realized_gain_base"] == 150.00


# ── F. Read-projection invariants (AC-17 / AC-18) ────────────────────────────────


def test_ac18_remaining_basis_equals_sum_of_open_lots(monkeypatch):
    lots = [
        {**_lot("L1", 10, 100.0, "2026-01-01"), "remaining_qty": 6},  # partially sold
        _lot("L2", 10, 120.0, "2026-03-01"),
    ]
    monkeypatch.setattr(svc, "LotStore", lambda: _FakeLotStore(lots))
    # 6 × 100 + 10 × 120 = 600 + 1200 = 1800
    assert svc.position_remaining_basis("pos-1") == 1800.00


# The owned branch of position_figures.derive — the ONE place a position's figures come from
# its open lots (ADR 2026-10-07-a-position-quantity-is-derived-from-its-records).
_POS = {"id": "pos-1", "instrument_id": "inst-1"}


def _owned(monkeypatch, lots):
    from backend.services import position_figures as pf

    monkeypatch.setattr(pf, "LotStore", lambda: _FakeLotStore(lots))
    return pf._owned_figures(_POS, conn=None)


def test_ac17_owned_figures_recover_quantity_and_avg_cost(monkeypatch):
    figures = _owned(
        monkeypatch, [_lot("L1", 10, 100.0, "2026-01-01"), _lot("L2", 10, 120.0, "2026-03-01")]
    )
    # basis-weighted avg = (10×100 + 10×120) / 20 = 110; currency from the (single) lots.
    assert figures == {"quantity": 20.0, "cost_basis": 110.0, "cost_currency": "USD"}


def test_owned_avg_cost_none_when_all_unknown(monkeypatch):
    figures = _owned(monkeypatch, [_lot("L1", 10, None, "2026-01-01")])
    assert figures["quantity"] == 10.0
    assert figures["cost_basis"] is None


def test_owned_cost_currency_follows_the_lots(monkeypatch):
    """REGRESSION: a position created in the instrument's quote currency (USD) but whose
    buy lots are all booked in the settlement currency (SGD) takes SGD — otherwise the rollup
    mislabels the basis currency and double-applies FX converting an SGD cost as USD."""
    lots = [
        _lot("L1", 10, 100.0, "2026-01-01", ccy="SGD"),
        _lot("L2", 10, 120.0, "2026-03-01", ccy="SGD"),
    ]
    assert _owned(monkeypatch, lots)["cost_currency"] == "SGD"


def test_owned_mixed_currency_lots_convert_into_quote_currency(monkeypatch):
    """REGRESSION: open cost-known lots that disagree on currency convert each lot's basis
    into the instrument's quote currency at current rates — never a raw cross-currency
    average — and the figure is stamped in that currency."""
    from backend.services import position_figures as pf

    class _Inst:
        def find_by_id(self, iid, *, conn=None):
            return {"id": iid, "quote_currency": "USD"}

    monkeypatch.setattr(pf, "InstrumentStore", lambda: _Inst())
    # 1 SGD = 1/1.35 USD → L2's 1,350 SGD basis = 1,000 USD.
    monkeypatch.setattr(pf, "to_base", lambda a, c, b=None, rates=None: round(float(a) / 1.35, 2))
    lots = [
        _lot("L1", 10, 100.0, "2026-01-01", ccy="USD"),
        _lot("L2", 10, 135.0, "2026-03-01", ccy="SGD"),
    ]
    figures = _owned(monkeypatch, lots)
    # (10×100 USD + 1,000 USD-converted) / 20 = 100/unit, stamped USD.
    assert figures["cost_basis"] == 100.0
    assert figures["cost_currency"] == "USD"


# ── E. Oversell guard (AC-13) ────────────────────────────────────────────────────


def test_ac13_oversell_whole_position_raises():
    lots = [_lot("L1", 10, 100.0, "2026-01-01")]
    with pytest.raises(InsufficientLots):
        svc.compute_disposal(_POS, lots, _dispose(11, 130.0), rates=_USD, base="USD")


# ── Test doubles ─────────────────────────────────────────────────────────────────


class _FakeLotStore:
    def __init__(self, lots):
        self._lots = lots

    def open_lots_for_position(self, position_id, *, conn=None):
        return [
            lot
            for lot in self._lots
            if lot.get("position_id") == position_id and float(lot.get("remaining_qty") or 0) > 0
        ]

    def lots_for_position(self, position_id, include_closed=True, *, conn=None):
        return [lot for lot in self._lots if lot.get("position_id") == position_id]


class _NoopPos:
    def touch(self, pid, changes, *, conn=None):
        return changes


class _FakeEventStore:
    """Mirrors RealizedEventStore.query_events filtering, so the test fails if the service
    stops threading a filter through (the regression: position_id was never passed)."""

    def __init__(self, events):
        self._events = events

    def query_events(
        self, date_from=None, date_to=None, instrument_id=None, account_id=None, position_id=None
    ):
        items = list(self._events)
        if instrument_id is not None:
            items = [e for e in items if e.get("instrument_id") == instrument_id]
        if account_id is not None:
            items = [e for e in items if e.get("account_id") == account_id]
        if position_id is not None:
            items = [e for e in items if e.get("position_id") == position_id]
        return items


class _FakeInstruments:
    def all(self):
        return []


def _event(eid, position_id, gain):
    return {
        "id": eid,
        "instrument_id": "inst-1",
        "account_id": "acc-1",
        "position_id": position_id,
        "disposed_at": "2026-06-01",
        "proceeds_currency": "USD",
        "realized_gain": gain,
        "consumptions": [{"qty": 1.0, "realized_gain": gain, "term": "short"}],
    }


def test_realized_pnl_scopes_to_position(monkeypatch):
    """Regression: a disposal must not bleed onto a SIBLING position of the same instrument
    (the unvested-grant row was showing the sellable row's sale). realized_pnl(position_id=…)
    returns only that position's events; the instrument-wide query still returns all of them."""
    events = [
        _event("ev-liquid", "pos-liquid", 100.0),
        _event("ev-locked-sibling", "pos-locked", 200.0),
    ]
    monkeypatch.setattr(svc, "RealizedEventStore", lambda: _FakeEventStore(events))
    monkeypatch.setattr(svc, "instruments", _FakeInstruments())
    monkeypatch.setattr(svc, "_load_rates", lambda: {})

    scoped = svc.realized_pnl(position_id="pos-liquid", base="USD")
    assert [e["id"] for e in scoped["events"]] == ["ev-liquid"]
    assert scoped["realized_total_base"] == 100.0

    # No regression: an instrument-wide query still returns every disposal.
    by_instrument = svc.realized_pnl(instrument_id="inst-1", base="USD")
    assert {e["id"] for e in by_instrument["events"]} == {"ev-liquid", "ev-locked-sibling"}
    assert by_instrument["realized_total_base"] == 300.0


def test_dispose_undo_round_trip(tmp_path, monkeypatch):
    """dispose_undo is the inverse of dispose: re-opens the consumed lot, removes the realized
    event, and resyncs the position back to its pre-sale quantity (the recovery path Lucius now
    has for a duplicate/mistaken sale). Round-trips on real stores isolated to tmp_path."""
    from backend.db.lot_store import LotStore
    from backend.db.position_store import PositionStore
    from backend.db.realized_event_store import RealizedEventStore

    monkeypatch.setattr(svc, "_load_rates", lambda: {"USD": 1.0})
    monkeypatch.setattr(svc, "base_currency", lambda: "USD")

    pos = PositionStore().create(
        {
            "instrument_id": "inst-1",
            "account_id": "acc-1",
            "quantity": 10.0,
            "cost_basis": 100.0,
            "cost_currency": "USD",
            "bucket": "liquid",
        }
    )
    lot = LotStore().create(
        {
            "position_id": pos["id"],
            "instrument_id": "inst-1",
            "account_id": "acc-1",
            "acquired_at": "2026-01-01",
            "original_qty": 10.0,
            "remaining_qty": 10.0,
            "unit_cost": 100.0,
            "cost_currency": "USD",
            "kind": "buy",
        }
    )

    # Sell 4 @ 130 FIFO — consumes the lot from 10 → 6, books a realized event.
    res = svc.apply_lot_change(
        {
            "type": "dispose",
            "position_id": pos["id"],
            "quantity": 4,
            "proceeds_per_unit": 130.0,
            "proceeds_currency": "USD",
            "method": "fifo",
            "disposed_at": "2026-06-01",
        }
    )
    assert res["ok"]
    event_id = res["event_id"]
    assert LotStore().find_by_id(lot["id"])["remaining_qty"] == 6.0
    assert PositionStore().find_by_id(pos["id"])["quantity"] == 6.0
    assert RealizedEventStore().find_by_id(event_id) is not None

    # Undo — lot restored to 10, position back to 10, realized event gone.
    undo = svc.apply_lot_change({"type": "dispose_undo", "event_id": event_id})
    assert undo["ok"] and undo["event_id"] == event_id
    assert LotStore().find_by_id(lot["id"])["remaining_qty"] == 10.0
    assert PositionStore().find_by_id(pos["id"])["quantity"] == 10.0
    assert RealizedEventStore().find_by_id(event_id) is None


def test_dispose_undo_refuses_when_consumed_lot_gone(tmp_path, monkeypatch):
    """If a consumed lot was deleted after the sale, the shares can't be restored — the undo
    refuses (no mutation) rather than silently dropping the event."""
    from backend.db.realized_event_store import RealizedEventStore

    ev = RealizedEventStore().append(
        {
            "position_id": "pos-1",
            "instrument_id": "inst-1",
            "quantity": 4.0,
            "consumptions": [{"lot_id": "ghost-lot", "qty": 4.0}],
        }
    )
    out = svc.apply_lot_change({"type": "dispose_undo", "event_id": ev["id"]})
    assert "error" in out and "no longer exist" in out["error"]
    # The event is untouched (refused before any mutation).
    assert RealizedEventStore().find_by_id(ev["id"]) is not None


def _seed_disposable_position(tmp_path, monkeypatch, qty=100.0):
    """Real stores at tmp_path + one position with a single 100-unit lot — the fixture the
    duplicate-disposal guard tests dispose against."""
    from backend.db.lot_store import LotStore
    from backend.db.position_store import PositionStore

    monkeypatch.setattr(svc, "_load_rates", lambda: {"USD": 1.0})
    monkeypatch.setattr(svc, "base_currency", lambda: "USD")

    pos = PositionStore().create(
        {
            "instrument_id": "inst-1",
            "account_id": "acc-1",
            "quantity": qty,
            "cost_basis": 100.0,
            "cost_currency": "USD",
            "bucket": "liquid",
        }
    )
    LotStore().create(
        {
            "position_id": pos["id"],
            "instrument_id": "inst-1",
            "account_id": "acc-1",
            "acquired_at": "2026-01-01",
            "original_qty": qty,
            "remaining_qty": qty,
            "unit_cost": 100.0,
            "cost_currency": "USD",
            "kind": "buy",
        }
    )
    return pos


def _dispose_change(pos_id, qty=4, price=130.0, method="fifo", **kw):
    return {
        "type": "dispose",
        "position_id": pos_id,
        "quantity": qty,
        "proceeds_per_unit": price,
        "proceeds_currency": "USD",
        "method": method,
        "disposed_at": "2026-06-01",
        **kw,
    }


def test_duplicate_disposal_refused_even_with_different_method(tmp_path, monkeypatch):
    """The ONWAI recurrence guard: a second disposal with the SAME position/date/qty/price is
    refused as a double-book — even when its method differs (Specific vs FIFO), since method
    and fees are intentionally excluded from the match. The guard fires BEFORE lot-selection
    validation, so the second (specific, no selection) call is caught cleanly with no mutation."""
    from backend.db.realized_event_store import RealizedEventStore

    pos = _seed_disposable_position(tmp_path, monkeypatch)
    first = svc.apply_lot_change(_dispose_change(pos["id"], method="fifo"))
    assert first["ok"]

    # Same sale, DIFFERENT method → still caught as a duplicate (method/fees excluded).
    dup = svc.apply_lot_change(_dispose_change(pos["id"], method="specific_id", lot_selection=[]))
    assert "error" in dup and dup.get("existing_event_id") == first["event_id"]
    # Only the one real event exists; nothing further consumed.
    assert len(RealizedEventStore().query_events(position_id=pos["id"])) == 1


def test_duplicate_disposal_allow_override(tmp_path, monkeypatch):
    """allow_duplicate=true is the escape hatch for two genuinely separate identical fills."""
    from backend.db.realized_event_store import RealizedEventStore

    pos = _seed_disposable_position(tmp_path, monkeypatch)
    assert svc.apply_lot_change(_dispose_change(pos["id"]))["ok"]
    second = svc.apply_lot_change(_dispose_change(pos["id"], allow_duplicate=True))
    assert second["ok"]
    assert len(RealizedEventStore().query_events(position_id=pos["id"])) == 2


def test_non_duplicate_disposal_allowed(tmp_path, monkeypatch):
    """A different price (or qty/date) is NOT a duplicate — it books normally."""
    from backend.db.realized_event_store import RealizedEventStore

    pos = _seed_disposable_position(tmp_path, monkeypatch)
    assert svc.apply_lot_change(_dispose_change(pos["id"], price=130.0))["ok"]
    assert svc.apply_lot_change(_dispose_change(pos["id"], price=131.0))["ok"]
    assert len(RealizedEventStore().query_events(position_id=pos["id"])) == 2
