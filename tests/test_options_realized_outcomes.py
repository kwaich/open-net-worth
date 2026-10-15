"""Options realized outcomes (options-facts-not-verdicts, ADR 2026-07-12).

The write path records FACTS only:

  - options_service.realized_outcomes: short expired keeps full premium; short closed
    nets premium - buyback; long closed nets sale - premium; a closed contract with NO
    recorded close price realizes None (never fabricated 0) and counts as incomplete;
    window filters on closed_at; open contracts excluded; FX recomputed at read.

Isolation: same full-store redirection as tests/test_options_close_leg.py.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from backend.services import options as opt_svc


@pytest.fixture
def full_stores(tmp_path, monkeypatch, db_isolated, fx_rates):
    """accounts/transactions are DB-backed (plan 3 task 4) — db_isolated redirects them
    (and OptionContractStore's own account FK checks) to a fresh, empty per-test database."""
    from backend.core import fx as fx_mod
    from backend.db.account_store import AccountStore
    from backend.db.transaction_store import TransactionStore
    from backend.services import ledger as ledger_service
    from backend.services import lots as lots_service

    monkeypatch.setattr(ledger_service, "transactions", TransactionStore())
    monkeypatch.setattr(ledger_service, "accounts", AccountStore())
    monkeypatch.setattr(fx_mod, "base_currency", lambda: "USD")
    monkeypatch.setattr(fx_mod, "to_base", lambda a, c, b=None, rates=None: round(float(a or 0), 2))
    monkeypatch.setattr(lots_service, "_load_rates", lambda: {"USD": 1.0})
    return tmp_path


@pytest.fixture
def seeds(full_stores):
    from backend.db.account_store import AccountStore
    from backend.db.instrument_store import InstrumentStore

    acct = AccountStore().create(
        {"name": "Vantage Brokerage", "kind": "asset", "type": "investment", "currency": "USD"}
    )
    cash_acct = AccountStore().create(
        {"name": "Vantage Brokerage USD", "kind": "asset", "type": "cash", "currency": "USD"}
    )
    inst = InstrumentStore().create(
        {
            "symbol": "AAPL",
            "name": "Apple Inc.",
            "asset_class": "equity",
            "quote_currency": "USD",
            "price_source": "manual",
            "manual_price": 150.0,
        }
    )
    return {"account": acct, "cash_account": cash_acct, "instrument": inst}


_YEAR = date.today().year
_FROM = f"{_YEAR}-01-01"
_TO = f"{_YEAR}-12-31"


def _open(seeds, *, side: str, pps: float = 2.5, contracts: float = 2.0, **extra) -> str:
    result = opt_svc.apply_option_change(
        {
            "type": "open",
            "account_id": seeds["account"]["id"],
            "underlying_instrument_id": seeds["instrument"]["id"],
            "option_type": extra.pop("option_type", "call"),
            "side": side,
            "strike": extra.pop("strike", 160.0),
            "expiry": (date.today() + timedelta(days=30)).isoformat(),
            "contracts": contracts,
            "premium_per_share": pps,
            "premium_currency": "USD",
            **extra,
        }
    )
    assert result.get("ok") is True
    return result["contract_id"]


class TestRealizedOutcomes:
    def test_short_expired_keeps_full_premium(self, seeds):
        cid = _open(seeds, side="short")  # 2 × 100 × 2.5 = 500 received
        opt_svc.apply_option_change(
            {"type": "expire", "contract_id": cid, "as_of": date.today().isoformat()}
        )
        out = opt_svc.realized_outcomes(_FROM, _TO)
        assert out["contract_count"] == 1
        row = out["rows"][0]
        assert row["status"] == "expired"
        assert row["close_total_native"] == 0.0
        assert row["realized_base"] == pytest.approx(500.0)
        assert out["net_realized_base"] == pytest.approx(500.0)
        assert out["incomplete_count"] == 0

    def test_short_closed_nets_buyback(self, seeds):
        cid = _open(seeds, side="short")  # 500 received
        opt_svc.apply_option_change(
            {
                "type": "close",
                "contract_id": cid,
                "close_premium_per_share": 1.0,  # 200 buyback
                "premium_account_id": seeds["cash_account"]["id"],
            }
        )
        out = opt_svc.realized_outcomes(_FROM, _TO)
        assert out["rows"][0]["realized_base"] == pytest.approx(300.0)

    def test_long_closed_nets_sale_minus_premium(self, seeds):
        cid = _open(seeds, side="long", pps=1.5)  # 300 paid
        opt_svc.apply_option_change(
            {
                "type": "close",
                "contract_id": cid,
                "close_premium_per_share": 2.5,  # 500 sale
                "premium_account_id": seeds["cash_account"]["id"],
            }
        )
        out = opt_svc.realized_outcomes(_FROM, _TO)
        assert out["rows"][0]["realized_base"] == pytest.approx(200.0)

    def test_long_expired_loses_full_premium(self, seeds):
        cid = _open(seeds, side="long", pps=1.5)  # 300 paid
        opt_svc.apply_option_change(
            {"type": "expire", "contract_id": cid, "as_of": date.today().isoformat()}
        )
        out = opt_svc.realized_outcomes(_FROM, _TO)
        assert out["rows"][0]["realized_base"] == pytest.approx(-300.0)

    def test_close_without_price_is_unknown_never_zero(self, seeds):
        cid = _open(seeds, side="short")
        opt_svc.apply_option_change({"type": "close", "contract_id": cid})  # status flip only
        out = opt_svc.realized_outcomes(_FROM, _TO)
        row = out["rows"][0]
        assert row["complete"] is False
        assert row["realized_native"] is None
        assert row["realized_base"] is None
        assert out["incomplete_count"] == 1
        assert out["net_realized_base"] == pytest.approx(0.0)  # excluded, not counted as 0

    def test_open_contracts_and_out_of_window_excluded(self, seeds):
        _open(seeds, side="short")  # stays open
        cid = _open(seeds, side="short", strike=170.0)
        opt_svc.apply_option_change(
            {
                "type": "close",
                "contract_id": cid,
                "close_premium_per_share": 0.5,
                "premium_account_id": seeds["cash_account"]["id"],
            }
        )
        last_year = _YEAR - 1
        out = opt_svc.realized_outcomes(f"{last_year}-01-01", f"{last_year}-12-31")
        assert out["contract_count"] == 0
        assert out["net_realized_base"] == 0.0
