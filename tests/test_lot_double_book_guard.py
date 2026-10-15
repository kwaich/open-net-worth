"""Regression tests for the SPCX double-book incident (2026-06-12): one buy from a
trade-confirmation email was booked twice — add_positions (with acquired_at)
auto-recorded the buy lot, then the agent ALSO called acquire_lots for the same
trade three seconds later. The auto-lot carried no idempotency_key (the position-add
path had no way to accept one), so the broker key on the second call had nothing to
dedupe against → two identical lots, position resynced 1 → 2 shares.

The fix, both halves exercised here:
  1. apply_position_change(add) forwards the caller's idempotency_key onto the
     auto-recorded buy lot, so a redundant acquire_lots with the same key is the
     existing key-dedupe no-op.
  2. _apply_acquire structurally refuses a kind=buy acquire identical to a fully-open
     lot on the same position (same acquired_at, qty, unit_cost) unless the caller
     passes allow_duplicate=true. propose_lot_acquire surfaces the same refusal at
     propose time. Grant/vest lots and partially consumed matches are exempt.

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


def _preview(monkeypatch):
    """propose_position_add/propose_lot_acquire are now ALIASES of add_positions/
    acquire_lots (the 2026-07-19 propose_*/apply_* merge) — the SAME tool previews or
    writes depending on should_preview_write(), not on which name it was called by. Call
    this in a test that wants the PREVIEW branch (a bare test call sets no cost surface,
    which fails OPEN to a direct write)."""
    monkeypatch.setattr(tool_registry, "should_preview_write", lambda: True)


@pytest.fixture
def isolated(tmp_path, monkeypatch, db_isolated):
    # accounts is DB-backed now (plan 3 task 4) — db_isolated (above) covers it.
    monkeypatch.setattr(lots_svc, "_load_rates", lambda: {"USD": 1.0})
    monkeypatch.setattr(lots_svc, "base_currency", lambda: "USD")
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


def _add_position(inst, acc, *, idempotency_key=None):
    return pos_svc.apply_position_change(
        {
            "type": "add",
            "instrument_id": inst["id"],
            "account_id": acc["id"],
            "quantity": 1,
            "cost_basis": 167.48,
            "acquired_at": "2026-06-12",
            "idempotency_key": idempotency_key,
        }
    )


def _acquire_same_trade(position_id, **overrides):
    change = {
        "type": "acquire",
        "position_id": position_id,
        "quantity": 1,
        "unit_cost": 167.48,
        "cost_currency": "USD",
        "acquired_at": "2026-06-12",
        "kind": "buy",
    }
    change.update(overrides)
    return lots_svc.apply_lot_change(change)


def _seed_account():
    return AccountStore().create(
        {"name": "Vantage Brokerage", "kind": "asset", "type": "investment", "currency": "USD"}
    )


# ── Fix 1: position add forwards idempotency_key to the auto-lot ─────────────────


def test_position_add_forwards_idempotency_key_to_auto_lot(isolated):
    inst, acc = _seed_instrument(), _seed_account()
    result = _add_position(inst, acc, idempotency_key="UXXX98317")
    assert result["ok"] is True
    lots = LotStore().lots_for_position(result["position_id"], include_closed=True)
    assert len(lots) == 1
    assert lots[0]["idempotency_key"] == "UXXX98317"


def test_redundant_lot_acquire_with_same_key_is_noop(isolated):
    """The SPCX sequence with the broker key on both calls: the redundant
    acquire_lots dedupes against the auto-lot instead of double-booking."""
    inst, acc = _seed_instrument(), _seed_account()
    added = _add_position(inst, acc, idempotency_key="UXXX98317")
    out = _acquire_same_trade(added["position_id"], idempotency_key="UXXX98317")
    assert out["ok"] is True
    assert out["idempotent"] is True
    assert out["lot_id"] == added["lot_id"]
    assert len(LotStore().lots_for_position(added["position_id"], include_closed=True)) == 1
    assert float(PositionStore().find_by_id(added["position_id"])["quantity"]) == 1.0


# ── Fix 2: identical-lot guard ────────────────────────────────────────────────────


def test_identical_lot_acquire_is_refused_as_double_book(isolated):
    """The SPCX incident verbatim: position add WITHOUT a key (the auto-lot is
    key-less), then an identical acquire arriving with a fresh broker key — the key
    dedupe can't catch it, so the structural guard must."""
    inst, acc = _seed_instrument(), _seed_account()
    added = _add_position(inst, acc)
    out = _acquire_same_trade(added["position_id"], idempotency_key="UXXX98317")
    assert "error" in out
    assert out["existing_lot_id"] == added["lot_id"]
    assert len(LotStore().lots_for_position(added["position_id"], include_closed=True)) == 1
    assert float(PositionStore().find_by_id(added["position_id"])["quantity"]) == 1.0


def test_allow_duplicate_overrides_the_guard(isolated):
    """Two genuinely identical same-day fills: the caller states it explicitly."""
    inst, acc = _seed_instrument(), _seed_account()
    added = _add_position(inst, acc)
    out = _acquire_same_trade(added["position_id"], allow_duplicate=True)
    assert out["ok"] is True
    assert len(LotStore().lots_for_position(added["position_id"], include_closed=True)) == 2
    assert float(PositionStore().find_by_id(added["position_id"])["quantity"]) == 2.0


def test_guard_exempts_grant_lots(isolated):
    """Two same-day vests with identical FMV/qty are legitimate (vest_apply books
    kind=grant lots with their own per-tranche dedupe) — never refused."""
    inst, acc = _seed_instrument(), _seed_account()
    pos = PositionStore().create(
        {
            "instrument_id": inst["id"],
            "account_id": acc["id"],
            "quantity": 0,
            "cost_currency": "USD",
        }
    )
    first = _acquire_same_trade(pos["id"], kind="grant")
    second = _acquire_same_trade(pos["id"], kind="grant")
    assert first["ok"] is True and second["ok"] is True
    assert len(LotStore().lots_for_position(pos["id"], include_closed=True)) == 2


def test_guard_ignores_partially_consumed_match(isolated):
    """A partially consumed identical lot is an older real fill — a rebuy at the same
    price on the same day must not be refused."""
    inst, acc = _seed_instrument(), _seed_account()
    added = _add_position(inst, acc)
    LotStore().bulk_consume([(added["lot_id"], 0.5)])
    out = _acquire_same_trade(added["position_id"])
    assert out["ok"] is True
    assert len(LotStore().lots_for_position(added["position_id"], include_closed=True)) == 2


def test_undated_acquire_skips_the_guard(isolated):
    """No acquired_at → no signature to match on; the guard must not fire."""
    inst, acc = _seed_instrument(), _seed_account()
    added = _add_position(inst, acc)
    out = _acquire_same_trade(added["position_id"], acquired_at="")
    assert out["ok"] is True


# ── Tool layer ────────────────────────────────────────────────────────────────────


def test_propose_position_add_carries_idempotency_key(isolated, monkeypatch):
    _preview(monkeypatch)
    inst, acc = _seed_instrument(), _seed_account()
    res, proposed = call(
        "add_positions",
        instrument_id=inst["id"],
        account_id=acc["id"],
        quantity=1,
        cost_basis=167.48,
        acquired_at="2026-06-12",
        idempotency_key="UXXX98317",
    )
    assert res is None
    assert proposed["idempotency_key"] == "UXXX98317"


def test_propose_lot_acquire_surfaces_double_book_at_propose_time(isolated, monkeypatch):
    _preview(monkeypatch)
    inst, acc = _seed_instrument(), _seed_account()
    added = _add_position(inst, acc)
    res, proposed = call(
        "acquire_lots",
        position_id=added["position_id"],
        quantity=1,
        unit_cost=167.48,
        cost_currency="USD",
        acquired_at="2026-06-12",
    )
    assert "error" in res
    assert res["existing_lot_id"] == added["lot_id"]
    assert proposed is None
    assert len(LotStore().lots_for_position(added["position_id"], include_closed=True)) == 1

    res2, proposed2 = call(
        "acquire_lots",
        position_id=added["position_id"],
        quantity=1,
        unit_cost=167.48,
        cost_currency="USD",
        acquired_at="2026-06-12",
        allow_duplicate=True,
    )
    assert res2 is None
    assert proposed2["allow_duplicate"] is True


# ── Fee-capture sweep 2026-07-13: position add forwards fees to the auto-lot ─────


def test_position_add_forwards_fees_to_auto_lot(isolated):
    inst, acc = _seed_instrument(), _seed_account()
    result = pos_svc.apply_position_change(
        {
            "type": "add",
            "instrument_id": inst["id"],
            "account_id": acc["id"],
            "quantity": 1,
            "cost_basis": 167.48,
            "acquired_at": "2026-06-12",
            "fees": 1.99,
        }
    )
    assert result["ok"] is True
    lots = LotStore().lots_for_position(result["position_id"], include_closed=True)
    assert len(lots) == 1
    assert lots[0]["fees"] == pytest.approx(1.99)


def test_propose_position_add_carries_fees_on_change_and_display(isolated, monkeypatch):
    _preview(monkeypatch)
    inst, acc = _seed_instrument(), _seed_account()
    _, proposed = call(
        "add_positions",
        instrument_id=inst["id"],
        account_id=acc["id"],
        quantity=1,
        cost_basis=167.48,
        acquired_at="2026-06-12",
        fees=1.99,
    )
    assert proposed["fees"] == pytest.approx(1.99)
    assert proposed["display"]["fees"] == pytest.approx(1.99)


def test_propose_lot_acquire_display_shows_fees(isolated, monkeypatch):
    _preview(monkeypatch)
    inst, acc = _seed_instrument(), _seed_account()
    result = _add_position(inst, acc)
    _, proposed = call(
        "acquire_lots",
        position_id=result["position_id"],
        quantity=2,
        unit_cost=170.0,
        cost_currency="USD",
        acquired_at="2026-06-13",
        fees=2.5,
    )
    assert proposed["fees"] == pytest.approx(2.5)
    assert proposed["display"]["fees"] == pytest.approx(2.5)
