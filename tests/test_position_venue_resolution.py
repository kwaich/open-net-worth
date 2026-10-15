"""A holding's `account_name` resolves like every other account ref (synthetic data only).

Persona QA 2026-10-14: Lucius created the investment account "IBKR USD", then `add_positions`
named the venue "IBKR" — and the holdings landed in "IBKR USD", with nothing said. The lookup
matched the NAME or the INSTITUTION exactly and took the first row, so two accounts under one
institution were a coin flip. CLAUDE.md: an account name that matches several accounts RAISES,
naming every candidate.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from backend.ai.tools import execute_tool
from backend.core.errors import ValidationError
from backend.db.account_store import AccountStore
from backend.db.institution_store import InstitutionStore
from backend.db.instrument_store import InstrumentStore
from backend.db.position_store import PositionStore
from backend.services import positions as ps


@pytest.fixture
def isolated(db_isolated):
    return db_isolated


def _account(name, institution="IBKR", **extra):
    institution_id = InstitutionStore().resolve_or_create(institution)["id"]
    return AccountStore().create(
        {
            "name": name,
            "type": "investment",
            "currency": "USD",
            "institution_id": institution_id,
            **extra,
        }
    )


def _instrument(symbol="VTI"):
    return InstrumentStore().create(
        {"symbol": symbol, "price_source": "manual", "quote_currency": "USD", "currency": "USD"}
    )


def test_a_name_matching_one_institution_resolves_to_that_account(isolated):
    usd = _account("IBKR USD")
    assert ps.find_account(account_name="IBKR")["id"] == usd["id"]


def test_an_exact_name_beats_the_institution_of_another_account(isolated):
    _account("IBKR USD")
    ibkr = _account("IBKR")
    assert ps.find_account(account_name="ibkr")["id"] == ibkr["id"]


def test_two_accounts_under_one_institution_are_ambiguous_not_first_wins(isolated):
    _account("IBKR USD")
    _account("IBKR SGD", currency="SGD")
    with pytest.raises(ValidationError) as err:
        ps.find_account(account_name="IBKR")
    assert "IBKR USD" in str(err.value)
    assert "IBKR SGD" in str(err.value)


def test_an_archived_account_never_makes_an_institution_ambiguous(isolated):
    live = _account("IBKR USD")
    _account("IBKR old", archived=True)
    assert ps.find_account(account_name="IBKR")["id"] == live["id"]


def test_an_unknown_name_still_matches_nothing(isolated):
    _account("IBKR USD")
    assert ps.find_account(account_name="IBK") is None
    assert ps.find_account(account_name="IBKR US") is None


def test_add_positions_by_broker_name_lands_in_the_existing_account(isolated):
    usd = _account("IBKR USD")
    inst = _instrument()
    body, _ = asyncio.run(
        execute_tool(
            "add_positions",
            {"instrument_id": inst["id"], "account_name": "IBKR", "quantity": 3},
        )
    )
    assert "error" not in json.loads(body), body
    [position] = PositionStore().all()
    assert position["account_id"] == usd["id"]
    assert len(AccountStore().all()) == 1


def test_add_positions_refuses_an_ambiguous_broker_name(isolated):
    _account("IBKR USD")
    _account("IBKR SGD", currency="SGD")
    inst = _instrument()
    with pytest.raises(ValidationError, match="ambiguous") as err:
        asyncio.run(
            execute_tool(
                "add_positions",
                {"instrument_id": inst["id"], "account_name": "IBKR", "quantity": 3},
            )
        )
    assert "IBKR SGD" in str(err.value)
    assert PositionStore().all() == []
    assert len(AccountStore().all()) == 2


def test_the_confirm_card_names_the_account_the_holding_will_land_in(isolated):
    from backend.ai.tools.portfolio import _build_position_add_change

    usd = _account("IBKR USD")
    inst = _instrument()
    change, err = _build_position_add_change(
        {"instrument_id": inst["id"], "account_name": "IBKR", "quantity": 3}
    )
    assert err is None
    assert change["account_id"] == usd["id"]
    assert change["display"]["venue"] == "IBKR USD"
