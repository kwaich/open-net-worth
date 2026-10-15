"""Close-leg symmetry for options (options-e2e spec, 2026-07-12).

apply_option_change(type="close") may book the buy-to-close / sell-to-close cash
leg through the ledger choke point when BOTH close_premium_per_share and
premium_account_id are supplied — mirroring _maybe_book_premium_leg on open:

  - short → buy-to-close = expense from the wallet
  - long  → sell-to-close = income to the wallet
  - omitted fields / zero amount → status flip only, zero ledger rows
  - the leg books BEFORE the status flip: a failed leg leaves the contract open
  - second close is idempotent and never double-books
  - close_premium_per_share is snapshotted onto the contract (display only)

Isolation: same full-store redirection as tests/test_options_edge.py.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from backend.core.txn_sources import primary_ref, primary_source
from backend.services import options as opt_svc


@pytest.fixture
def full_stores(tmp_path, monkeypatch, db_isolated, fx_rates):
    """Redirect ALL stores (options + accounts + instruments + transactions) to tmp_path.
    InstrumentStore is DB-backed (plan 2, task 5); accounts/transactions are DB-backed
    too (plan 3, task 4) — all resolve against the per-test isolated database
    (db_isolated) instead."""
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


def _open_contract(seeds, *, side: str, option_type: str = "call") -> str:
    future = (date.today() + timedelta(days=60)).isoformat()
    result = opt_svc.apply_option_change(
        {
            "type": "open",
            "account_id": seeds["account"]["id"],
            "underlying_instrument_id": seeds["instrument"]["id"],
            "option_type": option_type,
            "side": side,
            "strike": 160.0,
            "expiry": future,
            "contracts": 2.0,
            "premium_per_share": 2.50,
            "premium_currency": "USD",
        }
    )
    assert result.get("ok") is True
    return result["contract_id"]


def _close_txns(full_stores):
    from backend.db.transaction_store import TransactionStore

    return [t for t in TransactionStore().all() if primary_source(t) == "option-close"]


class TestCloseLeg:
    def test_close_without_leg_is_status_flip_only(self, seeds, full_stores):
        cid = _open_contract(seeds, side="short")
        result = opt_svc.apply_option_change({"type": "close", "contract_id": cid})
        assert result.get("ok") is True
        assert _close_txns(full_stores) == []
        contract = opt_svc.get_contract(cid)
        assert contract["status"] == "closed"

    def test_close_short_with_leg_books_expense(self, seeds, full_stores):
        cid = _open_contract(seeds, side="short")
        result = opt_svc.apply_option_change(
            {
                "type": "close",
                "contract_id": cid,
                "close_premium_per_share": 1.25,
                "premium_account_id": seeds["cash_account"]["id"],
            }
        )
        assert result.get("ok") is True
        txns = _close_txns(full_stores)
        assert len(txns) == 1
        # buy-to-close: 2 contracts × 100 × 1.25 = 250 out of the wallet
        assert txns[0]["kind"] == "expense"
        assert txns[0]["from_account_id"] == seeds["cash_account"]["id"]
        assert txns[0]["from_amount"] == pytest.approx(250.0)
        assert txns[0]["from_currency"] == "USD"
        assert primary_source(txns[0]) == "option-close"
        assert primary_ref(txns[0]) == cid
        # Regression (2026-07-20 note-field retirement): _maybe_book_close_premium_leg used
        # to pass this text as "note", relying on a now-removed choke-point alias — without
        # the fix the leg would silently book with a blank description.
        assert txns[0]["description"].startswith("Option close — CALL")

    def test_close_long_with_leg_books_income_no_taxable(self, seeds, full_stores):
        cid = _open_contract(seeds, side="long")
        result = opt_svc.apply_option_change(
            {
                "type": "close",
                "contract_id": cid,
                "close_premium_per_share": 3.0,
                "premium_account_id": seeds["cash_account"]["id"],
            }
        )
        assert result.get("ok") is True
        txns = _close_txns(full_stores)
        assert len(txns) == 1
        # sell-to-close: 2 × 100 × 3.0 = 600 into the wallet.
        assert txns[0]["kind"] == "income"
        assert txns[0]["to_account_id"] == seeds["cash_account"]["id"]
        assert txns[0]["to_amount"] == pytest.approx(600.0)

    def test_close_zero_buyback_books_nothing(self, seeds, full_stores):
        cid = _open_contract(seeds, side="short")
        result = opt_svc.apply_option_change(
            {
                "type": "close",
                "contract_id": cid,
                "close_premium_per_share": 0,
                "premium_account_id": seeds["cash_account"]["id"],
            }
        )
        assert result.get("ok") is True
        assert _close_txns(full_stores) == []
        assert opt_svc.get_contract(cid)["status"] == "closed"

    def test_second_close_is_idempotent_no_double_leg(self, seeds, full_stores):
        cid = _open_contract(seeds, side="short")
        change = {
            "type": "close",
            "contract_id": cid,
            "close_premium_per_share": 1.0,
            "premium_account_id": seeds["cash_account"]["id"],
        }
        opt_svc.apply_option_change(change)
        result = opt_svc.apply_option_change(change)
        assert result.get("idempotent") is True
        assert len(_close_txns(full_stores)) == 1

    def test_failed_leg_leaves_contract_open(self, seeds, full_stores, monkeypatch):
        cid = _open_contract(seeds, side="short")
        from backend.services import ledger as ledger_service

        def boom(_data):
            raise RuntimeError("ledger down")

        monkeypatch.setattr(ledger_service, "create_transaction", boom)
        result = opt_svc.apply_option_change(
            {
                "type": "close",
                "contract_id": cid,
                "close_premium_per_share": 1.0,
                "premium_account_id": seeds["cash_account"]["id"],
            }
        )
        assert "error" in result
        assert opt_svc.get_contract(cid)["status"] == "open"
        assert _close_txns(full_stores) == []

    def test_close_premium_snapshot_stored(self, seeds, full_stores):
        cid = _open_contract(seeds, side="short")
        opt_svc.apply_option_change(
            {
                "type": "close",
                "contract_id": cid,
                "close_premium_per_share": 1.75,
                "premium_account_id": seeds["cash_account"]["id"],
            }
        )
        assert opt_svc.get_contract(cid)["close_premium_per_share"] == pytest.approx(1.75)


class TestPhantomWalletFailClosed:
    """Service-level defense (REST path): a nonexistent premium account is refused
    at the choke point — never an orphaned ledger leg (security review 2026-07-12)."""

    def test_open_with_phantom_wallet_refused(self, seeds, full_stores):
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
                "premium_per_share": 2.5,
                "premium_account_id": "acc_phantom",
            }
        )
        assert result == {"error": "premium account not found"}
        from backend.db.option_contract_store import OptionContractStore
        from backend.db.transaction_store import TransactionStore

        assert OptionContractStore().all() == []
        assert TransactionStore().all() == []

    def test_close_with_phantom_wallet_refused(self, seeds, full_stores):
        cid = _open_contract(seeds, side="short")
        result = opt_svc.apply_option_change(
            {
                "type": "close",
                "contract_id": cid,
                "close_premium_per_share": 1.0,
                "premium_account_id": "acc_phantom",
            }
        )
        assert result == {"error": "premium account not found"}
        assert opt_svc.get_contract(cid)["status"] == "open"
        assert _close_txns(full_stores) == []
