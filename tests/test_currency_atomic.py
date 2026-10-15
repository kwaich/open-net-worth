"""
Regression tests for the AI-tool currency contract and bulk_add atomicity.

FIX A (bank-statement XLSX import fix) — currency default: an omitted 'currency' once
         defaulted to the BASE currency, mis-denominating 200 000 VND as 200 000 SGD
         (~4 billion VND leg after FX re-conversion). The first fix defaulted to the
         account's currency instead.

FIX A2 (subscription-charge currency fix) — currency REQUIRED: the account-currency
         default was still a silent guess. An agent parsed a card alert as "USD 15.32"
         but omitted 'currency' in the add_transaction call → stored as SGD 15.32 at
         fx_rate=1, understating the expense by the FX factor. The contract is now:
         omitted/blank 'currency' → ValueError (the model must state the source
         currency); 'to_currency' on transfers still defaults to the other leg's own
         account currency (account metadata, not parsed from a fallible source).

FIX B — atomicity: add_transactions must be all-or-nothing. If any item fails
         pre-validation, 0 transactions must be created — no partial batches.

Categories are opaque ids (docs/adr/2026-07-05-category-opaque-ids.md): the category
values below are built-in seed ids (cat_13095ba70000, cat_a6b4e3bc0000, …) rather than the retired
human-readable keys. 'transfer' is a transaction KIND, not a category — it stays a
literal here (the AI helper clears kind-name category values before the write choke).

Tests:
  - test_currency_omitted_is_refused / test_currency_blank_is_refused /
    test_currency_non_iso_is_refused
      Regression for FIX A2: no silent denomination, ever.
  - test_currency_required_usd_charge_on_sgd_account
      The exact incident shape: a USD subscription charge on an SGD card.
  - test_currency_explicit_overrides_account_currency
      Explicit currency field is respected unchanged.
  - test_currency_transfer_to_leg_defaults_to_account_currency
      Transfer with 'currency' given, 'to_currency' omitted → to-leg uses its own
      account's currency.
  - test_currency_transfer_cross_currency_explicit
      Explicit cross-currency transfer: each leg uses the provided currency.
  - test_bulk_add_atomicity_no_partial_batch
      3 items, middle one has a bad account_id → created=0, no residual transactions.
  - test_bulk_add_all_valid_creates_all
      All items valid → all created, ok=True.
  - test_bulk_add_atomicity_errors_detail_covers_bad_item
      errors_detail must include the bad item's index/description.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from backend.ai.tools._shared import _account_currency, _txn_data_from_ai
from backend.services import ledger as ledger_service

# ── Shared fixtures ────────────────────────────────────────────────────────────


@pytest.fixture
def vnd_sgd_setup(tmp_path, monkeypatch):
    """VND account (id='vnd-1'), SGD base, FX rate VND/SGD ≈ 0.000049 (1 SGD ≈ 20 408 VND)."""
    from backend.db.account_store import AccountStore
    from backend.db.import_batch_store import ImportBatchStore
    from backend.db.transaction_store import TransactionStore

    acc_store = AccountStore()
    acc_store._path = tmp_path / "accounts.json"
    txn_store = TransactionStore()
    txn_store._path = tmp_path / "transactions.json"
    batch_store = ImportBatchStore()
    batch_store._path = tmp_path / "import_batches.json"

    monkeypatch.setattr(ledger_service, "accounts", acc_store)
    monkeypatch.setattr(ledger_service, "transactions", txn_store)
    monkeypatch.setattr(ledger_service, "import_batches", batch_store)
    monkeypatch.setattr(ledger_service, "base_currency", lambda: "SGD")

    # FX: 1 VND = 0.000049 SGD  →  1 SGD ≈ 20 408 VND
    fx_rates = {"SGD": 1.0, "VND": 0.000049}
    monkeypatch.setattr(
        ledger_service,
        "to_base",
        lambda amt, cur, base=None, rates=None: round(
            amt * fx_rates.get(cur, 1.0) / fx_rates.get(base or "SGD", 1.0), 2
        ),
    )

    # Rules off

    # Stub statements store (not under test)
    class _NoStatements:
        def add_batch_id(self, *a, **k):
            pass

    monkeypatch.setattr(ledger_service, "statements", _NoStatements())

    # Also patch _shared.svc so _txn_data_from_ai sees the isolated store
    from backend.ai.tools import _shared

    monkeypatch.setattr(_shared.svc, "accounts", acc_store)
    monkeypatch.setattr(_shared.svc, "base_currency", lambda: "SGD")

    vnd_account = acc_store.create(
        {
            "name": "Riverside Bank",
            "kind": "asset",
            "type": "cash",
            "currency": "VND",
            "initial_balance": 0.0,
        }
    )
    return vnd_account, txn_store, batch_store


@pytest.fixture
def two_vnd_accounts(tmp_path, monkeypatch):
    """Two VND accounts (src='vnd-a', dst='vnd-b'), SGD base."""
    from backend.db.account_store import AccountStore
    from backend.db.import_batch_store import ImportBatchStore
    from backend.db.transaction_store import TransactionStore

    acc_store = AccountStore()
    acc_store._path = tmp_path / "accounts.json"
    txn_store = TransactionStore()
    txn_store._path = tmp_path / "transactions.json"
    batch_store = ImportBatchStore()
    batch_store._path = tmp_path / "import_batches.json"

    monkeypatch.setattr(ledger_service, "accounts", acc_store)
    monkeypatch.setattr(ledger_service, "transactions", txn_store)
    monkeypatch.setattr(ledger_service, "import_batches", batch_store)
    monkeypatch.setattr(ledger_service, "base_currency", lambda: "SGD")

    fx_rates = {"SGD": 1.0, "VND": 0.000049}
    monkeypatch.setattr(
        ledger_service,
        "to_base",
        lambda amt, cur, base=None, rates=None: round(
            amt * fx_rates.get(cur, 1.0) / fx_rates.get(base or "SGD", 1.0), 2
        ),
    )

    class _NoStatements:
        def add_batch_id(self, *a, **k):
            pass

    monkeypatch.setattr(ledger_service, "statements", _NoStatements())

    from backend.ai.tools import _shared

    monkeypatch.setattr(_shared.svc, "accounts", acc_store)
    monkeypatch.setattr(_shared.svc, "base_currency", lambda: "SGD")

    src = acc_store.create(
        {"name": "Riverside Checking", "kind": "asset", "type": "cash", "currency": "VND"}
    )
    dst = acc_store.create(
        {"name": "Riverside Savings", "kind": "asset", "type": "cash", "currency": "VND"}
    )
    return src, dst, txn_store, batch_store


# ── FIX A2: currency is required — no silent denomination ──────────────────────


def test_currency_omitted_is_refused(vnd_sgd_setup):
    """REGRESSION (FIX A2): omitting 'currency' must raise, not guess a denomination."""
    vnd_account, _, _ = vnd_sgd_setup
    with pytest.raises(ValueError, match="currency is required"):
        _txn_data_from_ai(
            {
                "account_id": vnd_account["id"],
                "amount": 200_000,
                "description": "ATM withdrawal",
                "category": "cat_a6b4e3bc0000",
                "direction": "debit",
                # 'currency' intentionally omitted — the LLM didn't provide it
            },
            default_date="2026-06-01",
        )


def test_currency_blank_is_refused(vnd_sgd_setup):
    """A blank/whitespace currency is the same as omitted — refused."""
    vnd_account, _, _ = vnd_sgd_setup
    with pytest.raises(ValueError, match="currency is required"):
        _txn_data_from_ai(
            {
                "account_id": vnd_account["id"],
                "amount": 50.0,
                "description": "Coffee",
                "category": "cat_13095ba70000",
                "currency": "  ",
            },
            default_date="2026-06-01",
        )


def test_currency_non_iso_is_refused(vnd_sgd_setup):
    """A non-3-letter code ('US dollars', 'S$') is refused with an actionable message."""
    vnd_account, _, _ = vnd_sgd_setup
    with pytest.raises(ValueError, match="3-letter ISO 4217"):
        _txn_data_from_ai(
            {
                "account_id": vnd_account["id"],
                "amount": 50.0,
                "description": "Coffee",
                "category": "cat_13095ba70000",
                "currency": "US dollars",
            },
            default_date="2026-06-01",
        )


def test_currency_required_usd_charge_on_sgd_account(tmp_path, monkeypatch):
    """REGRESSION (FIX A2, incident shape): USD charge on an SGD card.

    The agent parsed a card alert as 'USD 15.32' but omitted 'currency' → the old
    account-currency default stored SGD 15.32 at fx_rate=1. Omission must now refuse,
    and passing 'usd' (any case) must denominate the leg in USD.
    """
    from backend.ai.tools import _shared
    from backend.db.account_store import AccountStore

    acc_store = AccountStore()
    acc_store._path = tmp_path / "accounts.json"
    monkeypatch.setattr(_shared.svc, "accounts", acc_store)
    monkeypatch.setattr(_shared.svc, "base_currency", lambda: "SGD")
    card = acc_store.create(
        {
            "name": "Meridian Bank Rewards",
            "kind": "liability",
            "type": "credit",
            "currency": "SGD",
        }
    )

    base_item = {
        "account_id": card["id"],
        "amount": 15.32,
        "description": "ANTHROPIC SAN FRANCISCO USA",
        "category": "cat_f2ff09940000",
    }
    with pytest.raises(ValueError, match="currency is required"):
        _txn_data_from_ai(dict(base_item), default_date="2026-06-10")

    data = _txn_data_from_ai(dict(base_item, currency="usd"), default_date="2026-06-10")
    assert data["from_currency"] == "USD"
    assert data["from_amount"] == 15.32


def test_base_amount_not_catastrophic(vnd_sgd_setup):
    """base_amount for 200_000 VND should be ~9.8 SGD, not 200_000 SGD or billions of VND."""
    vnd_account, txn_store, _ = vnd_sgd_setup
    data = _txn_data_from_ai(
        {
            "account_id": vnd_account["id"],
            "amount": 200_000,
            "description": "ATM withdrawal",
            "category": "cat_a6b4e3bc0000",
            "currency": "VND",
        },
        default_date="2026-06-01",
    )
    # Run it through create_transaction to compute base_amount
    txn = ledger_service.create_transaction(data)
    # 200_000 VND * 0.000049 SGD/VND = 9.8 SGD  (±rounding)
    assert txn["base_amount"] == pytest.approx(9.8, abs=1.0), (
        f"base_amount={txn['base_amount']} should be ~9.8 SGD for 200 000 VND @ FX 0.000049"
    )
    assert txn["from_currency"] == "VND"


def test_currency_explicit_overrides_account_currency(vnd_sgd_setup):
    """Explicit 'currency' is respected regardless of the account's stored currency."""
    vnd_account, _, _ = vnd_sgd_setup
    data = _txn_data_from_ai(
        {
            "account_id": vnd_account["id"],
            "amount": 50.0,
            "description": "Foreign charge in SGD",
            "category": "cat_a6b4e3bc0000",
            "currency": "SGD",  # explicitly SGD even though account is VND
        },
        default_date="2026-06-01",
    )
    assert data["from_currency"] == "SGD"
    assert data["from_amount"] == 50.0


# ── Transfers: 'to_currency' still defaults to the other leg's account currency ──


def test_currency_transfer_to_leg_defaults_to_account_currency(two_vnd_accounts):
    """Transfer with 'currency' given, 'to_currency' omitted → counter-leg uses its
    own account's currency (account metadata is a safe default; parsed amounts aren't)."""
    src, dst, _, _ = two_vnd_accounts
    data = _txn_data_from_ai(
        {
            "account_id": src["id"],
            "to_account_id": dst["id"],
            "amount": 500_000,
            "description": "Internal transfer",
            "category": "transfer",
            "direction": "debit",
            "currency": "VND",
            # 'to_currency' intentionally omitted
        },
        default_date="2026-06-01",
    )
    assert data["kind"] == "transfer"
    assert data["from_currency"] == "VND", (
        f"Expected from_currency='VND', got {data['from_currency']!r}"
    )
    assert data["to_currency"] == "VND", f"Expected to_currency='VND', got {data['to_currency']!r}"
    assert data["from_amount"] == 500_000.0
    assert data["to_amount"] == 500_000.0


def test_currency_transfer_credit_direction_both_legs_vnd(two_vnd_accounts):
    """Credit-direction transfer (incoming): omitted counter-leg currency defaults to
    that account's own currency."""
    src, dst, _, _ = two_vnd_accounts
    # dst is statement account (account_id), money arrives from src
    data = _txn_data_from_ai(
        {
            "account_id": dst["id"],
            "to_account_id": src["id"],  # treated as "from" because direction=credit
            "amount": 300_000,
            "description": "Incoming transfer",
            "category": "transfer",
            "direction": "credit",
            "currency": "VND",
        },
        default_date="2026-06-01",
    )
    assert data["kind"] == "transfer"
    assert data["from_currency"] == "VND"
    assert data["to_currency"] == "VND"


def test_currency_transfer_cross_currency_explicit(two_vnd_accounts, monkeypatch):
    """Explicit cross-currency transfer: currencies provided → used as-is."""
    src, dst, _, _ = two_vnd_accounts
    # Suppose dst is actually an SGD account; explicitly set currencies
    data = _txn_data_from_ai(
        {
            "account_id": src["id"],
            "to_account_id": dst["id"],
            "amount": 200_000,
            "to_amount": 9.8,
            "description": "Cross-currency transfer",
            "category": "transfer",
            "direction": "debit",
            "currency": "VND",
            "to_currency": "SGD",
        },
        default_date="2026-06-01",
    )
    assert data["from_currency"] == "VND"
    assert data["to_currency"] == "SGD"
    assert data["from_amount"] == 200_000.0
    assert data["to_amount"] == 9.8


def test_account_currency_helper_fallback(monkeypatch):
    """_account_currency falls back to base_currency when account has no currency field."""
    from backend.ai.tools import _shared

    monkeypatch.setattr(_shared.svc, "base_currency", lambda: "USD")
    monkeypatch.setattr(
        _shared.svc.accounts, "find_by_id", lambda _id: {"id": _id} if _id else None
    )
    # No 'currency' key on the account → falls back to base USD
    assert _account_currency("any-id") == "USD"


# ── FIX B: atomicity — all-or-nothing ──────────────────────────────────────────


@pytest.fixture
def sgd_setup(tmp_path, monkeypatch):
    """SGD setup with two accounts: acct_a (SGD), acct_b (SGD)."""
    from backend.db.account_store import AccountStore
    from backend.db.import_batch_store import ImportBatchStore
    from backend.db.transaction_store import TransactionStore

    acc_store = AccountStore()
    acc_store._path = tmp_path / "accounts.json"
    txn_store = TransactionStore()
    txn_store._path = tmp_path / "transactions.json"
    batch_store = ImportBatchStore()
    batch_store._path = tmp_path / "import_batches.json"

    monkeypatch.setattr(ledger_service, "accounts", acc_store)
    monkeypatch.setattr(ledger_service, "transactions", txn_store)
    monkeypatch.setattr(ledger_service, "import_batches", batch_store)
    monkeypatch.setattr(ledger_service, "base_currency", lambda: "SGD")
    monkeypatch.setattr(
        ledger_service, "to_base", lambda amt, cur, base=None, rates=None: round(float(amt), 2)
    )

    class _NoStatements:
        def add_batch_id(self, *a, **k):
            pass

    monkeypatch.setattr(ledger_service, "statements", _NoStatements())

    from backend.ai.tools import _shared

    monkeypatch.setattr(_shared.svc, "accounts", acc_store)
    monkeypatch.setattr(_shared.svc, "base_currency", lambda: "SGD")

    acct_a = acc_store.create(
        {"name": "Meridian Bank", "kind": "asset", "type": "cash", "currency": "SGD"}
    )
    acct_b = acc_store.create(
        {"name": "Lighthouse Bank", "kind": "asset", "type": "cash", "currency": "SGD"}
    )
    return acct_a, acct_b, txn_store, batch_store


def test_bulk_add_atomicity_no_partial_batch(sgd_setup):
    """REGRESSION (FIX B): 3 items, middle one has a bad account_id → created=0, no residual txns."""
    from backend.ai.tools.ledger import add_transactions

    acct_a, acct_b, txn_store, batch_store = sgd_setup

    raw = [
        {
            "date": "2026-06-01",
            "description": "Grab",
            "amount": 12.0,
            "currency": "SGD",
            "category": "cat_13095ba70000",
            "account_id": acct_a["id"],
        },
        {
            "date": "2026-06-02",
            "description": "Bad item",
            "amount": 5.0,
            "currency": "SGD",
            "category": "cat_a6b4e3bc0000",
            "account_id": "does-not-exist",  # invalid — will fail pre-validation
        },
        {
            "date": "2026-06-03",
            "description": "Fairprice",
            "amount": 30.0,
            "currency": "SGD",
            "category": "cat_13095ba70000",
            "account_id": acct_b["id"],
        },
    ]
    result_text, proposed = asyncio.run(
        add_transactions({"transactions": raw, "import_description": "Test atomicity"})
    )
    body = json.loads(result_text)

    assert body["ok"] is False
    assert body["created"] == 0, f"Expected 0 created, got {body['created']}"
    assert body["errors"] >= 1
    assert "errors_detail" in body

    # No transactions must exist in the store
    all_txns = [t for t in txn_store.all() if not t.get("deleted")]
    assert len(all_txns) == 0, f"Expected 0 transactions, found {len(all_txns)}: {all_txns}"

    assert proposed is None


def test_bulk_add_atomicity_errors_detail_covers_bad_item(sgd_setup):
    """errors_detail must include the failing item (index 1) with a meaningful error message."""
    from backend.ai.tools.ledger import add_transactions

    acct_a, _, txn_store, _ = sgd_setup

    raw = [
        {
            "date": "2026-06-01",
            "description": "Grab",
            "amount": 12.0,
            "currency": "SGD",
            "category": "cat_13095ba70000",
            "account_id": acct_a["id"],
        },
        {
            "date": "2026-06-02",
            "description": "Missing account",
            "amount": 5.0,
            "currency": "SGD",
            "category": "cat_a6b4e3bc0000",
            "account_id": "ghost-id",
        },
    ]
    result_text, _ = asyncio.run(add_transactions({"transactions": raw}))
    body = json.loads(result_text)

    assert body["ok"] is False
    assert body["created"] == 0
    detail = body["errors_detail"]
    assert any(d["index"] == 1 for d in detail), f"Missing index=1 in errors_detail: {detail}"
    assert any(
        "ghost-id" in d.get("error", "") or "not found" in d.get("error", "").lower()
        for d in detail
    ), f"Expected 'not found' error in detail: {detail}"


def test_bulk_add_all_valid_creates_all(sgd_setup):
    """All items valid → all created, ok=True, no residual batch clutter."""
    from backend.ai.tools.ledger import add_transactions

    acct_a, acct_b, txn_store, _ = sgd_setup

    raw = [
        {
            "date": "2026-06-01",
            "description": "Coffee",
            "amount": 5.0,
            "currency": "SGD",
            "category": "cat_13095ba70000",
            "account_id": acct_a["id"],
        },
        {
            "date": "2026-06-02",
            "description": "Transport",
            "amount": 2.5,
            "currency": "SGD",
            "category": "cat_db5642ab0000",
            "account_id": acct_b["id"],
        },
    ]
    result_text, proposed = asyncio.run(
        add_transactions({"transactions": raw, "import_description": "June 2026"})
    )
    body = json.loads(result_text)

    assert body["ok"] is True
    assert body["created"] == 2
    assert body["errors"] == 0
    all_txns = [t for t in txn_store.all() if not t.get("deleted")]
    assert len(all_txns) == 2
    assert proposed is None


# ── FIX A3: a transfer's figure and its unit belong to the SAME leg ────────────
#
# Root-caused from a repro (2026-08-23). Lucius logged a large VND loan repayment into a
# VND bank from an SGD receivable, in the credit form the model naturally emits:
#
#     amount=420_000_000, currency="VND", direction="credit",
#     account_id=<Riverside Bank, VND>, from_account_id=<Alex Tan's Loan, SGD>
#
# The mapping split that pair across two legs: the NUMBER landed on the source leg
# while the caller's UNIT landed on the destination leg, so the source leg silently
# took its own account's currency. `_normalise_leg` then saw SGD == SGD, skipped FX,
# stamped no original_amount — and `_currency_conversion_warning` (which keys off
# original_amount) never fired. ₫420,000,000 was booked as S$420,000,000 on the
# receivable: a ~20,408x error (the fixture's own 1 SGD ≈ 20,408 VND rate), invisible
# to every existing guard.
#
# The rule these tests pin: `amount`/`currency` describe the SOURCE leg and
# `to_amount`/`to_currency` the DESTINATION leg, in ALL THREE anchor forms. The
# anchor decides only which account is which side. A destination amount that was not
# supplied is FX-derived — never the raw number relabelled.


@pytest.fixture
def sgd_loan_and_vnd_bank(tmp_path, monkeypatch):
    """The incident's own shape: an SGD receivable and a VND bank, SGD base."""
    from backend.db.account_store import AccountStore
    from backend.db.import_batch_store import ImportBatchStore
    from backend.db.transaction_store import TransactionStore

    acc_store = AccountStore()
    acc_store._path = tmp_path / "accounts.json"
    txn_store = TransactionStore()
    txn_store._path = tmp_path / "transactions.json"
    batch_store = ImportBatchStore()
    batch_store._path = tmp_path / "import_batches.json"

    monkeypatch.setattr(ledger_service, "accounts", acc_store)
    monkeypatch.setattr(ledger_service, "transactions", txn_store)
    monkeypatch.setattr(ledger_service, "import_batches", batch_store)
    monkeypatch.setattr(ledger_service, "base_currency", lambda: "SGD")

    # 1 VND = 0.000049 SGD → 1 SGD ≈ 20,408 VND
    fx_rates = {"SGD": 1.0, "VND": 0.000049}

    def _to_base(amt, cur, base=None, rates=None):
        return round(
            float(amt) * fx_rates.get(cur, 1.0) / fx_rates.get((base or "SGD").upper(), 1.0), 2
        )

    monkeypatch.setattr(ledger_service, "to_base", _to_base)

    class _NoStatements:
        def add_batch_id(self, *a, **k):
            pass

    monkeypatch.setattr(ledger_service, "statements", _NoStatements())

    from backend.ai.tools import _shared

    monkeypatch.setattr(_shared.svc, "accounts", acc_store)
    monkeypatch.setattr(_shared.svc, "base_currency", lambda: "SGD")
    monkeypatch.setattr(_shared.svc, "to_base", _to_base)

    loan = acc_store.create(
        {
            "name": "Alex Tan's Loan",
            "kind": "asset",
            "type": "receivable",
            "currency": "SGD",
        }
    )
    bank = acc_store.create(
        {"name": "Riverside Bank", "kind": "asset", "type": "cash", "currency": "VND"}
    )
    return loan, bank, txn_store


def test_credit_form_keeps_the_stated_unit_on_the_stated_amount(sgd_loan_and_vnd_bank):
    """REGRESSION (FIX A3, the incident itself): credit + from_account_id.

    ₫420M out of an SGD receivable must convert to ~S$20,580 — never book as
    S$420,000,000 because the source leg took its account's own currency.
    """
    loan, bank, _ = sgd_loan_and_vnd_bank
    data = _txn_data_from_ai(
        {
            "description": "Alex Tan loan repayment (tranche 1 of 2)",
            "amount": 420_000_000,
            "currency": "VND",
            "direction": "credit",
            "account_id": bank["id"],
            "from_account_id": loan["id"],
        },
        default_date="2026-08-21",
    )
    assert data["kind"] == "transfer"
    assert data["from_account_id"] == loan["id"]
    assert data["to_account_id"] == bank["id"]
    # The pair the caller stated stays together, on the leg it describes.
    assert data["from_amount"] == 420_000_000.0
    assert data["from_currency"] == "VND"
    assert data["to_amount"] == 420_000_000.0
    assert data["to_currency"] == "VND"

    row = ledger_service.create_transaction(dict(data))
    # The SGD receivable is credited the CONVERTED value, not the raw VND number.
    assert row["from_currency"] == "SGD"
    assert row["from_amount"] == pytest.approx(20_580.0, rel=0.02), (
        f"source leg booked as {row['from_amount']} {row['from_currency']} "
        "— the VND figure was relabelled SGD instead of converted"
    )
    assert row["to_amount"] == 420_000_000.0
    assert row["to_currency"] == "VND"


def test_credit_form_via_to_account_id_keeps_the_pair_together(sgd_loan_and_vnd_bank):
    """The sibling branch (direction='credit' + to_account_id as the SOURCE) carried
    the identical defect and gets the identical guarantee."""
    loan, bank, _ = sgd_loan_and_vnd_bank
    data = _txn_data_from_ai(
        {
            "description": "loan repayment, credit form",
            "amount": 180_000_000,
            "currency": "VND",
            "direction": "credit",
            "account_id": bank["id"],
            "to_account_id": loan["id"],  # counterparty = SOURCE under direction=credit
        },
        default_date="2026-08-21",
    )
    assert data["from_account_id"] == loan["id"]
    assert data["from_currency"] == "VND"
    assert data["from_amount"] == 180_000_000.0

    row = ledger_service.create_transaction(dict(data))
    assert row["from_currency"] == "SGD"
    assert row["from_amount"] == pytest.approx(8_820.0, rel=0.02)


def test_missing_destination_amount_is_converted_not_relabelled(sgd_loan_and_vnd_bank):
    """The mirror hazard: debit form, cross-currency, `to_amount` omitted.

    Defaulting the destination leg to the source NUMBER while stamping the
    destination ACCOUNT's currency invents an unconverted figure — S$14,568.76
    arriving as ₫14,568.76 instead of ~₫300M.
    """
    loan, bank, _ = sgd_loan_and_vnd_bank
    data = _txn_data_from_ai(
        {
            "description": "repayment, destination amount omitted",
            "amount": 14_568.76,
            "currency": "SGD",
            "direction": "debit",
            "account_id": loan["id"],
            "to_account_id": bank["id"],
            # to_amount deliberately omitted
        },
        default_date="2026-08-21",
    )
    assert data["from_amount"] == 14_568.76
    assert data["from_currency"] == "SGD"
    assert data["to_currency"] == "VND"
    assert data["to_amount"] == pytest.approx(297_320_000.0, rel=0.02), (
        f"destination leg defaulted to {data['to_amount']} VND — the SGD number was "
        "relabelled VND instead of converted"
    )


def test_same_currency_default_still_copies_the_number(sgd_loan_and_vnd_bank):
    """The default is only a copy when both legs share a currency — the case it was
    written for. No FX call, no drift, exact equality."""
    loan, _, _ = sgd_loan_and_vnd_bank
    from backend.ai.tools import _shared

    second_sgd = _shared.svc.accounts.create(
        {"name": "Meridian Bank Multiplier", "kind": "asset", "type": "cash", "currency": "SGD"}
    )
    data = _txn_data_from_ai(
        {
            "description": "same-currency transfer",
            "amount": 2_500,
            "currency": "SGD",
            "direction": "debit",
            "account_id": loan["id"],
            "to_account_id": second_sgd["id"],
        },
        default_date="2026-08-21",
    )
    assert data["from_amount"] == 2_500.0
    assert data["to_amount"] == 2_500.0
    assert data["from_currency"] == data["to_currency"] == "SGD"


def test_to_currency_lands_on_the_destination_leg_in_every_form(sgd_loan_and_vnd_bank):
    """`to_currency` means the DESTINATION leg — its documented meaning. Two of the
    three branches used to apply it to the SOURCE leg instead."""
    loan, bank, _ = sgd_loan_and_vnd_bank
    common = {
        "description": "explicit to_currency",
        "amount": 100,
        "currency": "SGD",
        "to_amount": 2_040_816,
        "to_currency": "VND",
    }
    debit_form = _txn_data_from_ai(
        {**common, "direction": "debit", "account_id": loan["id"], "to_account_id": bank["id"]},
        default_date="2026-08-21",
    )
    credit_form = _txn_data_from_ai(
        {**common, "direction": "credit", "account_id": bank["id"], "from_account_id": loan["id"]},
        default_date="2026-08-21",
    )
    for form, label in ((debit_form, "debit"), (credit_form, "credit")):
        assert form["from_account_id"] == loan["id"], label
        assert form["to_account_id"] == bank["id"], label
        assert form["from_currency"] == "SGD", label
        assert form["to_currency"] == "VND", label
        assert form["from_amount"] == 100.0, label
        assert form["to_amount"] == 2_040_816.0, label


@pytest.mark.parametrize(
    "anchor_form",
    [
        {"direction": "credit", "anchor": "bank", "counterparty_key": "from_account_id"},
        {"direction": "credit", "anchor": "bank", "counterparty_key": "to_account_id"},
        {"direction": "debit", "anchor": "loan", "counterparty_key": "to_account_id"},
    ],
)
def test_the_stated_pair_is_never_split_across_legs(sgd_loan_and_vnd_bank, anchor_form):
    """The invariant, stated once over all three anchor forms: whichever leg carries
    the caller's `amount` must also carry the caller's `currency`. Splitting them is
    what made ₫420,000,000 into S$420,000,000."""
    loan, bank, _ = sgd_loan_and_vnd_bank
    accounts = {"loan": loan["id"], "bank": bank["id"]}
    anchor = accounts[anchor_form["anchor"]]
    counterparty = accounts["loan" if anchor_form["anchor"] == "bank" else "bank"]
    data = _txn_data_from_ai(
        {
            "description": "pair integrity",
            "amount": 1_234,
            "currency": "VND",
            "direction": anchor_form["direction"],
            "account_id": anchor,
            anchor_form["counterparty_key"]: counterparty,
        },
        default_date="2026-08-21",
    )
    stated_leg = "from" if data["from_amount"] == 1_234.0 else "to"
    assert data[f"{stated_leg}_amount"] == 1_234.0
    assert data[f"{stated_leg}_currency"] == "VND", (
        f"the {stated_leg} leg holds the stated amount but not the stated currency "
        f"({data[f'{stated_leg}_currency']!r}) — the pair was split"
    )


# ── FIX A3b: a leg the app COMPUTED says so ───────────────────────────────────
#
# The other half of FIX A3. Once a missing destination amount is FX-derived rather than
# relabelled, the stored figure is the app's arithmetic at TODAY's rate — not the rate that
# actually posted. `_currency_conversion_warning` structurally cannot report it: it reads
# `original_amount`, and `ledger._resolve_original` returns (None, None) for kind='transfer'
# by design (a transfer has no single "original" charge). So the derivation had no surface
# at all until `_txn_data_from_ai`'s `derived_out` out-param.
#
# It matters where a transfer is meant to settle a balance to zero — a receivable paid in two
# tranches at today's rate does not sum to the amount actually owed, and the account ends at
# "$2.40 still owed": wrong in a way nobody goes looking for.


def test_a_derived_destination_leg_is_reported(sgd_loan_and_vnd_bank):
    loan, bank, _ = sgd_loan_and_vnd_bank
    derived: dict = {}
    _txn_data_from_ai(
        {
            "description": "repayment, destination amount omitted",
            "amount": 14_568.76,
            "currency": "SGD",
            "direction": "debit",
            "account_id": loan["id"],
            "to_account_id": bank["id"],
        },
        default_date="2026-08-21",
        derived_out=derived,
    )
    assert derived["source_amount"] == 14_568.76
    assert derived["source_currency"] == "SGD"
    assert derived["derived_currency"] == "VND"
    assert derived["derived_amount"] == pytest.approx(297_320_000.0, rel=0.02)


def test_a_same_currency_default_is_not_reported_as_derived(sgd_loan_and_vnd_bank):
    """Copying the caller's own number across two same-currency legs is not a derivation —
    warning about it would be noise on every ordinary transfer."""
    loan, _, _ = sgd_loan_and_vnd_bank
    from backend.ai.tools import _shared

    second_sgd = _shared.svc.accounts.create(
        {"name": "Meridian Bank Multiplier", "kind": "asset", "type": "cash", "currency": "SGD"}
    )
    derived: dict = {}
    _txn_data_from_ai(
        {
            "description": "same-currency transfer",
            "amount": 2_500,
            "currency": "SGD",
            "direction": "debit",
            "account_id": loan["id"],
            "to_account_id": second_sgd["id"],
        },
        default_date="2026-08-21",
        derived_out=derived,
    )
    assert derived == {}


def test_an_explicit_destination_amount_is_not_reported_as_derived(sgd_loan_and_vnd_bank):
    """The caller stated it — nothing was computed, so there is nothing to warn about."""
    loan, bank, _ = sgd_loan_and_vnd_bank
    derived: dict = {}
    _txn_data_from_ai(
        {
            "description": "both legs stated",
            "amount": 14_568.76,
            "currency": "SGD",
            "direction": "debit",
            "account_id": loan["id"],
            "to_account_id": bank["id"],
            "to_amount": 420_000_000,
            "to_currency": "VND",
        },
        default_date="2026-08-21",
        derived_out=derived,
    )
    assert derived == {}


def test_the_derived_warning_names_the_rate_and_the_fix():
    """The warning the tool result carries. Built from the out-param alone, so it is
    testable without a write."""
    from backend.ai.tools.ledger import _derived_leg_warning

    warning = _derived_leg_warning(
        {
            "source_amount": 100.0,
            "source_currency": "SGD",
            "derived_amount": 2_040_816.0,
            "derived_currency": "VND",
        }
    )
    assert warning is not None
    assert warning["type"] == "leg_amount_derived"
    msg = warning["message"]
    assert "2,040,816" in msg
    assert "20,408" in msg, "the implied rate should be spelled out"
    assert "to_amount" in msg, "the message must name the field that pins it exactly"
    assert _derived_leg_warning({}) is None
    assert _derived_leg_warning(None) is None


# ── the unverified-pair warning ───────────────────────────────────────────────
#
# `services.ledger._assert_transfer_amount_converted` (tests/test_transfer_rate_sanity.py)
# refuses an equal-amount cross-currency transfer outright, but ONLY when the pair has a
# KNOWN, non-1:1 rate — an unfamiliar or crypto pair has no rate to judge it against and
# is booked as stated. `_unverified_equal_amount_warning` is the non-blocking half: it
# cannot refuse (an unknown pair might genuinely be 1:1), so it surfaces the same "did you
# forget to convert?" question as a warning instead of silence.


def test_equal_amount_on_an_unknown_pair_is_warned_about(monkeypatch):
    from backend.ai.tools.ledger import _unverified_equal_amount_warning
    from backend.core import fx as core_fx

    monkeypatch.setattr(core_fx, "_fx_rates_loader", lambda: {"SGD": 1.0})
    warning = _unverified_equal_amount_warning(
        {
            "kind": "transfer",
            "from_amount": 100.0,
            "from_currency": "USD",
            "to_amount": 100.0,
            "to_currency": "USDC",
        }
    )
    assert warning is not None
    assert warning["type"] == "unverified_equal_amount_transfer"
    msg = warning["message"]
    assert "100.00" in msg
    assert "USD" in msg and "USDC" in msg
    assert "no known exchange rate" in msg


def test_equal_amount_on_a_known_pair_is_not_double_warned(monkeypatch):
    """A known, non-1:1 pair already went through the hard refusal above — this warning
    would be redundant noise on top of an outright rejection, so it stays quiet."""
    from backend.ai.tools.ledger import _unverified_equal_amount_warning
    from backend.core import fx as core_fx

    monkeypatch.setattr(core_fx, "_fx_rates_loader", lambda: {"SGD": 1.0, "USD": 1.35})
    warning = _unverified_equal_amount_warning(
        {
            "kind": "transfer",
            "from_amount": 100.0,
            "from_currency": "USD",
            "to_amount": 100.0,
            "to_currency": "SGD",
        }
    )
    assert warning is None


def test_differing_amounts_on_an_unknown_pair_are_not_warned_about(monkeypatch):
    from backend.ai.tools.ledger import _unverified_equal_amount_warning
    from backend.core import fx as core_fx

    monkeypatch.setattr(core_fx, "_fx_rates_loader", lambda: {"SGD": 1.0})
    warning = _unverified_equal_amount_warning(
        {
            "kind": "transfer",
            "from_amount": 100.0,
            "from_currency": "USD",
            "to_amount": 99.5,
            "to_currency": "USDC",
        }
    )
    assert warning is None


def test_a_non_transfer_is_never_warned_about(monkeypatch):
    from backend.ai.tools.ledger import _unverified_equal_amount_warning

    assert (
        _unverified_equal_amount_warning(
            {"kind": "expense", "from_amount": 100.0, "from_currency": "USD"}
        )
        is None
    )
