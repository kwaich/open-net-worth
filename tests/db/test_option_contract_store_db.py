"""DB-backed CRUD + money-exactness tests for OptionContractStore (plan 4b, task 2)."""

from __future__ import annotations

from sqlalchemy import select

from backend.db.option_contract_store import OptionContractStore
from backend.db.tables import DEFAULT_BOOK_ID, option_contracts


def _contract(**extra):
    return {
        "account_id": "a1",
        "underlying_instrument_id": "i1",
        "option_type": "call",
        "side": "short",
        "strike": 100.0,
        "expiry": "2026-12-18",
        "contracts": 2,
        "premium_per_share": 1.5,
        "premium_currency": "usd",
        **extra,
    }


def test_create_contract_and_find_round_trip(db_engine):
    s = OptionContractStore(engine=db_engine)
    c = s.create_contract(_contract())
    assert c["id"] and c["created_at"] and c["updated_at"]
    assert c["premium_currency"] == "USD"  # single coercion authority upper-cases
    assert s.find_by_id(c["id"])["strike"] == 100.0


def test_writes_default_book_id(db_engine):
    s = OptionContractStore(engine=db_engine)
    c = s.create_contract(_contract())
    with db_engine.connect() as conn:
        row = conn.execute(select(option_contracts).where(option_contracts.c.id == c["id"])).first()
    assert row.book_id == DEFAULT_BOOK_ID


def test_invalid_enums_fall_back_to_defaults(db_engine):
    s = OptionContractStore(engine=db_engine)
    c = s.create_contract(_contract(option_type="bogus", side="bogus", status="bogus"))
    assert c["option_type"] == "call"
    assert c["side"] == "long"
    assert c["status"] == "open"


def test_query_contracts_filters(db_engine):
    """query_contracts/find_by_idempotency_key were rewritten from self._load_items() to
    self.all() — proves the filter behavior is unchanged post-rewrite."""
    s = OptionContractStore(engine=db_engine)
    open_c = s.create_contract(
        _contract(account_id="a1", underlying_instrument_id="i1", status="open")
    )
    s.create_contract(_contract(account_id="a2", underlying_instrument_id="i2", status="closed"))
    s.create_contract(_contract(account_id="a1", underlying_instrument_id="i2", status="open"))

    assert len(s.query_contracts(account_id="a1")) == 2
    assert len(s.query_contracts(status="closed")) == 1
    assert [c["id"] for c in s.query_contracts(underlying_instrument_id="i1")] == [open_c["id"]]


def test_update_contract_patches_and_persists(db_engine):
    s = OptionContractStore(engine=db_engine)
    c = s.create_contract(_contract())
    updated = s.update_contract(c["id"], {"status": "closed"})
    assert updated["status"] == "closed"
    assert updated["updated_at"] >= c["updated_at"]
    assert s.find_by_id(c["id"])["status"] == "closed"
    assert s.update_contract("nonexistent", {"status": "x"}) is None


def test_find_by_idempotency_key(db_engine):
    s = OptionContractStore(engine=db_engine)
    s.create_contract(_contract(idempotency_key="k-1"))
    assert s.find_by_idempotency_key("k-1")["strike"] == 100.0
    assert s.find_by_idempotency_key("") is None


def test_strike_premium_and_collateral_round_trip_exactly(db_engine):
    """money in payload (strike/premium/collateral) — exact via JSON round-trip."""
    s = OptionContractStore(engine=db_engine)
    tricky = 0.1 + 0.2
    c = s.create_contract(
        _contract(
            strike=tricky,
            premium_per_share=tricky,
            collateral_amount=tricky,
            close_premium_per_share=tricky,
        )
    )
    back = s.find_by_id(c["id"])
    assert back["strike"] == tricky
    assert back["premium_per_share"] == tricky
    assert back["collateral_amount"] == tricky
    assert back["close_premium_per_share"] == tricky
