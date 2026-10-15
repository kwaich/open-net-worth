"""Edge-case tests for options premium money path (sys-options review fixes).

Covers:
  - premium_account_id + short → exactly one income row (no category, no tax verdict)
  - premium_account_id + long → exactly one expense row (no category)
  - omitted premium_account_id → zero ledger rows
  - idempotency_key retry does NOT double-book the cash leg (external_ref dedup)
  - targeted expire: _apply_expire with contract_id flips only that contract
  - uncovered_call_count includes coverage='unknown' contracts
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from backend.core.txn_sources import primary_source
from backend.services import options as opt_svc


@pytest.fixture
def full_stores(tmp_path, monkeypatch, db_isolated, fx_rates):
    """Redirect ALL stores (options + accounts + instruments + transactions) to an isolated
    database (db_isolated — accounts/transactions are DB-backed now, plan 3 task 4; every
    unpinned store instance, incl. the ledger module's own singletons, resolves against it
    automatically, no per-instance wiring needed)."""
    from backend.core import fx as fx_mod
    from backend.services import lots as lots_service

    monkeypatch.setattr(fx_mod, "base_currency", lambda: "USD")
    monkeypatch.setattr(fx_mod, "to_base", lambda a, c, b=None, rates=None: round(float(a or 0), 2))
    monkeypatch.setattr(lots_service, "_load_rates", lambda: {"USD": 1.0})
    return tmp_path


@pytest.fixture
def seeds(full_stores):
    from backend.db.account_store import AccountStore
    from backend.db.instrument_store import InstrumentStore

    acct = AccountStore().create(
        {
            "name": "Vantage Brokerage",
            "kind": "asset",
            "type": "investment",
            "currency": "USD",
        }
    )
    cash_acct = AccountStore().create(
        {
            "name": "Vantage Brokerage USD",
            "kind": "asset",
            "type": "cash",
            "currency": "USD",
        }
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


def _all_txns(full_stores):
    from backend.db.transaction_store import TransactionStore

    # Create a fresh instance using the redirected path (set by the full_stores fixture).
    return TransactionStore().all()


class TestPremiumMoneyPath:
    def test_short_with_premium_account_books_income(self, seeds, full_stores):
        """Short + premium_account_id → exactly one income row (no category, no tax verdict)."""
        future = (date.today() + timedelta(days=60)).isoformat()
        result = opt_svc.apply_option_change(
            {
                "type": "open",
                "account_id": seeds["account"]["id"],
                "underlying_instrument_id": seeds["instrument"]["id"],
                "option_type": "call",
                "side": "short",
                "strike": 160.0,
                "expiry": future,
                "contracts": 1.0,
                "premium_per_share": 2.50,
                "premium_account_id": seeds["cash_account"]["id"],
            }
        )
        assert result.get("ok") is True
        txns = _all_txns(full_stores)
        # category-opaque-ids migration (decision #3): no "options-premium" category.
        # The deterministic external_source is the authoritative marker.
        premium_txns = [t for t in txns if primary_source(t) == "option-premium"]
        assert len(premium_txns) == 1
        assert premium_txns[0]["kind"] == "income"
        assert premium_txns[0]["to_account_id"] == seeds["cash_account"]["id"]
        assert premium_txns[0].get("category") is None
        # Regression (2026-07-20 note-field retirement): `_maybe_book_premium_leg` used to
        # pass the leg's text as `"note"` in the create_transaction dict, relying on a
        # choke-point note→description alias that has since been removed — without the fix,
        # this leg would silently book with a BLANK description.
        assert premium_txns[0]["description"].startswith("Option premium — CALL")

    def test_long_with_premium_account_books_expense(self, seeds, full_stores):
        """Long + premium_account_id → exactly one expense row (no category)."""
        future = (date.today() + timedelta(days=60)).isoformat()
        result = opt_svc.apply_option_change(
            {
                "type": "open",
                "account_id": seeds["account"]["id"],
                "underlying_instrument_id": seeds["instrument"]["id"],
                "option_type": "put",
                "side": "long",
                "strike": 140.0,
                "expiry": future,
                "contracts": 1.0,
                "premium_per_share": 1.50,
                "premium_account_id": seeds["cash_account"]["id"],
            }
        )
        assert result.get("ok") is True
        txns = _all_txns(full_stores)
        # No system "options-premium" category anymore (decision #3) — paid premium carries
        # no category (None/Uncategorized).
        premium_txns = [t for t in txns if primary_source(t) == "option-premium"]
        assert len(premium_txns) == 1
        assert premium_txns[0]["kind"] == "expense"
        assert premium_txns[0]["from_account_id"] == seeds["cash_account"]["id"]
        assert premium_txns[0].get("category") is None
        # Regression (2026-07-20 note-field retirement) — see the short-leg test above.
        assert premium_txns[0]["description"].startswith("Option premium — PUT")

    def test_no_premium_account_books_nothing(self, seeds, full_stores):
        """Omitted premium_account_id → zero ledger rows."""
        future = (date.today() + timedelta(days=60)).isoformat()
        opt_svc.apply_option_change(
            {
                "type": "open",
                "account_id": seeds["account"]["id"],
                "underlying_instrument_id": seeds["instrument"]["id"],
                "option_type": "call",
                "side": "short",
                "strike": 170.0,
                "expiry": future,
                "contracts": 1.0,
                "premium_per_share": 3.0,
                # No premium_account_id
            }
        )
        txns = _all_txns(full_stores)
        assert len(txns) == 0

    def test_idempotency_key_retry_does_not_double_book(self, seeds, full_stores):
        """Idempotency_key retry (same contract) → exactly one premium row regardless of retries."""
        future = (date.today() + timedelta(days=60)).isoformat()
        base_change = {
            "type": "open",
            "account_id": seeds["account"]["id"],
            "underlying_instrument_id": seeds["instrument"]["id"],
            "option_type": "call",
            "side": "short",
            "strike": 180.0,
            "expiry": future,
            "contracts": 1.0,
            "premium_per_share": 2.0,
            "premium_account_id": seeds["cash_account"]["id"],
            "idempotency_key": "broker-order-abc-123",
        }
        opt_svc.apply_option_change(base_change)
        opt_svc.apply_option_change(base_change)  # retry → idempotent no-op
        txns = _all_txns(full_stores)
        premium_txns = [t for t in txns if primary_source(t) == "option-premium"]
        assert len(premium_txns) == 1  # second call was a no-op; no double-booking


class TestTargetedExpire:
    def test_targeted_expire_flips_only_specified_contract(self, seeds):
        """_apply_expire with contract_id flips only that contract, not the whole book."""
        future = (date.today() + timedelta(days=10)).isoformat()  # NOT past expiry
        r = opt_svc.apply_option_change(
            {
                "type": "open",
                "account_id": seeds["account"]["id"],
                "underlying_instrument_id": seeds["instrument"]["id"],
                "option_type": "call",
                "side": "long",
                "strike": 130.0,
                "expiry": future,
                "contracts": 1.0,
            }
        )
        cid = r["contract_id"]
        # Force-expire it via targeted expire (bypasses the past-expiry date check in the sweep)
        result = opt_svc.apply_option_change({"type": "expire", "contract_id": cid})
        assert result.get("ok") is True
        assert result.get("expired") == 1
        from backend.db.option_contract_store import OptionContractStore

        c = OptionContractStore().find_by_id(cid)
        assert c["status"] == "expired"

    def test_targeted_expire_refuses_non_open(self, seeds):
        """Targeting a closed contract → error."""
        future = (date.today() + timedelta(days=10)).isoformat()
        r = opt_svc.apply_option_change(
            {
                "type": "open",
                "account_id": seeds["account"]["id"],
                "underlying_instrument_id": seeds["instrument"]["id"],
                "option_type": "put",
                "side": "short",
                "strike": 120.0,
                "expiry": future,
                "contracts": 1.0,
            }
        )
        cid = r["contract_id"]
        opt_svc.apply_option_change({"type": "close", "contract_id": cid})
        result = opt_svc.apply_option_change({"type": "expire", "contract_id": cid})
        assert "error" in result


class TestUncoveredCallCount:
    def test_unknown_coverage_counts_toward_uncovered(self, seeds, monkeypatch):
        """Short call with no collateral_account_id (coverage='unknown') counts toward uncovered_call_count."""
        future = (date.today() + timedelta(days=30)).isoformat()
        opt_svc.apply_option_change(
            {
                "type": "open",
                "account_id": seeds["account"]["id"],
                "underlying_instrument_id": seeds["instrument"]["id"],
                "option_type": "call",
                "side": "short",
                "strike": 200.0,
                "expiry": future,
                "contracts": 1.0,
                # No collateral_account_id → coverage='unknown'
            }
        )
        monkeypatch.setattr(opt_svc, "_resolve_underlying_price", lambda c: 150.0)
        monkeypatch.setattr("backend.services.positions.hydrated", lambda: [])
        s = opt_svc.summary()
        assert s["uncovered_call_count"] >= 1
        # Confirm coverage is "unknown" (not "uncovered")
        holdings = s["holdings"]
        assert any(h.get("coverage") == "unknown" for h in holdings)
