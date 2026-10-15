"""The write-then-verify chip — collection, the differential, and the reload-safe pairing.

The inversion these pin (ADR 2026-07-20): an ordinary ledger write no longer asks
permission, it WRITES, and the reply carries ONE chip per turn saying what landed. So the
properties that make that safe are:

  1. collection is CLOSED by default — no non-chat write path pays for or leaks into a chip;
  2. ONE chip per turn, whatever the row count (a 12-row import is one chip, not twelve);
  3. the chip stays entity-only — counts when collapsed, affected entity chips when expanded;
  4. undo NEVER expires — the chip rides its turn checkpoint, so it survives a reload.
"""

from __future__ import annotations

import pytest

from backend.core import write_receipt as collect
from backend.db import category_store as cat_mod
from backend.services import ledger as svc
from backend.services import write_receipt as receipt


@pytest.fixture
def env(tmp_path, monkeypatch, db_isolated):
    # accounts/transactions/audit/payees are DB-backed now (plan 3 task 4 / plan 4d task 2) —
    # db_isolated redirects them all to a fresh, empty database.
    svc.accounts.insert({"id": "acc_chk", "name": "Checking", "kind": "asset", "currency": "SGD"})
    cats = cat_mod.CategoryStore()
    cats.insert({"id": "cat_meals", "label": "Daily Meals", "kind": "expense"})
    return tmp_path


def _expense(description="Lunch", amount=12.0, category="cat_meals"):
    return {
        "kind": "expense",
        "date": "2026-07-19",
        "description": description,
        "category": category,
        "from_account_id": "acc_chk",
        "from_amount": amount,
        "from_currency": "SGD",
    }


# ── 1. Closed by default ───────────────────────────────────────────────────────


def test_a_write_outside_a_turn_collects_NOTHING(env):
    """The load-bearing default. A REST edit from the register, an import, a migration, a
    test — none of them opened a receipt, so none of them pays for one or bleeds a row into
    somebody else's chip. "The chip shows what THIS turn wrote" is true by construction."""
    svc.create_transaction(_expense())
    assert collect.collected() == []


def test_record_write_is_a_no_op_when_no_receipt_is_open():
    collect.record_write("create", "txn_x")
    assert collect.collected() == []


def test_a_turn_collects_only_its_own_writes(env):
    token = collect.open_receipt()
    try:
        svc.create_transaction(_expense())
        assert [w["op"] for w in collect.collected()] == ["create"]
    finally:
        collect.close_receipt(token)
    # Closed again — the next write outside the turn must not append to a stale buffer.
    svc.create_transaction(_expense(description="After"))
    assert collect.collected() == []


def test_a_nested_turn_does_not_drain_the_outer_turns_receipt(env):
    """Same nesting contract as core.audit_context (CONC-H5): an agent dispatched mid-turn
    recurses into a fresh run(), and its close must restore the OUTER buffer rather than
    null it out from under a turn that is still collecting."""
    outer = collect.open_receipt()
    svc.create_transaction(_expense(description="Outer"))
    inner = collect.open_receipt()
    svc.create_transaction(_expense(description="Inner"))
    assert len(collect.collected()) == 1, "inner turn saw the outer turn's writes"
    collect.close_receipt(inner)
    assert len(collect.collected()) == 1, "outer turn's receipt was drained by the inner close"
    collect.close_receipt(outer)


# ── 2. One chip per turn ───────────────────────────────────────────────────────


def test_a_multi_row_import_produces_ONE_chip_not_one_per_row(env):
    """The count that motivated the design: "a 12-row import is one chip, not twelve".
    Deliberately 15 rows, not 12 — at exactly MAX_ITEMS the sample equals the total and the
    truncation contract below would pass without ever being exercised."""
    token = collect.open_receipt()
    try:
        for i in range(15):
            svc.create_transaction(_expense(description=f"Row {i}", amount=5.0 + i))
        chip = receipt.build_turn_receipt(collect.collected())
    finally:
        collect.close_receipt(token)

    assert chip is not None
    assert chip["total"] == 15
    assert chip["created"] == 15
    # Detail is sampled, the COUNT is exact — a truncated sample must never be mistaken for
    # the whole write (the same contract every bounded read in this codebase reports).
    assert chip["showing"] == len(chip["items"]) == receipt.MAX_ITEMS
    assert chip["total"] > chip["showing"]


def test_a_turn_that_wrote_nothing_has_no_chip(env):
    assert receipt.build_turn_receipt([]) is None


def test_counts_are_split_by_operation(env):
    earlier = svc.create_transaction(_expense())
    token = collect.open_receipt()
    try:
        svc.create_transaction(_expense(description="Dinner"))
        svc.update_transaction(earlier["id"], {"description": "Lunch, edited"})
        chip = receipt.build_turn_receipt(collect.collected())
    finally:
        collect.close_receipt(token)

    assert chip["created"] == 1
    assert chip["updated"] == 1
    assert chip["total"] == 2


def test_editing_a_row_the_turn_just_created_is_still_one_added_row(env):
    """QA 2026-10-14: 23 imported statement rows, labelled in the same pass, read "37
    transactions changed". The user added 23 rows; the labelling is part of adding them."""
    token = collect.open_receipt()
    try:
        ids = [svc.create_transaction(_expense(description=f"Row {i}"))["id"] for i in range(3)]
        for txn_id in ids:
            svc.update_transaction(txn_id, {"description": "Labelled"})
        chip = receipt.build_turn_receipt(collect.collected())
    finally:
        collect.close_receipt(token)

    assert (chip["created"], chip["updated"], chip["total"]) == (3, 0, 3)
    assert chip["groups"] == [{"type": "transaction", "count": 3, "ops": ["create"]}]


# ── 3. Entity-only detail ──────────────────────────────────────────────────────


def test_the_chip_carries_only_entity_identity_and_operation(env):
    """A receipt identifies what changed; it does not repeat transaction details or make
    anomaly judgments. The real drawer remains the source of those details."""
    token = collect.open_receipt()
    try:
        booked = svc.create_transaction(_expense())
        chip = receipt.build_turn_receipt(collect.collected())
    finally:
        collect.close_receipt(token)
    item = chip["items"][0]
    assert item["mention"]["type"] == "transaction"
    assert item["mention"]["id"] == booked["id"]
    assert item["mention"]["label"] == "Lunch"
    assert item["mention"]["sublabel"] == ""
    assert set(item) == {"mention", "op"}
    assert set(chip) == {"created", "updated", "deleted", "total", "showing", "groups", "items"}


# ── 4. Undo never expires — the chip rides its turn checkpoint ─────────────────


def test_the_chip_is_persisted_on_its_turn_checkpoint_and_read_back(tmp_path, monkeypatch):
    """Undo must work tomorrow, not for 15 minutes. The confirm token had a TTL because it
    gated a FUTURE write; undo reverses a PAST one. So the chip is stored ON the checkpoint
    that can reverse it and rebuilt from there on reload — no parallel mechanism, no new
    expiry."""
    from backend.ai import checkpoint as ckpt

    monkeypatch.setattr(ckpt, "CHECKPOINTS_INDEX", tmp_path / "checkpoints.json")
    ctx = ckpt.CheckpointContext("ckpt_1", "sess_1", 4, "add lunch")
    ctx.has_writes = True
    ctx.receipt = {"total": 1, "created": 1, "items": []}
    assert ctx.finalise() is not None

    got = ckpt.turn_receipts("sess_1")
    assert 4 in got, "the chip did not survive being written and read back"
    assert got[4]["total"] == 1
    # The checkpoint id rides the chip so the chip's Undo is a restore of THIS exact
    # checkpoint — the pairing is what makes undo work after a reload.
    assert got[4]["checkpoint_id"] == "ckpt_1"


def test_a_turn_that_wrote_nothing_leaves_no_chip_to_rehydrate(tmp_path, monkeypatch):
    from backend.ai import checkpoint as ckpt

    monkeypatch.setattr(ckpt, "CHECKPOINTS_INDEX", tmp_path / "checkpoints.json")
    ctx = ckpt.CheckpointContext("ckpt_2", "sess_2", 0, "just asking")
    ctx.has_writes = True
    assert ctx.finalise() is not None
    assert ckpt.turn_receipts("sess_2") == {}
