"""Regression: the ONE canonical market fold (qty × price × quote_per_unit → base).

H1 — cashflow._market_balance_by_class dropped `quote_per_unit`, so a metal-by-gram
holding (quote_per_unit ≈ 1/31.1035) was mis-valued ~31× in the cash-flow runway versus
net worth (ledger) and the X-ray. All three now route through `ledger.row_base_value`;
this test pins that they agree on a non-default-quote_per_unit holding. It FAILS before
the fix (cashflow value ≈ 31× the others).

Offline + isolated: identity FX (currency == base), positions hydration monkeypatched.
"""

from __future__ import annotations

import pytest

from backend.services import cashflow as cashflow_svc
from backend.services import ledger as ledger_svc
from backend.services import positions as positions_service
from backend.services import xray as xray_svc

GRAMS_PER_TROY_OZ = 31.1035


# The runway rung counts positions held in an `investment` account only — a retirement
# account's holdings are never spendable (ADR 2026-09-07-seven-account-types-one-axis). That
# is a SCOPE difference, not a fold difference, so every row here is held at one investment
# account and the three folds stay directly comparable.
_BROKERAGE = {"id": "acc_ibkr", "type": "investment"}


def _accounts_are_one_brokerage(monkeypatch):
    monkeypatch.setattr(cashflow_svc.svc.accounts, "query_accounts", lambda *a, **k: [_BROKERAGE])


def _gold_gram_row():
    # 10 g of gold, priced per troy ounce; quote_per_unit converts grams → troy oz.
    return {
        "account_id": _BROKERAGE["id"],
        "instrument_id": "gold",
        "asset_class": "commodity",
        "quantity": 10.0,
        "price": 2000.0,  # USD / troy oz
        "quote_per_unit": 1.0 / GRAMS_PER_TROY_OZ,
        "currency": "USD",
        "ticker": "GC=F",
        "sector": "",
    }


@pytest.fixture
def isolated(monkeypatch):
    row = _gold_gram_row()
    monkeypatch.setattr(positions_service, "hydrated", lambda *a, **k: [row])
    _accounts_are_one_brokerage(monkeypatch)
    # base USD; identity FX so we compare folds, not FX (the row's currency == base).
    monkeypatch.setattr(ledger_svc, "base_currency", lambda: "USD")
    monkeypatch.setattr(xray_svc, "base_currency", lambda: "USD")
    monkeypatch.setattr(
        ledger_svc, "to_base", lambda amt, ccy, base=None, rates=None: round(float(amt), 2)
    )
    return row


def test_row_base_value_includes_quote_per_unit(isolated):
    # 10 g × 2000/oz × (1/31.1035) ≈ 643.05, NOT 10 × 2000 = 20000.
    expected = round(10.0 * 2000.0 * (1.0 / GRAMS_PER_TROY_OZ), 2)
    assert ledger_svc.row_base_value(isolated, "USD") == expected
    assert expected == pytest.approx(643.01, abs=0.01)


def test_three_call_sites_agree_on_gram_gold(isolated):
    """Net worth (ledger), cash-flow market baseline (cashflow), and X-ray must value the
    SAME holding identically — the divergence H1 fixed."""
    nw_value = ledger_svc._market_value_base("USD")
    cf_value = cashflow_svc._positions_runway_base("USD")
    xray_value = xray_svc.portfolio_xray(base="USD")["total_base"]

    expected = round(10.0 * 2000.0 * (1.0 / GRAMS_PER_TROY_OZ), 2)
    assert nw_value == expected
    assert cf_value == expected
    assert xray_value == expected
    assert nw_value == cf_value == xray_value


def test_three_call_sites_agree_unvested_equity_is_excluded_by_all(monkeypatch):
    """`ledger._market_value_base` already excludes `vesting_state="unvested"` rows (ADR
    2026-07-31-unvested-equity-excluded-from-net-worth). cashflow's `_positions_runway_base`
    and xray's `portfolio_xray` must exclude it too, or the three canonical call sites this
    file pins as agreeing quietly diverge the moment one holding is an unvested RSU tranche."""
    vested_row = _gold_gram_row()
    unvested_row = {
        "account_id": _BROKERAGE["id"],
        "instrument_id": "rsu",
        "asset_class": "equity",
        "quantity": 1000.0,
        "price": 1000.0,
        "quote_per_unit": 1.0,
        "currency": "USD",
        "ticker": "RSU",
        "sector": "",
        "vesting_state": "unvested",
    }
    monkeypatch.setattr(positions_service, "hydrated", lambda *a, **k: [vested_row, unvested_row])
    _accounts_are_one_brokerage(monkeypatch)
    monkeypatch.setattr(ledger_svc, "base_currency", lambda: "USD")
    monkeypatch.setattr(xray_svc, "base_currency", lambda: "USD")
    monkeypatch.setattr(
        ledger_svc, "to_base", lambda amt, ccy, base=None, rates=None: round(float(amt), 2)
    )

    nw_value = ledger_svc._market_value_base("USD")
    cf_value = cashflow_svc._positions_runway_base("USD")
    xray_value = xray_svc.portfolio_xray(base="USD")["total_base"]

    expected = round(10.0 * 2000.0 * (1.0 / GRAMS_PER_TROY_OZ), 2)  # gold only — RSU excluded
    assert nw_value == expected
    assert cf_value == expected
    assert xray_value == expected
    assert nw_value == cf_value == xray_value
