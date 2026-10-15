"""
AI-tool dispatch tests for the tax-lot tools (P2-INV1c-a).

These pin the APPROVE-TIER propose→confirm→apply contract for lots, mirroring
tests/test_char_tool_dispatch.py (async dispatch via asyncio.run) and the
real-store isolation in tests/test_lots_apply_idempotency.py:

  - propose_lot_dispose returns a proposed_change (element 1) carrying the
    realized-gain PREVIEW in display, and writes NOTHING (no realized event).
  - dispose_lots performs the write through services.lots.apply_lot_change.
  - propose_lot_acquire returns a proposed_change; acquire_lots writes the lot.
  - the propose_ tools are named so the existing PROPOSED CHANGE PROTOCOL gate fires.

Offline, isolated to tmp_path, no LLM / network — not an integration test.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from backend.ai.tools import execute_tool
from backend.ai.tools import registry as tool_registry
from backend.db import lot_store
from backend.db.position_store import PositionStore
from backend.db.realized_event_store import RealizedEventStore

# A renamed tool name no longer resolves at runtime (ADR 2026-08-25-tool-names-are-final) —
# the migration's rename table is the only record that a rename happened.
from backend.services import lots as svc

# A gated tool binds its confirm card to the tool_use_id of the call being gated, which a
# real turn always supplies to dispatch. Passing one here is that turn-loop binding.
_TOOL_USE_ID = "toolu_test"


def call(tool, **inp):
    text, proposed = asyncio.run(execute_tool(tool, inp, tool_use_id=_TOOL_USE_ID))
    return (json.loads(text) if text is not None else None), proposed


def _preview(monkeypatch):
    """propose_lot_dispose/propose_lot_acquire are now ALIASES of dispose_lots/
    acquire_lots (the 2026-07-19 propose_*/apply_* merge) — the SAME tool previews or
    writes depending on should_preview_write(), not on which name it was called by. Call
    this in a test that wants the PREVIEW branch (a bare test call sets no cost surface,
    which fails OPEN to a direct write)."""
    monkeypatch.setattr(tool_registry, "should_preview_write", lambda: True)


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(svc, "_load_rates", lambda: {"USD": 1.0})
    monkeypatch.setattr(svc, "base_currency", lambda: "USD")
    return tmp_path


def _seed_position():
    return PositionStore().create(
        {"instrument_id": "inst-1", "account_id": "acc-1", "quantity": 0, "cost_currency": "USD"}
    )


def _seed_lot(position_id, qty=10, unit_cost=100, acquired_at="2026-01-01"):
    svc.apply_lot_change(
        {
            "type": "acquire",
            "position_id": position_id,
            "quantity": qty,
            "unit_cost": unit_cost,
            "cost_currency": "USD",
            "acquired_at": acquired_at,
        }
    )


# ── propose_lot_dispose: previews, writes nothing, is approve-tier ───────────────


def test_propose_lot_dispose_returns_preview_and_writes_nothing(isolated, monkeypatch):
    _preview(monkeypatch)
    pos = _seed_position()
    _seed_lot(pos["id"])
    res, proposed = call(
        "dispose_lots",
        position_id=pos["id"],
        quantity=4,
        proceeds_per_unit=130,
        proceeds_currency="USD",
        method="fifo",
        disposed_at="2026-06-01",
    )
    assert res is None
    assert proposed is not None
    assert proposed["type"] == "lot_dispose"
    # The realized-gain PREVIEW is in display: 4 × (130 − 100) = +120.
    assert proposed["display"]["realized_gain"] == 120.00
    assert proposed["display"]["consumptions"][0]["qty"] == 4.0
    # FE-4: the confirm card must show the booked date + total proceeds before applying.
    assert proposed["display"]["disposed_at"] == "2026-06-01"
    assert proposed["display"]["total_proceeds"] == 520.00  # 4 × 130
    # A client idempotency key is attached so a later apply is dedupe-safe.
    assert proposed["idempotency_key"]
    # NOTHING was written — propose never executes the sale.
    assert RealizedEventStore().all() == []


def test_propose_names_resolve_to_their_apply_twins(isolated):
    """propose_lot_dispose/propose_lot_acquire are now ALIASES of dispose_lots/
    acquire_lots (2026-07-19 propose_*/apply_* merge) — deliberately absent from
    TOOL_SCHEMAS (aliases carry zero prompt cost); the PREVIEW behavior now keys off
    registry.should_preview_write() (a card-capable/attended turn), not a name prefix."""
    from backend.ai.tools import TOOL_SCHEMAS

    names = {t["name"] for t in TOOL_SCHEMAS}
    assert {"dispose_lots", "acquire_lots", "get_lots", "get_realized_pnl"} <= names
    assert "propose_lot_dispose" not in names and "propose_lot_acquire" not in names


def test_apply_lot_dispose_disposals_batch_item_matches_root_shape():
    """``dispose_lots`` derives from a model (ADR `2026-07-19-derive-tool-schemas-
    from-models`) whose ``disposals`` batch item mirrors the root fields — proving the
    "batch shape isn't cleanly expressible as a derived model" assumption from the
    ADR/CLAUDE.md (which blocked several other tools' batch arrays as literals) does NOT
    hold here: a ``List[Item]`` field derives fine even for this messier case, where the
    batch item carries the bare (no ``additionalProperties``, no ``required``) field shapes.

    Since the 2026-07-19d dual-shape cleanup the ROOT no longer mirrors those fields at all
    (schema_of_batch_only un-advertises the duplicate — the bare call still works, it is
    just no longer described), so `required` names the batch wrapper and the item schema is
    the single place the shape is asserted."""
    from backend.ai.tools import TOOL_SCHEMAS

    schema = next(t for t in TOOL_SCHEMAS if t["name"] == "dispose_lots")["input_schema"]
    assert schema["required"] == ["disposals"]
    assert "position_id" not in schema["properties"], (
        "the root bare mirror is deliberately un-advertised — see schema_of_batch_only"
    )

    item_schema = schema["properties"]["disposals"]["items"]
    assert "additionalProperties" not in item_schema
    assert "required" not in item_schema
    # Type + absence of additionalProperties/required is what this test is about (see the
    # docstring). A `description` is additive metadata, not part of the shape — every
    # money-critical disposal field carries one since lane/money-param-descriptions, so
    # assert the type rather than an exact-dict equality that a description would break.
    assert item_schema["properties"]["position_id"]["type"] == "string"
    assert item_schema["properties"]["position_id"]["description"]
    assert item_schema["properties"]["allow_duplicate"]["description"]

    lot_selection_item = item_schema["properties"]["lot_selection"]["items"]
    assert "additionalProperties" not in lot_selection_item
    assert "required" not in lot_selection_item
    assert set(lot_selection_item["properties"]) == {"lot_id", "qty"}


def test_propose_lot_dispose_oversell_surfaces_error_no_proposal(isolated):
    pos = _seed_position()
    _seed_lot(pos["id"], qty=5)
    res, proposed = call(
        "dispose_lots",
        position_id=pos["id"],
        quantity=10,  # more than the 5 held
        proceeds_per_unit=130,
        proceeds_currency="USD",
    )
    assert "error" in res
    assert proposed is None
    assert RealizedEventStore().all() == []


def test_propose_lot_dispose_unknown_position(isolated):
    res, proposed = call("dispose_lots", position_id="nope", quantity=1)
    assert "error" in res
    assert proposed is None


def test_propose_lot_dispose_taxable_zero_proceeds_surfaces_error(isolated):
    # The money guard (P2-INV1c) reaches the AI propose path: a taxable $0 sale is surfaced
    # as an error, not a proposal that would book a fabricated full-cost loss on confirm.
    pos = _seed_position()
    _seed_lot(pos["id"])
    res, proposed = call(
        "dispose_lots",
        position_id=pos["id"],
        quantity=10,
        proceeds_per_unit=0,
        proceeds_currency="USD",
    )
    assert "error" in res
    assert proposed is None
    assert RealizedEventStore().all() == []


def _seed_two_brokerages():
    from backend.db.account_store import AccountStore

    src = AccountStore().create(
        {"name": "Workplace Custodian", "kind": "asset", "type": "investment", "currency": "USD"}
    )
    dst = AccountStore().create(
        {"name": "IBKR", "kind": "asset", "type": "investment", "currency": "USD"}
    )
    pos = PositionStore().create(
        {"instrument_id": "inst-1", "account_id": src["id"], "quantity": 0, "cost_currency": "USD"}
    )
    return src, dst, pos


def test_transfer_destination_on_the_card_then_stored(isolated, monkeypatch):
    # A share move names its destination by id OR exact name; the confirm card shows the
    # resolved NAME (the user approves "→ IBKR", not an opaque id) and the apply stores the id.
    _preview(monkeypatch)
    src, dst, pos = _seed_two_brokerages()
    _seed_lot(pos["id"])
    res, proposed = call(
        "dispose_lots",
        position_id=pos["id"],
        quantity=10,
        proceeds_per_unit=0,
        proceeds_currency="USD",
        disposed_at="2026-06-01",
        is_taxable=False,
        transfer_to_account_id="ibkr",
    )
    assert res is None, res
    assert proposed["display"]["transfer_to_account_id"] == dst["id"]
    assert proposed["display"]["transfer_to_account_name"] == "IBKR"
    assert proposed["display"]["is_taxable"] is False
    assert RealizedEventStore().all() == []

    monkeypatch.setattr(tool_registry, "should_preview_write", lambda: False)
    res, proposed = call(
        "dispose_lots",
        position_id=pos["id"],
        quantity=10,
        proceeds_per_unit=0,
        proceeds_currency="USD",
        disposed_at="2026-06-01",
        is_taxable=False,
        transfer_to_account_id="ibkr",
    )
    assert proposed is None
    assert res["ok"] is True, res
    assert res["transfer_to_account_id"] == dst["id"]
    (ev,) = RealizedEventStore().all()
    assert ev["transfer_to_account_id"] == dst["id"]
    assert ev["realized_gain"] is None


def test_transfer_destination_on_a_sale_is_refused_before_the_card(isolated, monkeypatch):
    _preview(monkeypatch)
    src, dst, pos = _seed_two_brokerages()
    _seed_lot(pos["id"])
    res, proposed = call(
        "dispose_lots",
        position_id=pos["id"],
        quantity=10,
        proceeds_per_unit=130,
        proceeds_currency="USD",
        transfer_to_account_id=dst["id"],
    )
    assert proposed is None
    assert "moves shares, not money" in res["error"]
    assert RealizedEventStore().all() == []


# ── dispose_lots: the write path through apply_lot_change ───────────────────


def test_apply_lot_dispose_books_event_and_consumes(isolated):
    pos = _seed_position()
    _seed_lot(pos["id"])
    res, proposed = call(
        "dispose_lots",
        position_id=pos["id"],
        quantity=4,
        proceeds_per_unit=130,
        proceeds_currency="USD",
        method="fifo",
        disposed_at="2026-06-01",
    )
    assert proposed is None  # apply returns no proposed_change
    assert res["ok"] is True
    assert res["realized_gain"] == 120.00
    assert len(RealizedEventStore().all()) == 1
    assert PositionStore().find_by_id(pos["id"])["quantity"] == 6.0


def test_apply_lot_dispose_idempotent_on_key(isolated):
    pos = _seed_position()
    _seed_lot(pos["id"])
    args = dict(
        position_id=pos["id"],
        quantity=4,
        proceeds_per_unit=130,
        proceeds_currency="USD",
        method="fifo",
        disposed_at="2026-06-01",
        idempotency_key="k-1",
    )
    first, _ = call("dispose_lots", **args)
    second, _ = call("dispose_lots", **args)  # retry
    assert second.get("idempotent") is True
    assert second["event_id"] == first["event_id"]
    assert len(RealizedEventStore().all()) == 1
    # A replay sold nothing — it must not claim to have.
    assert "quantity_sold" not in second


# ── the model-facing reply names what the call moved vs what the position holds ──
# The service spreads the position's re-derived figures under a bare `quantity`. Read by a
# model, that is the amount the call moved: live (2026-10-11) a 2-share sale came back as
# `quantity: 40.1867` and Lucius told the user the sale had gone wrong.

_SALE = dict(
    quantity=4,
    proceeds_per_unit=130,
    proceeds_currency="USD",
    method="fifo",
    disposed_at="2026-06-01",
)


def test_a_sale_reply_names_the_quantity_sold_not_the_position_left(isolated):
    pos = _seed_position()
    _seed_lot(pos["id"], qty=10)

    res, _ = call("dispose_lots", position_id=pos["id"], **_SALE)

    assert res["quantity_sold"] == 4
    assert res["position_quantity_after"] == 6.0
    assert "quantity" not in res


def test_a_batch_sale_reply_names_each_items_quantity_sold(isolated):
    a, b = _seed_position(), _seed_position()
    _seed_lot(a["id"], qty=10)
    _seed_lot(b["id"], qty=5)

    res, _ = call(
        "dispose_lots",
        disposals=[
            {"position_id": a["id"], **_SALE},
            {"position_id": b["id"], **{**_SALE, "quantity": 1}},
        ],
    )

    [first, second] = res["results"]
    assert (first["quantity_sold"], first["position_quantity_after"]) == (4, 6.0)
    assert (second["quantity_sold"], second["position_quantity_after"]) == (1, 4.0)
    assert "quantity" not in first and "quantity" not in second


def test_a_buy_reply_names_the_quantity_acquired_not_the_position_held(isolated):
    pos = _seed_position()
    _seed_lot(pos["id"], qty=10)

    res, _ = call(
        "acquire_lots",
        position_id=pos["id"],
        quantity=3,
        unit_cost=110,
        cost_currency="USD",
        acquired_at="2026-02-01",
    )

    assert res["quantity_acquired"] == 3
    assert res["position_quantity_after"] == 13.0
    assert "quantity" not in res and "cost_basis" not in res


def test_undoing_a_sale_reports_the_position_not_a_bare_quantity(isolated):
    pos = _seed_position()
    _seed_lot(pos["id"], qty=10)
    sold, _ = call("dispose_lots", position_id=pos["id"], **_SALE)

    res, _ = call("undo_lot_disposals", event_id=sold["event_id"])

    assert res["ok"] is True
    assert res["position_quantity_after"] == 10.0
    assert "quantity" not in res


# ── acquire path ────────────────────────────────────────────────────────────────


def test_propose_then_apply_lot_acquire(isolated, monkeypatch):
    pos = _seed_position()
    _preview(monkeypatch)
    res, proposed = call(
        "acquire_lots",
        position_id=pos["id"],
        quantity=10,
        unit_cost=100,
        cost_currency="USD",
        acquired_at="2026-01-01",
    )
    assert res is None
    assert proposed["type"] == "lot_acquire"
    # propose wrote nothing.
    assert lot_store.LotStore().all() == []

    # A DIFFERENT call, no confirm_token — the direct-write branch (an unattended/automated
    # caller submitting the trade itself, not confirming the card above).
    monkeypatch.setattr(tool_registry, "should_preview_write", lambda: False)
    applied, applied_change = call(
        "acquire_lots",
        position_id=pos["id"],
        quantity=10,
        unit_cost=100,
        cost_currency="USD",
        acquired_at="2026-01-01",
    )
    assert applied_change is None
    assert applied["ok"] is True
    assert PositionStore().find_by_id(pos["id"])["quantity"] == 10.0
    assert PositionStore().find_by_id(pos["id"])["cost_basis"] == 100.0


def test_apply_lot_acquire_next_step_directs_kind_investment(isolated):
    """Regression: the post-buy cash-leg guidance must say kind=investment, never the
    retired ``kind=expense, category=investment`` form — else AI-driven buys land as
    SPENDING in the budget (the exact thing kind=investment exists to prevent)."""
    pos = _seed_position()
    applied, _ = call(
        "acquire_lots",
        position_id=pos["id"],
        quantity=10,
        unit_cost=100,
        cost_currency="USD",
        acquired_at="2026-01-01",
    )
    step = applied.get("_next_step", "")
    assert "kind=investment" in step
    assert "kind=expense" not in step
    assert "category=investment" not in step


def test_acquire_lots_funding_debits_and_skips_followup_cash(isolated, monkeypatch):
    """funding_account_id books the debit in-choke so Lucius must not add_transactions again."""
    from backend.db.account_store import AccountStore
    from backend.db.transaction_store import TransactionStore
    from backend.services import ledger as ledger_service

    monkeypatch.setattr(ledger_service, "accounts", AccountStore())
    monkeypatch.setattr(ledger_service, "transactions", TransactionStore())
    monkeypatch.setattr(ledger_service, "base_currency", lambda: "USD")
    cash = AccountStore().create(
        {"name": "Brokerage cash", "kind": "asset", "type": "investment", "currency": "USD"}
    )
    pos = _seed_position()
    applied, _ = call(
        "acquire_lots",
        position_id=pos["id"],
        quantity=10,
        unit_cost=100,
        fees=5,
        cost_currency="USD",
        acquired_at="2026-01-01",
        funding_account_id=cash["id"],
    )
    assert applied["ok"] is True
    assert applied["cash_leg_id"]
    assert "do NOT add a second cash transaction" in applied.get("_next_step", "")
    live = [t for t in TransactionStore().all() if not t.get("deleted")]
    dest = AccountStore().find_by_id(cash["id"])
    assert (
        ledger_service.account_balance(dest, live, ledger_service.opening_date(dest, live))
        == -1005.0
    )


# ── reads ─────────────────────────────────────────────────────────────────────


def test_get_lots_and_get_realized_pnl(isolated):
    pos = _seed_position()
    _seed_lot(pos["id"])
    res, proposed = call("get_lots", position_id=pos["id"])
    assert proposed is None
    assert len(res["lots"]) == 1
    assert res["lots"][0]["effective_unit_cost"] == 100.0

    call(
        "dispose_lots",
        position_id=pos["id"],
        quantity=10,
        proceeds_per_unit=130,
        proceeds_currency="USD",
        method="fifo",
        disposed_at="2026-06-01",
    )
    pnl, _ = call("get_realized_pnl")
    assert pnl["realized_total_base"] == 300.00
    assert len(pnl["events"]) == 1


def test_get_lots_totals_basis_matches_service_not_a_resum_of_rounded_rows(isolated):
    """MATH-44 regression: get_lots' `totals.remaining_basis` must equal
    services.lots.position_remaining_basis (sum-then-round-once), never a re-sum of the
    already-2dp-rounded per-lot remaining_basis values. Three lots of 3 x 0.335: each lot
    rounds to 1.01 (3.03 re-summed) but the service's single round of the unrounded sum
    (3.015) is 3.02 — the two must not disagree."""
    pos = _seed_position()
    for i in range(3):
        _seed_lot(pos["id"], qty=3, unit_cost=0.335, acquired_at=f"2026-01-0{i + 1}")
    res, _ = call("get_lots", position_id=pos["id"])
    expected = svc.position_remaining_basis(pos["id"])
    assert expected == 3.02
    assert res["totals"]["remaining_basis"] == expected


# ── The confirm card names what it touches (display only) ─────────────────────────────────


def test_a_funded_buy_card_names_the_funding_account(isolated, monkeypatch):
    from backend.db.account_store import AccountStore

    pos = _seed_position()
    acc = AccountStore().create(
        {"name": "Brokerage Cash", "type": "investment", "currency": "USD", "kind": "asset"}
    )
    _preview(monkeypatch)
    _, card = call(
        "acquire_lots",
        position_id=pos["id"],
        quantity=10,
        unit_cost=100,
        cost_currency="USD",
        acquired_at="2026-01-01",
        funding_account_id=acc["id"],
    )
    assert card["display"]["funding_account_id"] == acc["id"]
    assert card["display"]["funding_account_name"] == "Brokerage Cash"


def test_a_lot_delete_card_carries_the_lots_cost_currency(isolated, monkeypatch):
    pos = _seed_position()
    _seed_lot(pos["id"], qty=4, unit_cost=100)
    lot = lot_store.LotStore().all()[0]
    _preview(monkeypatch)
    _, card = call("delete_lots", lot_id=lot["id"], position_id=pos["id"])
    assert card["type"] == "lot_delete"
    assert card["display"]["cost_currency"] == "USD"


def test_approving_the_named_cards_still_writes(isolated, monkeypatch):
    """Both cards are re-derived and compared on approval; the added name and currency must
    re-derive identically, or every approval would be refused as "the book moved"."""
    from backend.ai.tools import _confirm_gate as _gate
    from backend.db.account_store import AccountStore
    from backend.services import ledger as ledger_svc

    # The funded buy books a USD cash leg; keep the ledger's base USD so no FX is needed.
    monkeypatch.setattr(ledger_svc, "base_currency", lambda: "USD")
    monkeypatch.setattr(
        tool_registry, "should_preview_write", lambda: not tool_registry.is_redeeming_approval()
    )
    approve = {_gate.CONFIRM_TOKEN_KEY: _TOOL_USE_ID}
    pos = _seed_position()
    acc = AccountStore().create(
        {"name": "Brokerage Cash", "type": "investment", "currency": "USD", "kind": "asset"}
    )
    call(
        "acquire_lots",
        position_id=pos["id"],
        quantity=10,
        unit_cost=100,
        cost_currency="USD",
        acquired_at="2026-01-01",
        funding_account_id=acc["id"],
    )
    bought, _ = call("acquire_lots", **approve)
    assert bought["ok"] is True, bought
    lot_id = bought["lot_id"]

    call("delete_lots", lot_id=lot_id, position_id=pos["id"])
    deleted, _ = call("delete_lots", **approve)
    assert deleted["ok"] is True, deleted
    assert lot_store.LotStore().find_by_id(lot_id) is None
