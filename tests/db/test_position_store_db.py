"""DB-backed CRUD + money-exactness tests for PositionStore (plan 4b, task 2)."""

from __future__ import annotations

import pytest
from sqlalchemy import select

from backend.db.position_store import PositionStore, vesting_state_of
from backend.db.tables import DEFAULT_BOOK_ID, positions


def test_create_and_find_round_trip(db_engine):
    s = PositionStore(engine=db_engine)
    pos = s.create(
        {"instrument_id": "i1", "account_id": "a1", "quantity": "5", "cost_basis": "12.5"}
    )
    assert pos["id"] and pos["created_at"] and pos["updated_at"]
    found = s.find_by_id(pos["id"])
    assert found["instrument_id"] == "i1"
    assert found["quantity"] == 5.0
    assert found["cost_basis"] == 12.5


def test_writes_default_book_id(db_engine):
    s = PositionStore(engine=db_engine)
    s.create({"instrument_id": "i1", "account_id": "a1", "quantity": 5})
    with db_engine.connect() as conn:
        assert conn.execute(select(positions.c.book_id)).scalar_one() == DEFAULT_BOOK_ID


def test_query_positions_filters_and_instrument_ids(db_engine):
    """query_positions/instrument_ids were rewritten from self._load_items() to self.all()
    — proves the filter behavior is unchanged post-rewrite."""
    s = PositionStore(engine=db_engine)
    s.create(
        {"instrument_id": "btc", "account_id": "binance", "quantity": 1, "vesting_state": "vested"}
    )
    s.create(
        {"instrument_id": "btc", "account_id": "gemini", "quantity": 2, "vesting_state": "vested"}
    )
    s.create(
        {"instrument_id": "rsu1", "account_id": "eq", "quantity": 3, "vesting_state": "unvested"}
    )

    assert len(s.query_positions(instrument_id="btc")) == 2
    assert len(s.query_positions(account_id="gemini")) == 1
    assert len(s.query_positions(vesting_state="unvested")) == 1
    assert set(s.instrument_ids()) == {"btc", "rsu1"}


def test_touch_refuses_the_derived_figures(db_engine):
    from backend.core.errors import DataIntegrityError

    s = PositionStore(engine=db_engine)
    pos = s.create({"instrument_id": "i1", "account_id": "a1", "quantity": 1})
    with pytest.raises(DataIntegrityError, match="derived"):
        s.touch(pos["id"], {"quantity": "9"})
    assert s.touch(pos["id"], {"note": "x"})["note"] == "x"


def test_write_figures_bumps_updated_at_and_coerces_numerics(db_engine):
    s = PositionStore(engine=db_engine)
    pos = s.create({"instrument_id": "i1", "account_id": "a1", "quantity": 1})
    updated = s.write_figures(pos["id"], {"quantity": "9", "cost_basis": "3.5"})
    assert updated["quantity"] == 9.0
    assert updated["cost_basis"] == 3.5
    assert updated["updated_at"] >= pos["updated_at"]


def test_legacy_bucket_maps_to_vesting_state(db_engine):
    """vesting_state_of tolerates rows written before the split (contingent -> vested)."""
    s = PositionStore(engine=db_engine)
    pos = s.create({"instrument_id": "i1", "account_id": "a1", "quantity": 1, "bucket": "locked"})
    assert pos["vesting_state"] == "unvested"
    assert vesting_state_of({"bucket": "contingent"}) == "vested"


def test_cost_basis_and_quantity_round_trip_exactly(db_engine):
    """cost_basis/quantity live in payload — exact via JSON round-trip; no promoted
    money column."""
    s = PositionStore(engine=db_engine)
    tricky = 0.1 + 0.2
    pos = s.create(
        {"instrument_id": "i1", "account_id": "a1", "quantity": tricky, "cost_basis": tricky}
    )
    back = s.find_by_id(pos["id"])
    assert back["quantity"] == tricky
    assert back["cost_basis"] == tricky
