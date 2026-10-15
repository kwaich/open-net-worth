"""``update_lots`` — Lucius corrects a recorded lot (synthetic data only).

A position's quantity and cost basis are derived from its lots (ADR
2026-10-07-a-position-quantity-is-derived-from-its-records), so a wrong holding is fixed on
the LOT. The headline case: a holding added with no purchase date became an opening balance
dated the day it was added, and the user later says when they really bought it.
"""

# ruff: noqa: F811  (the shared `isolated` fixture is imported, then requested by name)
from __future__ import annotations

import asyncio
import json

import pytest

from backend.ai.tools import execute_tool, registry
from backend.db.lot_store import LotStore
from backend.db.position_store import PositionStore
from backend.services import lots as lots_service
from backend.services import positions as positions_service
from backend.services.portfolio_history import holdings_as_of
from tests.test_position_figures import _account, _grant, _instrument, _vest, isolated  # noqa: F401

_TOOL_USE_ID = "toolu_update_lots_test"


@pytest.fixture
def unattended(monkeypatch):
    monkeypatch.setattr(registry, "should_preview_write", lambda: False)


@pytest.fixture
def attended(monkeypatch):
    monkeypatch.setattr(
        registry, "should_preview_write", lambda: not registry.is_redeeming_approval()
    )


def call(**inp):
    text, proposed = asyncio.run(execute_tool("update_lots", inp, tool_use_id=_TOOL_USE_ID))
    return (json.loads(text) if text is not None else None), proposed


def approve():
    """What the server does when the user approves the card (``resolve_pending_tool_use``):
    re-run the tool with the card's tool_use_id as its confirm token. The handler pops the
    STAGED args and ignores anything else — a card cannot commit something it didn't show."""
    from backend.ai.tools import _confirm_gate as _gate

    return call(**{_gate.CONFIRM_TOKEN_KEY: _TOOL_USE_ID})


def _undated_holding(qty: float = 10) -> tuple[str, str]:
    inst, acct = _instrument(), _account()
    res = positions_service.apply_position_change(
        {
            "type": "add",
            "instrument_id": inst["id"],
            "account_id": acct["id"],
            "quantity": qty,
            "cost_basis": 50.0,
        }
    )
    return res["position_id"], res["lot_id"]


def test_setting_the_real_date_on_an_opening_lot(isolated, unattended):
    pid, lot_id = _undated_holding()

    res, proposed = call(updates=[{"lot_id": lot_id, "acquired_at": "2023-03-12", "kind": "buy"}])

    assert proposed is None and res["ok"] is True
    lot = LotStore().find_by_id(lot_id)
    assert (lot["acquired_at"], lot["kind"]) == ("2023-03-12", "buy")
    assert PositionStore().find_by_id(pid)["quantity"] == 10.0
    assert holdings_as_of("2024-01-01"), "history now holds it from the real purchase date"


def test_a_quantity_correction_moves_the_position(isolated, unattended):
    pid, lot_id = _undated_holding()

    res, _ = call(lot_id=lot_id, quantity=12)

    assert res["ok"] is True
    assert PositionStore().find_by_id(pid)["quantity"] == 12.0
    # The position's figure is named as the position's — a bare `quantity` beside a lot
    # edit reads as the lot's own.
    assert res["position_quantity_after"] == 12.0
    assert "quantity" not in res


def test_on_an_attended_turn_it_stages_a_before_after_card_and_writes_on_approval(
    isolated, attended
):
    pid, lot_id = _undated_holding()
    today = LotStore().find_by_id(lot_id)["acquired_at"]

    res, proposed = call(lot_id=lot_id, acquired_at="2023-03-12")

    assert res is None, "a staged card pauses the tool_use — no write yet"
    assert proposed["type"] == "lot_update"
    assert proposed["display"]["acquired_at"] == f"{today} → 2023-03-12"
    assert LotStore().find_by_id(lot_id)["acquired_at"] == today

    res, proposed = approve()
    assert proposed is None and res["ok"] is True
    assert LotStore().find_by_id(lot_id)["acquired_at"] == "2023-03-12"


def test_a_batch_is_one_card_and_a_bad_item_refuses_before_any_card(isolated, attended):
    _, first = _undated_holding()
    inst, acct = _instrument("OTHR"), _account("Other")
    second = positions_service.apply_position_change(
        {"type": "add", "instrument_id": inst["id"], "account_id": acct["id"], "quantity": 3}
    )["lot_id"]

    res, proposed = call(
        updates=[{"lot_id": first, "acquired_at": "2022-01-03"}, {"lot_id": second, "fees": 1.5}]
    )
    assert res is None and proposed["display"]["count"] == 2

    res, proposed = call(updates=[{"lot_id": first, "acquired_at": "2999-01-01"}])
    assert proposed is None and "future" in res["error"]


@pytest.mark.parametrize(
    "fields, refusal",
    [
        ({"acquired_at": "12/03/2023"}, "ISO date"),
        ({"quantity": 0}, "positive"),
        ({"kind": "grant"}, "kind must be one of"),
        ({}, "nothing to change"),
    ],
)
def test_invalid_corrections_are_refused_and_nothing_moves(isolated, unattended, fields, refusal):
    pid, lot_id = _undated_holding()
    before = LotStore().find_by_id(lot_id)

    res, _ = call(lot_id=lot_id, **fields)

    assert refusal in res["error"]
    assert LotStore().find_by_id(lot_id) == before


def _sell_four(pid: str) -> str:
    return lots_service.apply_lot_change(
        {
            "type": "dispose",
            "position_id": pid,
            "quantity": 4,
            "proceeds_per_unit": 60.0,
            "proceeds_currency": "SGD",
            "disposed_at": "2026-10-12",
            "method": "fifo",
        }
    )["event_id"]


def test_a_sold_lot_is_correctable_but_never_below_what_was_sold(isolated, unattended):
    """2026-10-13: a sold lot used to be frozen, so a user could not give it its real date."""
    pid, lot_id = _undated_holding()
    _sell_four(pid)

    res, _ = call(lot_id=lot_id, acquired_at="2023-03-12")
    assert res["ok"] is True
    assert LotStore().find_by_id(lot_id)["acquired_at"] == "2023-03-12"

    res, _ = call(lot_id=lot_id, quantity=3)
    assert "already been sold" in res["error"]


def test_the_card_for_a_sold_lot_shows_each_sales_gain_before_and_after(isolated, attended):
    pid, lot_id = _undated_holding()
    _sell_four(pid)

    res, proposed = call(lot_id=lot_id, unit_cost=40.0)

    assert res is None and proposed["type"] == "lot_update"
    (row,) = proposed["display"]["sales_updated"]
    assert row["sale_date"] == "2026-10-12"
    assert "→ +80.00 SGD" in row["realized_gain"]  # 4 × (60 − 40)
    res, _ = approve()
    assert res["ok"] is True


def test_a_vests_lot_is_refused_even_unattended(isolated, unattended):
    inst, acct = _instrument("VEST"), _account("Custodian")
    grant = _grant(inst, acct, release_required=False)
    lot_id = _vest(grant["id"], grant["tranches"][0]["id"])["lot_id"]

    res, _ = call(lot_id=lot_id, quantity=1)

    assert "undo the vest" in res["error"]
    assert LotStore().find_by_id(lot_id)["original_qty"] == 200.0


def test_an_unknown_lot_points_at_get_lots(isolated, unattended):
    res, _ = call(lot_id="lot_nope", note="x")
    assert "get_lots" in res["error"]


def test_approving_a_batch_card_writes_every_correction(isolated, attended):
    pid, first = _undated_holding()
    inst, acct = _instrument("OTHR"), _account("Other")
    second = positions_service.apply_position_change(
        {"type": "add", "instrument_id": inst["id"], "account_id": acct["id"], "quantity": 3}
    )["lot_id"]
    edits = [{"lot_id": first, "acquired_at": "2022-01-03"}, {"lot_id": second, "quantity": 4}]

    res, proposed = call(updates=edits)
    assert res is None and proposed["display"]["count"] == 2

    res, proposed = approve()
    assert proposed is None and res["succeeded"] == 2
    assert LotStore().find_by_id(first)["acquired_at"] == "2022-01-03"
    assert LotStore().find_by_id(second)["original_qty"] == 4.0


def test_approval_is_refused_when_the_lot_changed_after_the_card(isolated, attended):
    """The card promised a before→after; if the lot moved in between, approving it would
    commit something the user never saw."""
    _, lot_id = _undated_holding()
    res, proposed = call(lot_id=lot_id, acquired_at="2023-03-12")
    assert res is None
    LotStore().touch(lot_id, {"acquired_at": "2024-05-05"})  # changed elsewhere meanwhile

    res, proposed = approve()

    assert proposed is None and "error" in res
    assert LotStore().find_by_id(lot_id)["acquired_at"] == "2024-05-05"


def test_correcting_a_funded_lot_moves_its_cash_leg_with_it(isolated, unattended, monkeypatch):
    from backend.core import fx as core_fx
    from backend.db.account_store import AccountStore
    from backend.db.transaction_store import TransactionStore

    monkeypatch.setattr(core_fx, "_fx_rates_loader", lambda: {"SGD": 1.0})
    inst, acct = _instrument("CASH"), _account("Broker")
    cash = AccountStore().create(
        {"name": "Broker cash", "kind": "asset", "type": "investment", "currency": "SGD"}
    )
    pid = positions_service.apply_position_change(
        {"type": "add", "instrument_id": inst["id"], "account_id": acct["id"], "quantity": 0}
    )["position_id"]
    bought = lots_service.apply_lot_change(
        {
            "type": "acquire",
            "position_id": pid,
            "quantity": 10,
            "unit_cost": 20,
            "cost_currency": "SGD",
            "acquired_at": "2026-01-05",
            "funding_account_id": cash["id"],
        }
    )

    res, _ = call(lot_id=bought["lot_id"], acquired_at="2026-01-02", unit_cost=21)

    assert res["ok"] is True
    live = [t for t in TransactionStore().all() if not t.get("deleted")]
    assert len(live) == 1, "the old cash leg is replaced, never doubled"
    assert (live[0]["date"], live[0]["from_amount"]) == ("2026-01-02", 210.0)
    assert LotStore().find_by_id(bought["lot_id"])["txn_id"] == live[0]["id"]


def test_history_and_returns_follow_a_corrected_date(isolated, unattended):
    from backend.db.price_history_store import PriceHistoryStore
    from backend.services import returns

    pid, lot_id = _undated_holding()
    symbol = PositionStore().find_by_id(pid)["instrument_id"]
    PriceHistoryStore().record("ACME", 55.0, day="2026-03-10", source="manual")
    assert holdings_as_of("2026-03-31") == {}, "an opening balance dated today isn't in March"

    call(lot_id=lot_id, acquired_at="2026-03-10", kind="buy")

    assert holdings_as_of("2026-03-31")[symbol] == pytest.approx(10.0)
    flows, _ = returns.tagged_cash_flows("2026-03-01", "2026-03-31", "SGD", rates={"SGD": 1})
    assert flows == [("2026-03-10", -500.0, symbol)], "a buy enters at its cost, 10 × 50"


def test_one_correction_is_a_single_card_without_no_op_rows(isolated, attended):
    """Found live (2026-10-09): Lucius sends a one-item `updates` list, which drew a "batch"
    card with the change as a JSON blob — and a no-op "cost_currency: USD → USD" row."""
    _, lot_id = _undated_holding()
    today = LotStore().find_by_id(lot_id)["acquired_at"]
    edit = {"lot_id": lot_id, "acquired_at": "2024-03-14", "unit_cost": 87.5, "kind": "buy"}

    res, proposed = call(updates=[{**edit, "cost_currency": "SGD"}])

    assert res is None and proposed["type"] == "lot_update"
    shown = proposed["display"]
    assert shown["acquired_at"] == f"{today} → 2024-03-14"
    assert shown["unit_cost"] == "50.0 → 87.5" and shown["kind"] == "opening → buy"
    assert "cost_currency" not in shown, "an unchanged field is not a change"

    res, proposed = approve()
    assert proposed is None and res["ok"] is True
    lot = LotStore().find_by_id(lot_id)
    assert (lot["acquired_at"], lot["unit_cost"], lot["kind"]) == ("2024-03-14", 87.5, "buy")


def test_a_batch_of_two_lots_from_one_sale_is_refused_on_the_card(isolated, attended):
    """Each row's before → after is previewed against the stored book, so two lots that fed one
    sale would each show a 'before' the other already changed (review, 2026-10-13)."""
    pid, first = _undated_holding(5)
    second = lots_service.apply_lot_change(
        {
            "type": "acquire",
            "position_id": pid,
            "quantity": 5,
            "unit_cost": 55.0,
            "acquired_at": "2025-06-01",
        }
    )["lot_id"]
    lots_service.apply_lot_change(
        {
            "type": "dispose",
            "position_id": pid,
            "quantity": 8,
            "proceeds_per_unit": 60.0,
            "proceeds_currency": "SGD",
            "disposed_at": "2026-10-12",
            "method": "fifo",
        }
    )

    res, proposed = call(
        updates=[{"lot_id": first, "unit_cost": 40.0}, {"lot_id": second, "unit_cost": 45.0}]
    )

    assert proposed is None and "same sale" in json.dumps(res)


def test_dating_two_lots_from_one_sale_in_one_batch_is_allowed(isolated, attended):
    """Re-review: the shared-sale refusal fired on date-only corrections, which move no gain —
    blocking "date both my opening lots", the commonest correction."""
    pid, first = _undated_holding(5)
    second = lots_service.apply_lot_change(
        {"type": "acquire", "position_id": pid, "quantity": 5, "unit_cost": 55.0, "kind": "opening"}
    )["lot_id"]
    lots_service.apply_lot_change(
        {
            "type": "dispose",
            "position_id": pid,
            "quantity": 8,
            "proceeds_per_unit": 60.0,
            "proceeds_currency": "SGD",
            "disposed_at": "2026-10-12",
            "method": "fifo",
        }
    )

    res, proposed = call(
        updates=[
            {"lot_id": first, "acquired_at": "2023-01-02"},
            {"lot_id": second, "acquired_at": "2023-02-03"},
        ]
    )

    assert res is None and proposed["type"] == "lot_update_batch"
    res, _ = approve()
    assert LotStore().find_by_id(second)["acquired_at"] == "2023-02-03"
