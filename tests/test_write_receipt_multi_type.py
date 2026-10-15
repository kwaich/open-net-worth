"""Generalizing the write-receipt chip beyond transactions.

The chip started ledger-only. It now reports ANY entity type a turn wrote, and every row is
a real ``MentionRow`` so one chip renderer serves both the receipt and the ``@``-picker.

What these pin — in the order the properties matter:

  1. the TRANSACTION path keeps its default entity type while Automation provenance remains
     domain metadata rather than receipt display content;
  2. collection stays CLOSED BY DEFAULT for every type, not just transactions — that is
     what makes "the chip shows what THIS turn wrote" true by construction;
  3. every ``entity_type`` any choke actually records IS a registered mention type, so a
     new domain cannot half-land with rows that have no chip renderer;
  4. a cascading choke records ONLY the entity it is the choke for — a vest books an income
     txn AND a lot, and each reports itself, so recording them twice would double-count;
  5. every store backing an in-scope type is checkpoint-INCLUDED, or the receipt would be
     built and then silently thrown away by ``finalise``'s ``has_writes`` gate.
"""

from __future__ import annotations

import pytest

from backend.core import write_receipt as collect
from backend.db import category_store as cat_mod
from backend.services import ledger as svc
from backend.services import write_receipt as receipt
from backend.services.mentions.registry import DESCRIPTORS


@pytest.fixture
def env(tmp_path, monkeypatch, db_isolated):
    # accounts/transactions/audit are DB-backed now (plan 3 task 4), and PayeeStore is
    # DB-backed too (plan 4d task 2) — db_isolated redirects all of them to a fresh,
    # empty database; no more per-store _path to repoint.
    monkeypatch.setattr(svc, "base_currency", lambda: "SGD")
    svc.accounts.insert({"id": "acc_chk", "name": "Checking", "kind": "asset", "currency": "SGD"})
    cat_mod.CategoryStore().insert({"id": "cat_meals", "label": "Daily Meals", "kind": "expense"})
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


# ── 1. The transaction path is unchanged ───────────────────────────────────────


def test_the_audit_choke_still_records_a_transaction_with_NO_argument_changes(env):
    """The default ``entity_type`` keeps ordinary ledger receipt rows unchanged."""
    token = collect.open_receipt()
    try:
        booked = svc.create_transaction(_expense())
        recorded = collect.collected()
    finally:
        collect.close_receipt(token)

    assert recorded == [{"type": "transaction", "op": "create", "id": booked["id"], "meta": None}]


def test_automation_applied_arrives_as_provenance_but_not_receipt_content(env):
    """Entity-only receipts ignore Automation-specific provenance metadata."""
    token = collect.open_receipt()
    try:
        booked = svc.create_transaction(_expense())
        collect.record_write(
            "update",
            booked["id"],
            automation_applied={"category_from": None, "category_to": "cat_meals"},
        )
        recorded = collect.collected()
        chip = receipt.build_turn_receipt(recorded)
    finally:
        collect.close_receipt(token)

    assert recorded[-1]["meta"] == {
        "automation_applied": {"category_from": None, "category_to": "cat_meals"}
    }
    assert set(chip["items"][-1]) == {"mention", "op"}


# ── 2. Closed by default, for EVERY type ───────────────────────────────────────


@pytest.mark.parametrize(
    "entity_type", ["transaction", "envelope", "lot", "vest", "automation", "skill", "alert"]
)
def test_recording_any_type_outside_a_turn_collects_nothing(entity_type):
    """With ten chokes reporting instead of one, an always-on buffer would collect during
    every bank sync and every boot migration. Closed-by-default is MORE load-bearing after
    this change, not less."""
    collect.record_write("create", "id_1", entity_type=entity_type)
    assert collect.collected() == []


def test_an_unregistered_entity_type_is_dropped_rather_than_rendered_blank():
    """A row with no chip renderer is worse than no row."""
    token = collect.open_receipt()
    try:
        collect.record_write("create", "x_1", entity_type="not_a_real_type")
        chip = receipt.build_turn_receipt(collect.collected())
    finally:
        collect.close_receipt(token)
    assert chip is None or all(i["mention"]["type"] != "not_a_real_type" for i in chip["items"])


def test_record_write_never_raises_on_a_hostile_argument():
    """A mechanism that only DESCRIBES a write must not be able to fail it."""
    token = collect.open_receipt()
    try:
        collect.record_write("create", None, entity_type=None)  # type: ignore[arg-type]
        collect.record_write("create", "", entity_type="lot")
        assert collect.collected() == []
    finally:
        collect.close_receipt(token)


# ── 3. Every recorded type is a registered mention type ────────────────────────

# Every (entity_type) any production choke passes to record_write. Kept as an explicit
# table rather than grepped, so ADDING a domain is a deliberate edit here — which is the
# moment to notice it needs a chip renderer, a checkpoint inclusion and a route.
RECORDED_ENTITY_TYPES = frozenset(
    {
        "transaction",  # services/audit.py (create/update/delete)
        "lot",  # services/lots.py acquire | delete
        "disposal",  # services/lots.py dispose | dispose_undo
        "grant",  # services/equity.py grant_create | grant_update | grant_cancel
        "vest",  # services/equity.py vest_apply | vest_undo
        "income",  # services/income.py record | delete
        "automation",  # services/automations/service.py apply_automation_change
        "alert",  # services/alerts.py apply_alert_change
        "envelope",  # services/planning.py envelope + assign/move/target/auto_assign
        "category",  # services/planning.py category_create | rename | delete
        "skill",  # ai/tools/skills.py apply_skill_change
    }
)


def test_every_recorded_entity_type_is_a_registered_mention_type():
    """Asserted at import time rather than discovered on screen: an ``entity_type`` with no
    descriptor has no label, no icon and no chip — the row would render blank."""
    unregistered = sorted(RECORDED_ENTITY_TYPES - set(DESCRIPTORS))
    assert unregistered == [], f"no mention descriptor for: {unregistered}"


def test_every_recorded_type_can_be_hydrated_into_a_row_without_raising():
    """``receipt_rows`` must degrade a missing entity to a STALE row, never raise — a
    labelling failure must not be able to hide writes that already committed."""
    from backend.services.mentions.labels import receipt_rows

    rows = receipt_rows([(t, "does_not_exist") for t in sorted(RECORDED_ENTITY_TYPES)])
    assert len(rows) == len(RECORDED_ENTITY_TYPES)
    assert all(r["stale"] is True for r in rows)
    # An unresolvable id attaches NOTHING rather than "" — absent means "no label
    # available"; a fabricated name would be a wrong claim about a name.
    assert all(r["label"] == "" for r in rows)


def test_no_two_descriptors_share_an_order():
    """``disposal`` and ``envelope`` both sat at order 25, so their head-group tie-break fell
    back to module import order — exactly the instability ``order`` exists to remove."""
    orders = [d.order for d in DESCRIPTORS.values()]
    assert len(orders) == len(set(orders)), "duplicate descriptor.order"


# ── 4. Cascades are recorded once, by the choke that owns the entity ───────────


def test_a_choke_records_only_the_entity_it_is_the_choke_for(env, monkeypatch):
    """``apply_lot_change(dispose)`` books a cash transaction leg. That leg funnels through
    the ledger's own audit choke and reports ITSELF — so the lot choke must not report it
    too, or one economic event counts twice in the head."""
    from backend.services import lots as lots_service

    monkeypatch.setattr(
        lots_service, "_apply_dispose", lambda change: {"ok": True, "event_id": "ev_1"}
    )
    token = collect.open_receipt()
    try:
        lots_service.apply_lot_change({"type": "dispose"})
        recorded = collect.collected()
    finally:
        collect.close_receipt(token)

    assert recorded == [
        {"type": "disposal", "op": "create", "id": "ev_1", "meta": {"verb": "dispose"}}
    ]


def test_a_failed_choke_records_nothing(env, monkeypatch):
    """An op that returned an error wrote nothing, so the chip must claim nothing."""
    from backend.services import lots as lots_service

    monkeypatch.setattr(lots_service, "_apply_acquire", lambda change: {"error": "nope"})
    token = collect.open_receipt()
    try:
        lots_service.apply_lot_change({"type": "acquire"})
        assert collect.collected() == []
    finally:
        collect.close_receipt(token)


def test_repeat_records_of_the_same_entity_collapse_to_one(env):
    """An auto-assign then a correction touches one envelope twice; "40 envelopes edited"
    must not become 80. This also makes the ``match_review`` provenance record safe — it
    deliberately re-records a row the audit choke already reported, to attach ``verb=merge``.
    """
    token = collect.open_receipt()
    try:
        booked = svc.create_transaction(_expense())
        collect.record_write("update", booked["id"], meta={"verb": "merge", "absorbed_id": "t2"})
        collect.record_write("update", booked["id"], meta={"note": "second"})
        chip = receipt.build_turn_receipt(collect.collected())
    finally:
        collect.close_receipt(token)

    assert chip["total"] == 1, "the turn's own create, with both edits folded into it"
    assert (chip["created"], chip["updated"]) == (1, 0)
    assert chip["groups"] == [{"type": "transaction", "count": 1, "ops": ["create"]}]


# ── 5. Multi-type shape ────────────────────────────────────────────────────────


def test_a_mixed_turn_is_ONE_chip_with_per_type_groups(env):
    """A 12-row import plus an envelope assignment is ONE chip — and its head can now say
    WHICH types changed, which a bare "13 changed" never could."""
    token = collect.open_receipt()
    try:
        for i in range(3):
            svc.create_transaction(_expense(description=f"Row {i}"))
        collect.record_write(
            "update", "env_1", entity_type="envelope", meta={"verb": "assign", "month": "2026-07"}
        )
        chip = receipt.build_turn_receipt(collect.collected())
    finally:
        collect.close_receipt(token)

    assert chip["total"] == 4
    assert chip["groups"] == [
        {"type": "transaction", "count": 3, "ops": ["create"]},
        {"type": "envelope", "count": 1, "ops": ["update"]},
    ]
    # The composite (month, envelope_id) is NEVER faked into an entity id. Receipt detail is
    # entity-only, so the month remains write provenance and is not rendered.
    env_item = next(i for i in chip["items"] if i["mention"]["type"] == "envelope")
    assert env_item["mention"]["id"] == "env_1"
    assert env_item["mention"]["sublabel"] == ""


def test_a_rare_type_is_never_crowded_out_of_the_sample(env):
    """The one envelope row beside a 20-row import is exactly the row most worth seeing —
    at least one row per type is guaranteed before the sample fills by order."""
    token = collect.open_receipt()
    try:
        for i in range(20):
            svc.create_transaction(_expense(description=f"Row {i}"))
        collect.record_write("create", "lot_1", entity_type="lot")
        chip = receipt.build_turn_receipt(collect.collected())
    finally:
        collect.close_receipt(token)

    assert chip["showing"] == receipt.MAX_ITEMS
    assert any(i["mention"]["type"] == "lot" for i in chip["items"])


def test_every_entity_type_uses_the_same_entity_only_item_shape(env):
    token = collect.open_receipt()
    try:
        collect.record_write("create", "lot_1", entity_type="lot")
        collect.record_write("update", "env_1", entity_type="envelope")
        chip = receipt.build_turn_receipt(collect.collected())
    finally:
        collect.close_receipt(token)
    assert all(set(i) == {"mention", "op"} for i in chip["items"])


def test_an_envelope_only_turn_does_not_read_the_ledger(env, monkeypatch):
    """A turn that wrote no transactions must not pay for a full ledger scan just to build
    a chip — the ledger context is resolved lazily, only when a transaction row is present."""
    called = {"n": 0}

    def _boom():
        called["n"] += 1
        raise AssertionError("the ledger was read for a turn with no transaction rows")

    monkeypatch.setattr(svc.transactions, "all", _boom)
    token = collect.open_receipt()
    try:
        collect.record_write("update", "env_1", entity_type="envelope")
        chip = receipt.build_turn_receipt(collect.collected())
    finally:
        collect.close_receipt(token)
    assert chip["total"] == 1
    assert called["n"] == 0


def test_the_overflow_cap_keeps_the_total_honest():
    """Past MAX_COLLECTED the receipt stops collecting DETAIL but keeps COUNTING, and the
    overflow entry keeps its TYPE so a capped mixed turn can still be described honestly."""
    token = collect.open_receipt()
    try:
        for i in range(collect.MAX_COLLECTED + 5):
            collect.record_write("create", f"lot_{i}", entity_type="lot")
        recorded = collect.collected()
        chip = receipt.build_turn_receipt(recorded)
    finally:
        collect.close_receipt(token)

    assert len(recorded) == collect.MAX_COLLECTED + 5
    assert recorded[-1] == {"type": "lot", "op": "create", "id": "", "meta": None}
    assert chip["total"] == collect.MAX_COLLECTED + 5


# ── 6. Checkpoint inclusion — or the receipt is silently thrown away ───────────


def test_every_in_scope_stores_file_is_checkpoint_INCLUDED():
    """``finalise()`` drops the receipt entirely when ``has_writes`` is false — i.e. when no
    NON-EXCLUDED file was snapshotted. An in-scope domain whose store is excluded would
    therefore produce a receipt and then have it thrown away, silently. This is the test the
    design called for as a ship blocker, not a nicety."""
    from backend.ai.checkpoint import _EXCLUDED_PATHS
    from backend.core.paths import (
        ALERTS_JSON,
        AUTOMATIONS_JSON,
        CATEGORIES_JSON,
        ENVELOPES_JSON,
        GRANTS_JSON,
        INCOME_EVENTS_JSON,
        LOTS_JSON,
        REALIZED_EVENTS_JSON,
        SKILLS_STATE_JSON,
        TRANSACTIONS_JSON,
        VEST_EVENTS_JSON,
    )

    backing = {
        "transaction": TRANSACTIONS_JSON,
        "lot": LOTS_JSON,
        "disposal": REALIZED_EVENTS_JSON,
        "grant": GRANTS_JSON,
        "vest": VEST_EVENTS_JSON,
        "income": INCOME_EVENTS_JSON,
        "automation": AUTOMATIONS_JSON,
        "alert": ALERTS_JSON,
        "envelope": ENVELOPES_JSON,
        "category": CATEGORIES_JSON,
        "skill": SKILLS_STATE_JSON,
    }
    # The table must cover every type a choke records — otherwise a new domain could be
    # added above and quietly skip this check.
    assert set(backing) == set(RECORDED_ENTITY_TYPES)
    excluded = sorted(t for t, p in backing.items() if p.resolve() in _EXCLUDED_PATHS)
    assert excluded == [], f"receipt would be discarded for: {excluded}"
