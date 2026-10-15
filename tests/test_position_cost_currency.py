"""Onboarding preserves original basis separately from the market quote currency."""

import asyncio
import json

import pytest

from backend.ai.tools import execute_tool
from backend.core.errors import ValidationError
from backend.db.account_store import AccountStore
from backend.db.instrument_store import InstrumentStore
from backend.db.lot_store import LotStore
from backend.db.position_store import PositionStore
from backend.services import positions


@pytest.fixture
def holding(db_isolated, monkeypatch):
    monkeypatch.setattr(positions, "_trigger_backfill_async", lambda _: None)
    instrument = InstrumentStore().create(
        {"symbol": "BTC", "asset_class": "crypto", "quote_currency": "USD"}
    )
    account = AccountStore().create(
        {"name": "Test custodian", "type": "investment", "currency": "USD"}
    )
    return {
        "instrument_id": instrument["id"],
        "account_id": account["id"],
        "quantity": 0.04,
        "cost_basis": 80000,
        "cost_currency": "sgd",
    }


def call(name, args):
    text, _ = asyncio.run(execute_tool(name, args))
    return json.loads(text)


def test_add_and_update_tool_preserve_native_cost(holding):
    result = call("add_positions", {"adds": [holding]})  # one item → the single call
    pid = result["position_id"]
    row = PositionStore().find_by_id(pid)
    assert (row["cost_basis"], row["cost_currency"]) == (80000, "SGD")
    # The basis is derived from the lot (ADR 2026-10-07) — an edit is refused, nothing moves.
    # A one-item `updates` is the single call, which lets the refusal raise (the assistant
    # turns it into the tool's error result) — exactly as update_positions(position_id=…) does.
    with pytest.raises(ValidationError, match="derived from its records"):
        call(
            "update_positions",
            {
                "updates": [
                    {"position_id": pid, "changes": {"cost_basis": 81000, "cost_currency": "eur"}}
                ]
            },
        )
    row = PositionStore().find_by_id(pid)
    assert (row["cost_basis"], row["cost_currency"]) == (80000, "SGD")


def test_preview_discloses_cost_currency(holding):
    from backend.ai.tools.portfolio import _build_position_add_change, _build_position_update_change

    preview, error = _build_position_add_change(holding)
    assert error is None
    assert preview["cost_currency"] == "SGD"
    assert preview["display"]["cost_currency"] == "SGD"
    pid = positions.apply_position_change({"type": "add", **holding})["position_id"]
    preview, error = _build_position_update_change(
        {"position_id": pid, "changes": {"cost_currency": "EUR"}}
    )
    assert preview is None
    assert "derived from its records" in error  # refused before a card is ever staged


def test_auto_lot_keeps_supplied_currency(holding, monkeypatch):
    from backend.services import lots

    monkeypatch.setattr(lots, "_load_rates", lambda: {"USD": 1.0, "SGD": 0.8})
    monkeypatch.setattr(lots, "base_currency", lambda: "USD")
    result = positions.apply_position_change(
        {"type": "add", **holding, "acquired_at": "2026-01-02"}
    )
    lot = LotStore().find_by_id(result["lot_id"])
    assert lot["cost_currency"] == "SGD"
    assert lot["unit_cost"] == 80000
    with pytest.raises(ValidationError, match="derived from its records"):
        positions.update_position(result["position_id"], {"cost_currency": "EUR"})


@pytest.mark.parametrize("currency", ["", "S$", "INVALID", 42])
def test_invalid_explicit_currency_is_refused_without_position(holding, currency):
    with pytest.raises(ValidationError, match="cost_currency"):
        positions.apply_position_change({"type": "add", **holding, "cost_currency": currency})
    assert PositionStore().all() == []


def test_per_unit_display_refuses_missing_fx():
    from backend.core.errors import MissingFxRateError
    from backend.services.portfolio_rows import _unit_convert

    with pytest.raises(MissingFxRateError):
        _unit_convert(80000, "SGD", "USD", {"USD": 1.0})
    assert _unit_convert(0.0000044, "USD", "SGD", {"USD": 1.0, "SGD": 0.8}) == pytest.approx(
        0.0000055
    )


def test_grant_managed_cost_currency_is_protected(holding, monkeypatch):
    pid = positions.apply_position_change({"type": "add", **holding})["position_id"]
    monkeypatch.setattr(positions, "_grant_id_for_position", lambda _: "grant_test")
    with pytest.raises(ValidationError, match="managed by equity grant"):
        positions.update_position(pid, {"cost_currency": "EUR"})


@pytest.mark.parametrize(
    "overrides",
    [
        {"quantity": 0},
        {"fees": "bad"},
        {"cost_basis": "bad"},
        {"acquired_at": "not-a-date"},
    ],
)
def test_bad_acquisition_does_not_create_venue(holding, overrides):
    change = {
        **holding,
        "type": "add",
        "account_name": "New venue",
        "acquired_at": "2026-01-02",
        **overrides,
    }
    change.pop("account_id")
    with pytest.raises(ValidationError):
        positions.apply_position_change(change)
    assert len(AccountStore().all()) == 1
    assert PositionStore().all() == []


def test_missing_acquisition_fx_does_not_create_venue(holding, monkeypatch):
    from backend.core.errors import MissingFxRateError
    from backend.services import lots

    monkeypatch.setattr(lots, "base_currency", lambda: "USD")
    monkeypatch.setattr(lots, "_load_rates", lambda: {"USD": 1.0})
    change = {**holding, "type": "add", "account_name": "New venue", "acquired_at": "2026-01-02"}
    change.pop("account_id")
    with pytest.raises(MissingFxRateError):
        positions.apply_position_change(change)
    assert len(AccountStore().all()) == 1
    assert PositionStore().all() == []


def test_preserved_basis_and_quote_are_valued_in_their_own_currencies(holding):
    from backend.services.portfolio_rows import value_row

    pid = positions.apply_position_change({"type": "add", **holding})["position_id"]
    row = positions.hydrated_one(pid)
    row["price"] = 100000
    valued = value_row(row, "SGD", {"SGD": 1.0, "USD": 1.25})
    assert valued["base_value"] == 5000
    assert valued["cost_base"] == 3200
    assert valued["pl"] == 1800


@pytest.mark.parametrize("cost", [None, 0])
def test_unknown_acquisition_cost_does_not_require_fx(holding, monkeypatch, cost):
    from backend.services import lots

    monkeypatch.setattr(lots, "base_currency", lambda: "USD")
    monkeypatch.setattr(lots, "_load_rates", lambda: {"USD": 1.0})
    result = positions.apply_position_change(
        {"type": "add", **holding, "cost_basis": cost, "acquired_at": "2026-01-02"}
    )
    assert result["ok"]
    assert LotStore().find_by_id(result["lot_id"])["unit_cost"] is None


@pytest.mark.parametrize("protected", ["invalid_currency", "grant", "lot"])
def test_refused_update_does_not_create_named_venue(holding, monkeypatch, protected):
    pid = positions.apply_position_change({"type": "add", **holding})["position_id"]
    if protected == "grant":
        monkeypatch.setattr(positions, "_grant_id_for_position", lambda _: "grant_test")
    elif protected == "lot":
        LotStore().create(
            {
                "position_id": pid,
                "instrument_id": holding["instrument_id"],
                "account_id": holding["account_id"],
                "original_qty": 0.04,
                "remaining_qty": 0.04,
                "cost_currency": "SGD",
            }
        )
    with pytest.raises(ValidationError):
        positions.apply_position_change(
            {
                "type": "update",
                "position_id": pid,
                "changes": {
                    "account_name": "Unwanted venue",
                    "cost_currency": "not-a-currency" if protected == "invalid_currency" else "EUR",
                },
            }
        )
    assert len(AccountStore().all()) == 1
    assert PositionStore().find_by_id(pid)["account_id"] == holding["account_id"]
