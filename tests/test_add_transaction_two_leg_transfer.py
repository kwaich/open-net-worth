"""REGRESSION: the AI ``add_transaction`` contract is anchor-based (``account_id`` +
direction, with ``from_account_id``/``to_account_id`` as counterparty modifiers), but a
model naturally emits the REST-style symmetric pair for a conversational transfer
("move 5000 from Vantage Brokerage to Meridian Bank") — from_account_id + to_account_id, NO account_id.
That call used to die at ``_validate_account_id`` with a misleading
"account_id is required. Call list_accounts…" even though both IDs were valid.

The mapping helper now infers the anchor from the pair (source leg = anchor), and a
single-leg call with no anchor gets a transfer-specific actionable error.
"""

import asyncio
import json

import pytest

from backend.ai.tools._shared import _txn_data_from_ai
from backend.services import ledger as ledger_service


@pytest.fixture
def two_sgd_accounts(tmp_path, monkeypatch):
    """Two SGD accounts (src, dst), SGD base, isolated stores."""
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

    src = acc_store.create(
        {"name": "Vantage Brokerage", "kind": "asset", "type": "investment", "currency": "SGD"}
    )
    dst = acc_store.create(
        {"name": "Meridian Bank Multiplier", "kind": "asset", "type": "cash", "currency": "SGD"}
    )
    return src, dst, txn_store


def test_two_leg_pair_without_anchor_maps_to_transfer(two_sgd_accounts):
    src, dst, _ = two_sgd_accounts
    data = _txn_data_from_ai(
        {
            "description": "Transfer from Vantage Brokerage to Meridian Bank Multiplier",
            "amount": 5000,
            "currency": "SGD",
            "from_account_id": src["id"],
            "to_account_id": dst["id"],
            "category": "",
        },
        default_date="2026-07-15",
    )
    assert data["kind"] == "transfer"
    assert data["from_account_id"] == src["id"]
    assert data["to_account_id"] == dst["id"]
    assert data["from_amount"] == 5000
    assert data["to_amount"] == 5000
    assert data["from_currency"] == "SGD"


def test_two_leg_pair_without_anchor_ignores_direction(two_sgd_accounts):
    """With an explicit from→to pair, the pair is authoritative — direction must not swap legs."""
    src, dst, _ = two_sgd_accounts
    data = _txn_data_from_ai(
        {
            "description": "top-up",
            "amount": 100,
            "currency": "SGD",
            "direction": "credit",
            "from_account_id": src["id"],
            "to_account_id": dst["id"],
        },
        default_date="2026-07-15",
    )
    assert data["from_account_id"] == src["id"]
    assert data["to_account_id"] == dst["id"]


def test_ai_transaction_input_refuses_negative_amount(two_sgd_accounts):
    """The tool must not erase a malformed sign before the ledger choke sees it."""
    src, _, _ = two_sgd_accounts

    with pytest.raises(ValueError, match="amount must be zero or greater"):
        _txn_data_from_ai(
            {
                "description": "Malformed signed amount",
                "amount": -20,
                "currency": "SGD",
                "account_id": src["id"],
            },
            default_date="2026-07-15",
        )


def test_two_leg_pair_via_add_transaction_tool_creates_transfer(two_sgd_accounts):
    from backend.ai.tools.ledger import add_transactions

    src, dst, txn_store = two_sgd_accounts
    text, proposed = asyncio.run(
        add_transactions(
            {
                "description": "Transfer from Vantage Brokerage to Meridian Bank Multiplier",
                "amount": 5000,
                "currency": "SGD",
                "from_account_id": src["id"],
                "to_account_id": dst["id"],
                "category": "",
            }
        )
    )
    body = json.loads(text)
    assert body.get("ok") is True, body
    assert proposed is None
    rows = [t for t in txn_store.all() if not t.get("deleted")]
    assert len(rows) == 1
    assert rows[0]["kind"] == "transfer"
    assert rows[0]["from_account_id"] == src["id"]
    assert rows[0]["to_account_id"] == dst["id"]


def test_anchor_equal_to_from_is_not_a_degenerate_transfer(two_sgd_accounts):
    """account_id == from_account_id + to_account_id must anchor on the source, not
    build a same-account transfer (which the create choke point would refuse)."""
    src, dst, _ = two_sgd_accounts
    data = _txn_data_from_ai(
        {
            "description": "belt and braces",
            "amount": 250,
            "currency": "SGD",
            "account_id": src["id"],
            "from_account_id": src["id"],
            "to_account_id": dst["id"],
        },
        default_date="2026-07-15",
    )
    assert data["from_account_id"] == src["id"]
    assert data["to_account_id"] == dst["id"]


def test_single_leg_without_anchor_gets_transfer_specific_error(two_sgd_accounts):
    src, _, _ = two_sgd_accounts
    with pytest.raises(ValueError, match="from_account_id"):
        _txn_data_from_ai(
            {
                "description": "half a transfer",
                "amount": 10,
                "currency": "SGD",
                "from_account_id": src["id"],
            },
            default_date="2026-07-15",
        )


def test_anchor_plus_to_account_still_works_unchanged(two_sgd_accounts):
    """The original statement-line form (account_id + to_account_id) is untouched."""
    src, dst, _ = two_sgd_accounts
    data = _txn_data_from_ai(
        {
            "description": "classic form",
            "amount": 75,
            "currency": "SGD",
            "account_id": src["id"],
            "to_account_id": dst["id"],
        },
        default_date="2026-07-15",
    )
    assert data["kind"] == "transfer"
    assert data["from_account_id"] == src["id"]
    assert data["to_account_id"] == dst["id"]
