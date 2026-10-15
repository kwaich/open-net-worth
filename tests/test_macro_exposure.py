"""
Unit tests for backend.services.macro_exposure — deterministic, no LLM.

Covers:
- currency_exposure: sums match to_base per row; missing FX rate degrades 1:1 (logged)
- affected_holdings("fx"): returns only holdings in the given currency with correct base values
- affected_holdings("sector"): tag-only directional match
- affected_holdings("fx"): empty when no holdings in that currency
- missing FX rate → direction="unknown"
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest


@pytest.fixture
def isolated(tmp_path, monkeypatch, db_isolated):
    """Wire all stores to tmp_path; provide helpers.

    InstrumentStore is DB-backed (plan 2, task 5) — db_isolated gives it a fresh
    per-test database instead of a tmp_path file redirect.
    """
    acct_path = tmp_path / "accounts.json"

    return {
        "acct_path": acct_path,
        "tmp_path": tmp_path,
    }


def _write(path: Path, data) -> None:
    path.write_text(json.dumps(data), encoding="utf-8")


def _make_rows(specs: list[dict]) -> list[dict]:
    """Build minimal hydrated rows for testing."""
    rows = []
    for s in specs:
        rows.append(
            {
                "instrument_id": s.get("instrument_id", "inst-1"),
                "label": s.get("label", "Test"),
                "ticker": s.get("ticker", "TST"),
                "quantity": s.get("quantity", 10.0),
                "price": s.get("price", 100.0),
                "quote_per_unit": s.get("qpu", 1.0),
                "quote_currency": s.get("ccy", "USD"),
                "currency": s.get("cost_ccy", s.get("ccy", "USD")),
                "asset_class": s.get("asset_class", "equity"),
                "sector": s.get("sector", ""),
                "vesting_state": s.get("vesting_state", "vested"),
                "cost_basis": s.get("cost_basis", 0),
                "tags": s.get("tags", []),
            }
        )
    return rows


# ── currency_exposure ──────────────────────────────────────────────────────────


def test_currency_exposure_usd_only():
    from backend.services.macro_exposure import currency_exposure

    rates = {"SGD": 1.0, "USD": 0.74}
    rows = _make_rows(
        [
            {
                "instrument_id": "i1",
                "label": "AAPL",
                "ticker": "AAPL",
                "quantity": 10,
                "price": 100,
                "ccy": "USD",
            },
        ]
    )
    exp = currency_exposure(rows=rows, rates=rates, base="SGD")
    assert "USD" in exp
    # 10 * 100 * 1.0 = 1000 USD → to_base 1000 USD in SGD rates: 1000 * (1.0/0.74) ≈ 1351.35
    assert exp["USD"]["base_value"] > 0
    assert exp["USD"]["weight_pct"] == 100.0
    assert len(exp["USD"]["holdings"]) == 1
    assert exp["USD"]["holdings"][0]["instrument_id"] == "i1"


def test_currency_exposure_multi_currency():
    from backend.services.macro_exposure import currency_exposure

    rates = {"SGD": 1.0, "USD": 0.74, "EUR": 0.68}
    rows = _make_rows(
        [
            {"instrument_id": "i1", "ccy": "USD", "quantity": 10, "price": 100},
            {"instrument_id": "i2", "ccy": "EUR", "quantity": 5, "price": 200},
        ]
    )
    exp = currency_exposure(rows=rows, rates=rates, base="SGD")
    assert "USD" in exp
    assert "EUR" in exp
    assert abs(exp["USD"]["weight_pct"] + exp["EUR"]["weight_pct"] - 100.0) < 0.1


def test_currency_exposure_skips_zero_price():
    from backend.services.macro_exposure import currency_exposure

    rates = {"SGD": 1.0, "USD": 0.74}
    rows = _make_rows(
        [
            {"instrument_id": "i1", "ccy": "USD", "price": 0},
        ]
    )
    exp = currency_exposure(rows=rows, rates=rates, base="SGD")
    assert exp == {}


def test_currency_exposure_skips_non_liquid():
    from backend.services.macro_exposure import currency_exposure

    rates = {"SGD": 1.0, "USD": 0.74}
    rows = _make_rows(
        [
            {"instrument_id": "i1", "ccy": "USD", "vesting_state": "unvested", "price": 100},
        ]
    )
    exp = currency_exposure(rows=rows, rates=rates, base="SGD")
    assert exp == {}


def test_currency_exposure_sums_match_to_base():
    """currency_exposure base_value must match manual to_base computation."""
    from backend.core.fx import to_base
    from backend.services.macro_exposure import currency_exposure

    rates = {"SGD": 1.0, "USD": 0.74}
    qty, price, qpu = 5.0, 200.0, 1.0
    rows = _make_rows(
        [{"instrument_id": "i1", "ccy": "USD", "quantity": qty, "price": price, "qpu": qpu}]
    )
    exp = currency_exposure(rows=rows, rates=rates, base="SGD")
    expected_bv = to_base(qty * price * qpu, "USD", "SGD", rates=rates)
    assert abs(exp["USD"]["base_value"] - expected_bv) < 0.01


def test_currency_exposure_leaves_out_a_currency_with_no_rate():
    from backend.services.macro_exposure import currency_exposure

    rates = {"SGD": 1.0, "EUR": 0.68}  # USD rate absent
    rows = _make_rows(
        [
            {"instrument_id": "i1", "ccy": "USD", "quantity": 10, "price": 100},
            {"instrument_id": "i2", "ccy": "EUR", "quantity": 5, "price": 200},
        ]
    )
    exp = currency_exposure(rows=rows, rates=rates, base="SGD")
    assert set(exp) == {"EUR"}
    assert exp["EUR"]["weight_pct"] == 100.0  # of the valued book only


# ── affected_holdings (FX) ─────────────────────────────────────────────────────


def test_affected_holdings_fx_returns_usd_rows():
    from backend.services.macro_exposure import affected_holdings

    rates = {"SGD": 1.0, "USD": 0.74}
    rows = _make_rows(
        [
            {"instrument_id": "i1", "ccy": "USD", "quantity": 10, "price": 100},
            {"instrument_id": "i2", "ccy": "SGD", "quantity": 5, "price": 200},
        ]
    )
    result = affected_holdings(
        "fx", currency="USD", rows=rows, rates=rates, base="SGD", direction="down"
    )
    assert len(result) == 1
    assert result[0]["instrument_id"] == "i1"
    assert result[0]["direction"] == "down"
    assert result[0]["base_value"] > 0


def test_affected_holdings_fx_empty_when_no_match():
    from backend.services.macro_exposure import affected_holdings

    rates = {"SGD": 1.0, "USD": 0.74}
    rows = _make_rows([{"instrument_id": "i1", "ccy": "SGD", "quantity": 5, "price": 100}])
    result = affected_holdings(
        "fx", currency="USD", rows=rows, rates=rates, base="SGD", direction="up"
    )
    assert result == []


def test_affected_holdings_fx_missing_rate_direction_unknown():
    """When the FX rate is missing, direction should be 'unknown'."""
    from backend.services.macro_exposure import affected_holdings

    rates = {"SGD": 1.0}  # USD rate absent
    rows = _make_rows([{"instrument_id": "i1", "ccy": "USD", "quantity": 10, "price": 100}])
    result = affected_holdings(
        "fx", currency="USD", rows=rows, rates=rates, base="SGD", direction="down"
    )
    assert len(result) == 1
    assert result[0]["direction"] == "unknown"
    assert result[0]["impact"] == "directional"
    assert result[0]["base_value"] is None  # never valued at an invented 1:1


# ── affected_holdings (sector/theme) ──────────────────────────────────────────


def test_affected_holdings_sector_by_tag():
    from backend.services.macro_exposure import affected_holdings

    rates = {"SGD": 1.0, "USD": 0.74}
    rows = _make_rows(
        [
            {"instrument_id": "i1", "ccy": "USD", "tags": ["tech"], "sector": ""},
            {"instrument_id": "i2", "ccy": "USD", "tags": ["finance"], "sector": ""},
        ]
    )
    result = affected_holdings(
        "sector", sector="tech", rows=rows, rates=rates, base="SGD", direction="up"
    )
    assert len(result) == 1
    assert result[0]["instrument_id"] == "i1"
    assert result[0]["impact"] == "directional"


def test_affected_holdings_sector_by_sector_field():
    from backend.services.macro_exposure import affected_holdings

    rates = {"SGD": 1.0, "USD": 0.74}
    rows = _make_rows(
        [
            {"instrument_id": "i1", "ccy": "USD", "tags": [], "sector": "Technology"},
        ]
    )
    result = affected_holdings(
        "sector", sector="technology", rows=rows, rates=rates, base="SGD", direction="down"
    )
    assert len(result) == 1
    assert result[0]["instrument_id"] == "i1"


def test_affected_holdings_sector_empty_when_no_match():
    from backend.services.macro_exposure import affected_holdings

    rates = {"SGD": 1.0, "USD": 0.74}
    rows = _make_rows([{"instrument_id": "i1", "ccy": "USD", "tags": [], "sector": "Finance"}])
    result = affected_holdings(
        "sector", sector="tech", rows=rows, rates=rates, base="SGD", direction="up"
    )
    assert result == []
