"""
Regression tests for services.xray after the sys-classification change.

Verifies:
- A plain equity holding now contributes to a real by_region bucket (not unmapped).
- A plain bond holding now contributes to "Fixed income" sector + a region.
- Manual constituents still take precedence (existing behaviour unchanged).
- An instrument with no classifiable region still lands in unmapped.
"""

from __future__ import annotations

import pytest

from backend.db.instrument_store import InstrumentStore
from backend.services import ledger as ledger_svc
from backend.services import xray as svc


@pytest.fixture
def isolated(tmp_path, monkeypatch, db_isolated):
    monkeypatch.setattr(svc, "base_currency", lambda: "USD")
    monkeypatch.setattr(
        ledger_svc, "to_base", lambda amt, ccy, base=None, rates=None: round(float(amt), 2)
    )
    return tmp_path


def _row(iid, qty=1, price=100, quote_currency="USD", sector="", quote_per_unit=1.0):
    return {
        "instrument_id": iid,
        "quantity": qty,
        "price": price,
        "quote_per_unit": quote_per_unit,
        "currency": quote_currency,
        "quote_currency": quote_currency,
        "sector": sector,
    }


def test_plain_equity_usd_gets_real_region(isolated):
    """A plain USD equity now populates by_region with 'United States', not unmapped."""
    store = InstrumentStore()
    store.create(
        {
            "id": "eq1",
            "symbol": "AAPL",
            "asset_class": "equity",
            "asset_subclass": "stock",
            "sector": "Technology",
            "quote_currency": "USD",
        }
    )
    rows = [_row("eq1", qty=1, price=1000, quote_currency="USD", sector="Technology")]
    out = svc.portfolio_xray(rows=rows)

    assert out["total_base"] == 1000.0
    region_names = [b["name"] for b in out["by_region"]]
    assert "United States" in region_names
    assert out["region_unmapped_pct"] == 0.0


def test_plain_bond_sgd_gets_fixed_income_sector_and_region(isolated):
    """A plain SGD bond gets sector='Fixed income' and region='Singapore'."""
    store = InstrumentStore()
    store.create(
        {
            "id": "bond1",
            "symbol": "SGS2034",
            "asset_class": "bond",
            "quote_currency": "SGD",
        }
    )
    rows = [_row("bond1", qty=1, price=100, quote_currency="SGD")]
    out = svc.portfolio_xray(rows=rows)

    sector_names = [b["name"] for b in out["by_sector"]]
    assert "Fixed income" in sector_names
    region_names = [b["name"] for b in out["by_region"]]
    assert "Singapore" in region_names
    assert out["region_unmapped_pct"] == 0.0


def test_plain_crypto_gets_digital_assets_and_global(isolated):
    """A plain crypto holding gets sector='Digital assets' and region='Global'."""
    store = InstrumentStore()
    store.create(
        {
            "id": "btc",
            "symbol": "BTC",
            "asset_class": "crypto",
            "asset_subclass": "coin",
            "quote_currency": "USD",
        }
    )
    rows = [_row("btc", qty=1, price=50000, quote_currency="USD")]
    out = svc.portfolio_xray(rows=rows)

    sector_names = [b["name"] for b in out["by_sector"]]
    assert "Digital assets" in sector_names
    region_names = [b["name"] for b in out["by_region"]]
    assert "Global" in region_names


def test_manual_constituents_still_win_over_classify(isolated):
    """Manual constituents override classify_instrument — existing behaviour unchanged."""
    store = InstrumentStore()
    store.create(
        {
            "id": "etf1",
            "symbol": "VT",
            "asset_class": "equity",
            "asset_subclass": "etf",
            "quote_currency": "USD",
            "constituents": [
                {"weight": 0.6, "sector": "Technology", "region": "US"},
                {"weight": 0.4, "sector": "Financials", "region": "Europe"},
            ],
        }
    )
    rows = [_row("etf1", qty=1, price=1000)]
    out = svc.portfolio_xray(rows=rows)

    # The fund explodes by constituents — not by classify_instrument
    sector_names = {b["name"] for b in out["by_sector"]}
    assert "Technology" in sector_names
    assert "Financials" in sector_names
    region_names = {b["name"] for b in out["by_region"]}
    assert "US" in region_names
    assert "Europe" in region_names
    assert out["unmapped_pct"] == 0.0
    assert out["mapped_instruments"] == 1


def test_unknown_currency_instrument_region_still_unmapped(isolated):
    """An instrument with an unknown currency returns region='' (unmapped, not fabricated)."""
    store = InstrumentStore()
    store.create(
        {
            "id": "exotic",
            "symbol": "EXOTIC",
            "asset_class": "equity",
            "quote_currency": "ZZZ",  # not in the table
        }
    )
    rows = [_row("exotic", qty=1, price=100, quote_currency="ZZZ")]
    out = svc.portfolio_xray(rows=rows)

    assert out["by_region"] == []
    assert out["region_unmapped_pct"] == 100.0
