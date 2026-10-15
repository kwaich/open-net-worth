"""
services.xray + InstrumentStore.set_constituents tests (P2-INV5).

Offline + isolated. Pins the look-through explode math, unmapped %, validation, and the
store CRUD (incl. the factory default proving no back-compat shim is needed).
"""

from __future__ import annotations

import json

import pytest

from backend.core.errors import ValidationError
from backend.db.instrument_store import InstrumentStore
from backend.services import ledger as ledger_svc
from backend.services import xray as svc


@pytest.fixture
def isolated(tmp_path, monkeypatch, db_isolated):
    # InstrumentStore is DB-backed (plan 2, task 5) — db_isolated gives it a fresh
    # per-test database; tmp_path is still returned below for callers that use it.
    # base USD; identity FX. xray now values via the shared ledger.row_base_value helper,
    # which folds qty × price × quote_per_unit then calls ledger.to_base — patch that to
    # identity so the explode math is tested without an FX cache.
    monkeypatch.setattr(svc, "base_currency", lambda: "USD")
    monkeypatch.setattr(
        ledger_svc, "to_base", lambda amt, ccy, base=None, rates=None: round(float(amt), 2)
    )
    return tmp_path


# ── validate_constituents ────────────────────────────────────────────────────────


def test_validate_empty_ok(isolated):
    assert svc.validate_constituents([]) == []
    assert svc.validate_constituents(None) == []


def test_validate_normalizes_shape(isolated):
    out = svc.validate_constituents(
        [{"weight": 0.6, "sector": "Tech", "region": "US", "label": "x", "junk": 1}]
    )
    assert out == [{"weight": 0.6, "sector": "Tech", "region": "US", "label": "x"}]


def test_validate_rejects_negative_weight(isolated):
    with pytest.raises(ValidationError):
        svc.validate_constituents([{"weight": -0.1, "sector": "Tech"}])


def test_validate_rejects_sum_over_one(isolated):
    with pytest.raises(ValidationError):
        svc.validate_constituents(
            [{"weight": 0.7, "sector": "Tech"}, {"weight": 0.6, "sector": "Health"}]
        )


def test_validate_rejects_no_bucket(isolated):
    with pytest.raises(ValidationError):
        svc.validate_constituents([{"weight": 0.5}])


# ── portfolio_xray explode math ──────────────────────────────────────────────────


def _row(iid, qty=1, price=100, sector="", quote_per_unit=1.0, vesting_state="vested"):
    return {
        "instrument_id": iid,
        "quantity": qty,
        "price": price,
        "quote_per_unit": quote_per_unit,
        "currency": "USD",
        "sector": sector,
        "vesting_state": vesting_state,
    }


def test_plain_stock_is_its_own_sector(isolated):
    # Instrument defaults to quote_currency="USD" → classify_instrument gives "United States".
    InstrumentStore().create(
        {"id": "i1", "symbol": "AAA", "sector": "Technology", "quote_currency": "USD"}
    )
    rows = [_row("i1", qty=1, price=1000, sector="Technology")]
    out = svc.portfolio_xray(rows=rows)
    assert out["total_base"] == 1000.0
    assert out["by_sector"] == [{"name": "Technology", "value_base": 1000.0, "pct": 100.0}]
    # sys-classification: plain equity (USD) now gets region="United States" via heuristic.
    assert out["region_unmapped_pct"] == 0.0
    assert out["by_region"] == [{"name": "United States", "value_base": 1000.0, "pct": 100.0}]
    assert out["mapped_instruments"] == 0
    assert out["total_instruments"] == 1


def test_unvested_equity_grant_units_excluded(isolated):
    """An unvested RSU tranche is not owned yet (ADR
    2026-07-31-unvested-equity-excluded-from-net-worth) — it must not contribute to the
    look-through sector/region exposure, or a locked tranche skews "what am I exposed to"
    on shares that aren't the holder's yet."""
    InstrumentStore().create(
        {"id": "i1", "symbol": "AAA", "sector": "Technology", "quote_currency": "USD"}
    )
    InstrumentStore().create(
        {"id": "i2", "symbol": "RSU", "sector": "Consumer Cyclical", "quote_currency": "USD"}
    )
    rows = [
        _row("i1", qty=1, price=1000, sector="Technology"),
        _row("i2", qty=1000, price=1000, sector="Consumer Cyclical", vesting_state="unvested"),
    ]
    out = svc.portfolio_xray(rows=rows)
    assert out["total_base"] == 1000.0
    assert out["by_sector"] == [{"name": "Technology", "value_base": 1000.0, "pct": 100.0}]


def test_etf_explodes_by_weight(isolated):
    InstrumentStore().create(
        {
            "id": "etf",
            "symbol": "VOO",
            "quote_currency": "USD",
            "constituents": [
                {"weight": 0.6, "sector": "Technology", "region": "US"},
                {"weight": 0.4, "sector": "Financials", "region": "US"},
            ],
        }
    )
    rows = [_row("etf", qty=1, price=1000)]
    out = svc.portfolio_xray(rows=rows)
    sectors = {s["name"]: s["value_base"] for s in out["by_sector"]}
    assert sectors == {"Technology": 600.0, "Financials": 400.0}
    assert out["by_region"] == [{"name": "US", "value_base": 1000.0, "pct": 100.0}]
    assert out["unmapped_pct"] == 0.0
    assert out["mapped_instruments"] == 1


def test_weight_shortfall_counts_as_unmapped(isolated):
    InstrumentStore().create(
        {
            "id": "etf",
            "symbol": "ARK",
            "quote_currency": "USD",
            "constituents": [{"weight": 0.7, "sector": "Technology", "region": "US"}],
        }
    )
    rows = [_row("etf", qty=1, price=1000)]
    out = svc.portfolio_xray(rows=rows)
    # 70% mapped to Tech; 30% shortfall is unmapped on both axes.
    assert out["sector_unmapped_pct"] == 30.0
    assert out["region_unmapped_pct"] == 30.0
    assert out["unmapped_pct"] == 30.0


def test_multi_instrument_aggregation_and_unmapped(isolated):
    store = InstrumentStore()
    store.create(
        {
            "id": "etf",
            "symbol": "VOO",
            "quote_currency": "USD",
            "constituents": [{"weight": 1.0, "sector": "Technology", "region": "US"}],
        }
    )
    store.create(
        {"id": "plain", "symbol": "ZZZ", "sector": "", "quote_currency": "USD"}
    )  # no sector → unmapped
    rows = [_row("etf", qty=1, price=500), _row("plain", qty=1, price=500, sector="")]
    out = svc.portfolio_xray(rows=rows)
    assert out["total_base"] == 1000.0
    sectors = {s["name"]: s["value_base"] for s in out["by_sector"]}
    assert sectors == {"Technology": 500.0}
    # The plain holding has no sector → 50% sector-unmapped.
    assert out["sector_unmapped_pct"] == 50.0
    assert out["mapped_instruments"] == 1
    assert out["total_instruments"] == 2


def test_plain_book_is_self_classified_not_looked_through(isolated):
    """A book of plain stocks: 0% looked-through (no constituents map), the sector axis is fully
    self-classified (0 unmapped).

    After sys-classification: plain equity holdings (default USD) now get a real region
    via classify_instrument — region axis is no longer 100% unmapped. Both axes sum to 100%
    with NO unmapped slice for either.
    """
    store = InstrumentStore()
    store.create({"id": "i1", "symbol": "AAA", "sector": "Technology", "quote_currency": "USD"})
    store.create({"id": "i2", "symbol": "BBB", "sector": "Financials", "quote_currency": "USD"})
    rows = [
        _row("i1", qty=1, price=600, sector="Technology"),
        _row("i2", qty=1, price=400, sector="Financials"),
    ]
    out = svc.portfolio_xray(rows=rows)
    assert out["looked_through_pct"] == 0.0
    # Sector axis: every holding placed by its own sector → nothing unmapped, bars cover 100%.
    assert out["sector_unmapped_pct"] == 0.0
    assert out["sector_unmapped_base"] == 0.0
    assert round(sum(b["pct"] for b in out["by_sector"]), 2) == 100.0
    # Region axis: sys-classification fills region for plain USD equities → 0% unmapped.
    assert out["region_unmapped_pct"] == 0.0
    assert out["region_unmapped_base"] == 0.0
    assert len(out["by_region"]) > 0
    assert round(sum(b["pct"] for b in out["by_region"]), 2) == 100.0


def test_per_axis_unmapped_sums_to_full_book(isolated):
    """For each axis, mapped buckets + the unmapped slice must sum to ~100% (the panel renders the
    unmapped slice explicitly, so the column can never visually contradict the headline)."""
    store = InstrumentStore()
    store.create(
        {
            "id": "etf",
            "symbol": "ARK",
            "quote_currency": "USD",
            "constituents": [{"weight": 0.7, "sector": "Technology", "region": "US"}],
        }
    )
    store.create(
        {"id": "plain", "symbol": "ZZZ", "sector": "", "quote_currency": "USD"}
    )  # no sector → sector-unmapped too
    rows = [_row("etf", qty=1, price=600), _row("plain", qty=1, price=400, sector="")]
    out = svc.portfolio_xray(rows=rows)
    sector_total = sum(b["pct"] for b in out["by_sector"]) + out["sector_unmapped_pct"]
    region_total = sum(b["pct"] for b in out["by_region"]) + out["region_unmapped_pct"]
    assert round(sector_total, 2) == 100.0
    assert round(region_total, 2) == 100.0


def test_empty_book_all_zeros(isolated):
    out = svc.portfolio_xray(rows=[])
    assert out["total_base"] == 0.0
    assert out["by_sector"] == []
    assert out["by_region"] == []
    assert out["unmapped_pct"] == 0.0
    assert out["looked_through_pct"] == 0.0
    assert out["sector_unmapped_base"] == 0.0
    assert out["region_unmapped_base"] == 0.0


# ── store CRUD ────────────────────────────────────────────────────────────────────


def test_constituents_default_empty_no_shim(isolated):
    """A record written without constituents reads back [] via the factory default — proves
    no read-time back-compat fallback is needed."""
    inst = InstrumentStore().create({"symbol": "OLD", "quote_currency": "USD"})
    assert inst["constituents"] == []
    fetched = InstrumentStore().find_by_id(inst["id"])
    assert fetched["constituents"] == []


def test_set_constituents_persists_and_coerces(isolated):
    store = InstrumentStore()
    inst = store.create({"symbol": "VOO", "quote_currency": "USD"})
    updated = store.set_constituents(
        inst["id"], [{"weight": "0.5", "sector": "Tech", "region": "US", "label": "L"}]
    )
    assert updated["constituents"] == [
        {"weight": 0.5, "sector": "Tech", "region": "US", "label": "L"}
    ]
    # Round-trips through disk.
    reread = store.find_by_id(inst["id"])
    assert reread["constituents"][0]["weight"] == 0.5


def test_set_constituents_clear(isolated):
    store = InstrumentStore()
    inst = store.create(
        {
            "symbol": "VOO",
            "quote_currency": "USD",
            "constituents": [{"weight": 1.0, "sector": "Tech"}],
        }
    )
    updated = store.set_constituents(inst["id"], [])
    assert updated["constituents"] == []


def test_xray_default_rows_uses_hydrated(isolated, monkeypatch):
    """portfolio_xray(rows=None) fetches positions_service.hydrated() — smoke that the wiring
    works without passing rows explicitly."""
    from backend.services import positions as positions_service

    InstrumentStore().create(
        {"id": "i1", "symbol": "AAA", "sector": "Tech", "quote_currency": "USD"}
    )
    monkeypatch.setattr(
        positions_service, "hydrated", lambda *a, **k: [_row("i1", qty=1, price=100, sector="Tech")]
    )
    out = svc.portfolio_xray()
    assert out["total_base"] == 100.0
    assert json.dumps(out)  # serializable
