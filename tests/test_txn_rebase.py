"""
Reporting amounts are recomputed into the CURRENT base currency from each transaction's
native leg, not read from the stored base_amount (a snapshot in the base that was active
when the txn was written — stale after a base-currency change, e.g. an SGD figure shown
under a VND base).

Covers ledger_service.txn_base_amount + the get_ledger_summary aggregation.
"""

from __future__ import annotations

import pytest

from backend.api import ledger
from backend.services import fx_cache as fx_cache_mod
from backend.services import ledger as svc

# Base = VND; 1 SGD = 17,000 VND, 1 USD = 23,000 VND.
_FX = {"VND": 1.0, "SGD": 17_000.0, "USD": 23_000.0}


def _exp(frm, ccy, snapshot):
    return {
        "kind": "expense",
        "from_amount": frm,
        "from_currency": ccy,
        "to_amount": None,
        "to_currency": None,
        "base_amount": snapshot,
    }


def _inc(to, ccy, snapshot):
    return {
        "kind": "income",
        "to_amount": to,
        "to_currency": ccy,
        "from_amount": None,
        "from_currency": None,
        "base_amount": snapshot,
    }


def test_expense_uses_from_leg_into_current_base():
    # 100 SGD expense, but base_amount snapshot says 100 (stale, written under SGD base).
    t = _exp(100.0, "SGD", snapshot=100.0)
    assert svc.txn_base_amount(t, "VND", _FX) == pytest.approx(1_700_000.0)


def test_income_uses_to_leg_into_current_base():
    t = _inc(50.0, "USD", snapshot=67.0)
    assert svc.txn_base_amount(t, "VND", _FX) == pytest.approx(1_150_000.0)


def test_native_base_currency_is_identity():
    t = _exp(2_500_000.0, "VND", snapshot=2_500_000.0)
    assert svc.txn_base_amount(t, "VND", _FX) == pytest.approx(2_500_000.0)


def test_missing_leg_falls_back_to_snapshot():
    # No native leg amounts → use the stored snapshot rather than zeroing.
    t = {"kind": "adjustment", "from_amount": None, "to_amount": None, "base_amount": 42.0}
    assert svc.txn_base_amount(t, "VND", _FX) == pytest.approx(42.0)


def test_ledger_summary_rebases_to_current_base(monkeypatch):
    # A 100-SGD expense + a 50-USD income; stored snapshots are the old (SGD) base values.
    txns = [
        {
            "id": "1",
            "kind": "expense",
            "deleted": False,
            "date": "2026-06-10",
            "from_account_id": "x",
            "from_amount": 100.0,
            "from_currency": "SGD",
            "base_amount": 100.0,
        },
        {
            "id": "2",
            "kind": "income",
            "deleted": False,
            "date": "2026-06-12",
            "to_account_id": "y",
            "to_amount": 50.0,
            "to_currency": "USD",
            "base_amount": 67.0,
        },
    ]
    monkeypatch.setattr(svc.transactions, "all", lambda: [dict(t) for t in txns])
    monkeypatch.setattr(svc, "base_currency", lambda: "VND")
    monkeypatch.setattr(fx_cache_mod, "load_fx_cache", lambda: dict(_FX))

    out = ledger.get_ledger_summary(month=6, year=2026)
    assert out["base_currency"] == "VND"
    assert out["total_out"] == pytest.approx(1_700_000.0)  # 100 SGD → VND, not 100
    assert out["total_in"] == pytest.approx(1_150_000.0)  # 50 USD → VND, not 67
    assert out["net"] == pytest.approx(-550_000.0)
