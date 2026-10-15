"""Regression tests: a `propose_*` PREVIEW must not mutate state, and a rejected proposal
must leave no debris — while the matching `apply_*` still performs the write end to end.

``propose_position_update``/``propose_lot_dispose`` are now ALIASES of
``update_positions``/``dispose_lots`` (the propose_*/apply_* pair collapse,
harness reliability program 2026-07-19) — calling the alias with no ``confirm_token`` on a
card-capable/attended turn PREVIEWS (stages, returns a card) exactly like the old separate
propose tool did; the ``isolated`` fixture below forces ``registry.should_preview_write()``
True so these tests keep exercising that path deterministically (a bare test run sets no
cost surface, which would otherwise fail OPEN to a direct write — see that function's
docstring).

Two confirmed leaks are pinned here:

  1. ``propose_position_update`` with an ``account_name`` used to call
     ``positions.resolve_account``, which find-or-CREATEs — so merely PROPOSING a move to a
     typo'd or not-yet-existing venue silently minted a real account, even if the user never
     confirmed. It now resolves READ-ONLY via ``positions.find_account`` and flags
     ``account_to_is_new`` on the card; ``apply_position_change`` mints at confirm time.

  2. ``propose_lot_dispose`` -> ``lots.preview_disposal`` used to call ``ensure_opening_lot``,
     INSERTING the synthetic opening lot for a pre-lots holding just to price the preview.
     It now projects that lot in memory via ``lots.projected_open_lots``; only the apply path
     persists it. The projected lot carries the same deterministic id apply will store, so
     the preview's consumptions name the lots apply really consumes.

The double-book guards that fire at propose time (``propose_lot_acquire``,
``propose_lot_dispose``) are read-only and unaffected — covered by
``tests/test_lot_double_book_guard.py``; the dispose-side guard is re-pinned here against a
pre-lots position, the exact shape whose preview used to write.

Offline, isolated to tmp_path, no LLM / network.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from backend.ai.tools import execute_tool
from backend.ai.tools import registry as tool_registry
from backend.db.account_store import AccountStore
from backend.db.instrument_store import InstrumentStore
from backend.db.lot_store import LotStore
from backend.db.position_store import PositionStore
from backend.services import lots as lots_svc
from backend.services import positions as pos_svc

# A gated tool binds its confirm card to the tool_use_id of the call being gated, which a
# real turn always supplies to dispatch. Passing one here is that turn-loop binding.
_TOOL_USE_ID = "toolu_test"


def call(tool, **inp):
    text, proposed = asyncio.run(execute_tool(tool, inp, tool_use_id=_TOOL_USE_ID))
    return (json.loads(text) if text is not None else None), proposed


@pytest.fixture
def isolated(tmp_path, monkeypatch, db_isolated):
    monkeypatch.setattr(lots_svc, "_load_rates", lambda: {"USD": 1.0})
    monkeypatch.setattr(lots_svc, "base_currency", lambda: "USD")
    # Force the confirm-gate's surface check True — a bare test call sets no cost surface,
    # which fails OPEN (direct write) by design; these tests want the PREVIEW branch.
    monkeypatch.setattr(tool_registry, "should_preview_write", lambda: True)
    return tmp_path


def _seed_instrument():
    return InstrumentStore().create(
        {
            "symbol": "SPCX",
            "price_source_id": "SPCX",
            "quote_currency": "USD",
            "asset_class": "equity",
        }
    )


def _seed_account(name="Vantage Brokerage"):
    return AccountStore().create(
        {"name": name, "type": "investment", "currency": "USD", "kind": "asset"}
    )


def _pre_lots_position(inst, acc, quantity=10.0, cost_basis=100.0):
    """A legacy aggregate holding as revision 202610111200 leaves it — the position plus ONE
    opening lot of exactly its figures. (Its disposal preview used to seed, and so persist,
    that lot itself; nothing on a read path writes one any more.)"""
    pos = PositionStore().create(
        {
            "instrument_id": inst["id"],
            "account_id": acc["id"],
            "quantity": quantity,
            "cost_basis": cost_basis,
            "cost_currency": "USD",
            "acquired_at": "2020-01-02",
        }
    )
    if quantity > 0:
        LotStore().create(
            {
                "position_id": pos["id"],
                "instrument_id": inst["id"],
                "account_id": acc["id"],
                "acquired_at": "2020-01-02",
                "original_qty": quantity,
                "unit_cost": cost_basis,
                "cost_currency": "USD",
                "kind": "opening",
            }
        )
    return pos


# ── Case 1: propose_position_update must not create an account ───────────────────


def test_propose_position_update_does_not_create_a_missing_account(isolated):
    """Previewing a move to a venue that does not exist creates NOTHING — the card just
    says the account would be created."""
    inst, acc = _seed_instrument(), _seed_account()
    pos = _pre_lots_position(inst, acc)

    res, proposed = call(
        "update_positions",
        position_id=pos["id"],
        changes={"account_name": "Schwabb"},  # typo'd / not-yet-existing venue
    )

    assert res is None
    assert proposed["tool_use_id"]
    assert [a["name"] for a in AccountStore().all()] == ["Vantage Brokerage"], (
        "preview minted an account"
    )
    # The unresolved name rides the change so apply can mint it at confirm time.
    assert proposed["changes"]["account_name"] == "Schwabb"
    assert "account_id" not in proposed["changes"]
    assert proposed["display"]["account_to"] == "Schwabb"
    assert proposed["display"]["account_to_is_new"] is True


def test_apply_position_update_creates_the_proposed_account(isolated):
    """...and confirming that same proposal DOES create it and move the position."""
    inst, acc = _seed_instrument(), _seed_account()
    pos = _pre_lots_position(inst, acc)

    _, proposed = call(
        "update_positions",
        position_id=pos["id"],
        changes={"account_name": "Schwabb"},
    )
    out = pos_svc.apply_position_change({"type": "update", **proposed})

    assert out["ok"] is True
    created = next(a for a in AccountStore().all() if a["name"] == "Schwabb")
    assert created["institution"] == "Schwabb"
    assert PositionStore().find_by_id(pos["id"])["account_id"] == created["id"]


def test_propose_position_update_reuses_an_existing_account_by_name(isolated):
    """An account_name that already matches still resolves to a concrete id at propose
    time (unchanged behavior) and creates nothing."""
    inst, acc = _seed_instrument(), _seed_account()
    target = _seed_account("Schwab")
    pos = _pre_lots_position(inst, acc)

    _, proposed = call(
        "update_positions", position_id=pos["id"], changes={"account_name": "Schwab"}
    )

    assert proposed["changes"]["account_id"] == target["id"]
    assert "account_name" not in proposed["changes"]
    assert "account_to_is_new" not in proposed["display"]
    assert len(AccountStore().all()) == 2


def test_find_account_is_read_only_but_resolve_account_still_creates(isolated):
    """The service split itself: the preview half never writes, the apply half does."""
    _seed_account()
    assert pos_svc.find_account(account_name="Nope") is None
    assert pos_svc.find_account(account_id="acc-nonexistent") is None
    assert len(AccountStore().all()) == 1

    created = pos_svc.resolve_account(account_name="Nope", account_type="investment")
    assert created["institution"] == "Nope"
    assert len(AccountStore().all()) == 2
    # Now the read-only half finds it.
    assert pos_svc.find_account(account_name="Nope")["id"] == created["id"]


# ── Case 2: propose_lot_dispose must not insert an opening lot ───────────────────


def test_propose_lot_dispose_writes_nothing(isolated):
    """Previewing a sale prices the draft off the stored lots — and leaves them untouched
    until the user confirms."""
    inst, acc = _seed_instrument(), _seed_account()
    pos = _pre_lots_position(inst, acc)
    before = LotStore().all()

    res, proposed = call(
        "dispose_lots",
        position_id=pos["id"],
        quantity=4,
        proceeds_per_unit=150.0,
        proceeds_currency="USD",
        disposed_at="2026-07-18",
    )

    assert res is None
    assert proposed["tool_use_id"]
    assert LotStore().all() == before, "the preview wrote a lot"
    # The draft is still faithful: 4 units at 150 against a 100 basis = 200 realized.
    assert proposed["display"]["realized_gain"] == pytest.approx(200.0)
    assert proposed["display"]["cost_basis_known"] is True


def test_apply_lot_dispose_books_the_sale_against_the_opening_lot(isolated):
    """...and confirming that proposal realizes the gain the preview promised."""
    inst, acc = _seed_instrument(), _seed_account()
    pos = _pre_lots_position(inst, acc)

    _, proposed = call(
        "dispose_lots",
        position_id=pos["id"],
        quantity=4,
        proceeds_per_unit=150.0,
        proceeds_currency="USD",
        disposed_at="2026-07-18",
    )
    out = lots_svc.apply_lot_change({**proposed, "type": "dispose"})

    assert out["ok"] is True
    assert out["realized_gain"] == pytest.approx(200.0)
    lots = LotStore().lots_for_position(pos["id"], include_closed=True)
    assert len(lots) == 1 and lots[0]["kind"] == "opening"
    assert lots[0]["remaining_qty"] == pytest.approx(6.0)


def test_propose_lot_dispose_oversell_still_refused_without_writing(isolated):
    """The preview must still surface an oversell — and still not seed a lot on the way."""
    inst, acc = _seed_instrument(), _seed_account()
    pos = _pre_lots_position(inst, acc, quantity=3)
    before = LotStore().all()

    res, proposed = call(
        "dispose_lots", position_id=pos["id"], quantity=99, proceeds_per_unit=150.0
    )

    assert "error" in res and proposed is None
    assert LotStore().all() == before


def test_dispose_double_book_guard_still_fires_on_a_pre_lots_position(isolated):
    """The propose-time duplicate-sale refusal is read-only and survives the change: a
    second identical sale of a pre-lots holding is refused, with no debris either time."""
    inst, acc = _seed_instrument(), _seed_account()
    pos = _pre_lots_position(inst, acc)
    sale = dict(
        position_id=pos["id"],
        quantity=4,
        proceeds_per_unit=150.0,
        proceeds_currency="USD",
        disposed_at="2026-07-18",
    )

    _, proposed = call("dispose_lots", **sale)
    assert lots_svc.apply_lot_change({**proposed, "type": "dispose"})["ok"] is True

    res2, proposed2 = call("dispose_lots", **sale)
    assert "existing_event_id" in res2 and proposed2 is None

    res3, proposed3 = call("dispose_lots", **sale, allow_duplicate=True)
    assert res3 is None and proposed3["allow_duplicate"] is True
