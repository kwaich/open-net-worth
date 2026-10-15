"""
Unit tests for CommitmentsStore (Commitments & Funding Planning — RFC Phase 1.1).

Pure, fully isolated: CommitmentsStore is DB-backed (plan 4a task 3) — db_isolated gives
each test its own fresh, migrated database, so these never touch live data (per the "no
destructive tests on live data" rule). No LLM, no network.

Run:
    .venv/bin/pytest tests/test_commitments_store.py -v
"""

from __future__ import annotations

import json

import pytest

from backend.db.commitments_store import CommitmentsStore


@pytest.fixture
def isolated(tmp_path, db_isolated):
    return tmp_path


# ── create: defaults, id, normalization ───────────────────────────────────────


def test_create_defaults_and_slug_id(isolated):
    s = CommitmentsStore()
    c = s.create({"name": "Maple Court Tower", "currency": "vnd"})
    assert c["id"] == "maple-court-tower"  # slugified from name
    assert c["kind"] == "purchase"  # default
    assert c["status"] == "active"  # default
    assert c["currency"] == "VND"  # upper-cased
    assert c["installments"] == [] and c["funding_plan"] == []
    assert c["created_at"]
    assert c["linked_position_id"] is None and c["linked_account_id"] is None


def test_create_explicit_id_wins(isolated):
    s = CommitmentsStore()
    c = s.create({"id": "maple-court", "name": "Whatever"})
    assert c["id"] == "maple-court"


def test_create_dedupes_id(isolated):
    s = CommitmentsStore()
    a = s.create({"name": "Car"})
    b = s.create({"name": "Car"})
    assert a["id"] == "car"
    assert b["id"] == "car-2"
    assert len(s.all()) == 2


def test_create_invalid_kind_and_status_fall_back(isolated):
    s = CommitmentsStore()
    c = s.create({"name": "X", "kind": "nonsense", "status": "bogus"})
    assert c["kind"] == "purchase"
    assert c["status"] == "active"


def test_create_nameless_gets_uuid_id(isolated):
    s = CommitmentsStore()
    c = s.create({})
    assert c["id"] and c["id"] != ""
    assert len(c["id"]) == 8  # uuid hex fallback


# ── installments normalization ────────────────────────────────────────────────


def test_installments_normalized_on_create(isolated):
    s = CommitmentsStore()
    c = s.create(
        {
            "name": "Apt",
            "installments": [
                {"label": "Booking", "due_date": "2026-02-07", "amount": "100", "status": "paid"},
                {"label": "1st", "due_date": "2026-02-10", "amount": 861.4},  # status omitted
                {"label": "bad", "amount": "x", "status": "nope"},  # garbage
            ],
        }
    )
    insts = c["installments"]
    assert insts[0]["amount"] == 100.0 and isinstance(insts[0]["amount"], float)
    assert insts[0]["status"] == "paid"
    assert insts[1]["status"] == "upcoming"  # default when omitted
    assert insts[1]["paid_txn_id"] is None
    assert insts[2]["amount"] == 0.0  # uncoercible → 0
    assert insts[2]["status"] == "upcoming"  # invalid → default
    assert all(i["id"].startswith("inst-") for i in insts)


# ── funding source normalization (the type split) ─────────────────────────────


def test_asset_sale_source_keeps_its_frozen_amount_and_live_inputs(isolated):
    """Regression (2026-08-07): the write path silently dropped asset_sale's typed amount
    (stored None → valued 0.0 by live_source_value) long after the 2026-07-26 owner decision
    made EVERY source store its own frozen amount. The plan must not drift with the market —
    and must not zero out either."""
    s = CommitmentsStore()
    c = s.create(
        {
            "name": "Apt",
            "funding_plan": [
                {
                    "type": "asset_sale",
                    "label": "ONWAI sale",
                    "ref_position_id": "pos-1",
                    "sell_quantity": "1200",
                    "available_date": "2026-08-15",
                    "amount": 99999,
                    "currency": "SGD",
                },
            ],
        }
    )
    fs = c["funding_plan"][0]
    assert fs["type"] == "asset_sale"
    assert fs["amount"] == 99999.0  # the frozen target — kept, exactly like a fixed source
    assert fs["sell_quantity"] == 1200.0 and isinstance(fs["sell_quantity"], float)
    assert fs["ref_position_id"] == "pos-1"
    assert fs["available_date"] == "2026-08-15"
    assert fs["id"].startswith("fs-")


def test_fixed_source_carries_amount_no_sell_quantity(isolated):
    s = CommitmentsStore()
    c = s.create(
        {
            "name": "Apt",
            "funding_plan": [
                {"type": "cash", "label": "Savings earmark", "amount": "22400", "currency": "sgd"},
                {
                    "type": "loan",
                    "label": "Meridian Bank loan",
                    "amount": 200000,
                    "currency": "SGD",
                    "available_date": "2026-09-01",
                    "ref_account_id": "acct-1",
                },
            ],
        }
    )
    cash, loan = c["funding_plan"]
    assert cash["amount"] == 22400.0 and cash["currency"] == "SGD"
    assert cash["sell_quantity"] is None
    assert loan["amount"] == 200000.0 and loan["ref_account_id"] == "acct-1"


def test_invalid_funding_type_falls_back_to_cash(isolated):
    s = CommitmentsStore()
    c = s.create({"name": "X", "funding_plan": [{"type": "bitcoin-magic", "amount": 1}]})
    assert c["funding_plan"][0]["type"] == "cash"


# ── nested writes ──────────────────────────────────────────────────────────────


def test_set_installments_replaces_array(isolated):
    s = CommitmentsStore()
    c = s.create({"name": "Apt", "installments": [{"label": "old", "amount": 1}]})
    updated = s.set_installments(
        c["id"], [{"label": "new1", "amount": 10}, {"label": "new2", "amount": 20}]
    )
    assert [i["label"] for i in updated["installments"]] == ["new1", "new2"]
    assert s.set_installments("ghost", []) is None


def test_update_installment_patches_one_row_only(isolated):
    s = CommitmentsStore()
    c = s.create(
        {
            "name": "Apt",
            "installments": [
                {"id": "inst-a", "label": "A", "amount": 100, "status": "upcoming"},
                {"id": "inst-b", "label": "B", "amount": 200, "status": "upcoming"},
            ],
        }
    )
    updated = s.update_installment(
        c["id"], "inst-b", {"amount": "250", "status": "paid", "paid_txn_id": "txn-9"}
    )
    rows = {i["id"]: i for i in updated["installments"]}
    assert rows["inst-a"]["amount"] == 100 and rows["inst-a"]["status"] == "upcoming"  # untouched
    assert rows["inst-b"]["amount"] == 250.0  # coerced
    assert rows["inst-b"]["status"] == "paid"
    assert rows["inst-b"]["paid_txn_id"] == "txn-9"


def test_update_installment_rejects_bad_status_and_id_change(isolated):
    s = CommitmentsStore()
    c = s.create({"name": "Apt", "installments": [{"id": "inst-a", "amount": 1}]})
    updated = s.update_installment(c["id"], "inst-a", {"status": "garbage", "id": "hacked"})
    inst = updated["installments"][0]
    assert inst["status"] == "upcoming"  # bad status ignored, default kept
    assert inst["id"] == "inst-a"  # id immutable


def test_update_installment_missing_returns_none(isolated):
    s = CommitmentsStore()
    c = s.create({"name": "Apt", "installments": [{"id": "inst-a", "amount": 1}]})
    assert s.update_installment(c["id"], "no-such-inst", {"amount": 5}) is None
    assert s.update_installment("no-such-commitment", "inst-a", {"amount": 5}) is None


def test_update_commitment_whitelists_and_validates(isolated):
    s = CommitmentsStore()
    c = s.create({"name": "Apt", "installments": [{"id": "inst-a", "amount": 1}]})
    updated = s.update_commitment(
        c["id"],
        {
            "name": "Apartment",
            "status": "completed",
            "currency": "sgd",
            "kind": "bogus",  # invalid → dropped
            "installments": [],  # protected → must NOT clobber
            "created_at": "hacked",  # not editable → dropped
        },
    )
    assert updated["name"] == "Apartment"
    assert updated["status"] == "completed"
    assert updated["currency"] == "SGD"
    assert updated["kind"] == "purchase"  # invalid kind ignored, original kept
    assert len(updated["installments"]) == 1  # dedicated endpoint owns this, not PUT
    assert updated["created_at"] == c["created_at"]
    assert s.update_commitment("ghost", {"name": "x"}) is None


def test_set_funding_plan_replaces(isolated):
    s = CommitmentsStore()
    c = s.create({"name": "Apt"})
    updated = s.set_funding_plan(c["id"], [{"type": "loan", "label": "L", "amount": 5}])
    assert updated["funding_plan"][0]["type"] == "loan"
    assert s.set_funding_plan("ghost", []) is None


# ── 2026-07-22 commitments redesign: disabled/cadence, funding_id, missed, loan ─


def test_funding_source_defaults_disabled_false_and_normalizes_cadence(isolated):
    s = CommitmentsStore()
    c = s.create(
        {
            "name": "Apt",
            "funding_plan": [
                {"type": "income", "label": "Set-aside", "amount": 500, "cadence": "monthly"},
                {"type": "cash", "label": "Earmark", "amount": 100},  # cadence omitted
            ],
        }
    )
    income, cash = c["funding_plan"]
    assert income["cadence"] == "monthly"
    assert income["disabled"] is False
    assert cash["cadence"] is None
    assert cash["disabled"] is False


def test_update_funding_source_toggles_disabled(isolated):
    s = CommitmentsStore()
    c = s.create(
        {
            "name": "Apt",
            "funding_plan": [{"id": "fs-1", "type": "loan", "label": "L", "amount": 5000}],
        }
    )
    updated = s.update_funding_source(c["id"], "fs-1", {"disabled": True})
    assert updated["funding_plan"][0]["disabled"] is True
    restored = s.update_funding_source(c["id"], "fs-1", {"disabled": False})
    assert restored["funding_plan"][0]["disabled"] is False
    # id/type stay immutable through this patch path
    unchanged = s.update_funding_source(c["id"], "fs-1", {"id": "hacked", "type": "cash"})
    assert unchanged["funding_plan"][0]["id"] == "fs-1"
    assert unchanged["funding_plan"][0]["type"] == "loan"
    assert s.update_funding_source(c["id"], "no-such-source", {"disabled": True}) is None
    assert s.update_funding_source("ghost", "fs-1", {"disabled": True}) is None


def test_add_funding_sources_appends_without_touching_existing_rows(isolated):
    s = CommitmentsStore()
    c = s.create(
        {
            "name": "Apt",
            "funding_plan": [
                {"id": "fs-1", "type": "loan", "label": "Meridian Bank", "amount": 50000}
            ],
        }
    )
    updated = s.add_funding_sources(
        c["id"],
        [{"type": "cash", "label": "Earmark", "amount": 7000, "currency": "SGD"}],
    )
    plan = updated["funding_plan"]
    assert len(plan) == 2
    assert plan[0]["id"] == "fs-1" and plan[0]["amount"] == 50000.0  # untouched, same id
    assert plan[1]["type"] == "cash" and plan[1]["id"].startswith("fs-")
    assert s.add_funding_sources("ghost", [{"type": "cash", "amount": 1}]) is None


def test_remove_funding_sources_is_all_or_nothing(isolated):
    s = CommitmentsStore()
    c = s.create(
        {
            "name": "Apt",
            "funding_plan": [
                {"id": "fs-1", "type": "loan", "label": "Meridian Bank", "amount": 50000},
                {"id": "fs-2", "type": "cash", "label": "Earmark", "amount": 7000},
                {"id": "fs-3", "type": "windfall", "label": "Bonus", "amount": 25000},
            ],
        }
    )
    updated, unknown = s.remove_funding_sources(c["id"], ["fs-2"])
    assert unknown == []
    assert [f["id"] for f in updated["funding_plan"]] == ["fs-1", "fs-3"]

    # any unknown id → nothing removed, the unknowns named
    failed, unknown = s.remove_funding_sources(c["id"], ["fs-1", "nope"])
    assert failed is None and unknown == ["nope"]
    assert len(s.find_by_id(c["id"])["funding_plan"]) == 2  # fs-1 survived

    missing, unknown = s.remove_funding_sources("ghost", ["fs-1"])
    assert missing is None and unknown == []


def test_add_and_remove_installments_granular(isolated):
    s = CommitmentsStore()
    c = s.create(
        {
            "name": "Apt",
            "installments": [{"id": "inst-1", "label": "Deposit", "amount": 5000}],
        }
    )
    updated = s.add_installments(
        c["id"], [{"label": "Fee", "due_date": "2026-09-01", "amount": 300}]
    )
    rows = updated["installments"]
    assert len(rows) == 2
    assert rows[0]["id"] == "inst-1"  # existing row untouched
    assert rows[1]["label"] == "Fee" and rows[1]["id"].startswith("inst-")

    removed, unknown, blocked = s.remove_installments(c["id"], [rows[1]["id"]])
    assert unknown == [] and blocked == []
    assert [r["id"] for r in removed["installments"]] == ["inst-1"]

    assert s.add_installments("ghost", [{"label": "x"}]) is None
    missing, unknown, blocked = s.remove_installments("ghost", ["inst-1"])
    assert missing is None and unknown == [] and blocked == []


def test_remove_installments_refuses_paid_rows(isolated):
    """A paid installment anchors a real ledger txn (paid_txn_id) — removal must refuse,
    all-or-nothing, so payment history never silently vanishes from the projection."""
    s = CommitmentsStore()
    c = s.create(
        {
            "name": "Apt",
            "installments": [
                {"id": "inst-1", "label": "Deposit", "amount": 5000, "paid_txn_id": "txn-9"},
                {"id": "inst-2", "label": "2nd", "amount": 5000},
            ],
        }
    )
    failed, unknown, blocked = s.remove_installments(c["id"], ["inst-1", "inst-2"])
    assert failed is None and unknown == [] and blocked == ["inst-1"]
    assert len(s.find_by_id(c["id"])["installments"]) == 2  # inst-2 survived too

    # unlink → removal proceeds
    s.update_installment(c["id"], "inst-1", {"paid_txn_id": None})
    removed, unknown, blocked = s.remove_installments(c["id"], ["inst-1"])
    assert unknown == [] and blocked == []
    assert [r["id"] for r in removed["installments"]] == ["inst-2"]


def test_add_and_remove_actions_granular(isolated):
    s = CommitmentsStore()
    c = s.create(
        {
            "name": "Apt",
            "actions": [{"id": "act-1", "label": "Sign contract"}],
        }
    )
    updated = s.add_actions(c["id"], [{"label": "Apply for loan", "due_date": "2026-09-01"}])
    rows = updated["actions"]
    assert len(rows) == 2
    assert rows[0]["id"] == "act-1"  # existing row untouched
    assert rows[1]["label"] == "Apply for loan" and rows[1]["id"].startswith("act-")

    removed, unknown, blocked = s.remove_actions(c["id"], [rows[1]["id"]])
    assert unknown == [] and blocked == []
    assert [r["id"] for r in removed["actions"]] == ["act-1"]

    failed, unknown, blocked = s.remove_actions(c["id"], ["act-ghost"])
    assert failed is None and unknown == ["act-ghost"] and blocked == []


def test_append_always_mints_fresh_ids_never_honors_a_supplied_one(isolated):
    """Review finding (2026-08-07): a caller-supplied id on the append path could collide
    with an existing row's id — duplicate ids corrupt every id-addressed path (a remove of
    that id would drop BOTH rows). Append strips incoming ids; create()/set_* still honor
    them (round-tripping)."""
    s = CommitmentsStore()
    c = s.create({"name": "Apt", "actions": [{"id": "act-1", "label": "Sign"}]})
    updated = s.add_actions(c["id"], [{"id": "act-1", "label": "Duplicate attempt"}])
    ids = [a["id"] for a in updated["actions"]]
    assert len(ids) == 2 and len(set(ids)) == 2  # no collision
    assert ids[0] == "act-1" and ids[1] != "act-1"

    c2 = s.create({"name": "Apt2", "installments": [{"id": "inst-1", "amount": 1}]})
    updated = s.add_installments(c2["id"], [{"id": "inst-1", "amount": 2}])
    ids = [i["id"] for i in updated["installments"]]
    assert len(set(ids)) == 2

    c3 = s.create({"name": "Apt3", "funding_plan": [{"id": "fs-1", "type": "cash", "amount": 1}]})
    updated = s.add_funding_sources(c3["id"], [{"id": "fs-1", "type": "cash", "amount": 2}])
    ids = [f["id"] for f in updated["funding_plan"]]
    assert len(set(ids)) == 2


def test_remove_reports_unknown_and_blocked_together(isolated):
    s = CommitmentsStore()
    c = s.create(
        {
            "name": "Apt",
            "installments": [
                {"id": "inst-1", "label": "Paid", "amount": 1, "paid_txn_id": "txn-9"},
            ],
        }
    )
    failed, unknown, blocked = s.remove_installments(c["id"], ["inst-1", "inst-ghost"])
    assert failed is None
    assert unknown == ["inst-ghost"] and blocked == ["inst-1"]  # both surfaced in ONE call


def test_remove_actions_refuses_live_reminder_link(isolated):
    """An action with reminder_automation_id has a live Automation firing about it —
    removal refuses until the reminder is torn down, else the reminder orphans."""
    s = CommitmentsStore()
    c = s.create(
        {
            "name": "Apt",
            "actions": [
                {"id": "act-1", "label": "Sale window", "reminder_automation_id": "auto-7"},
                {"id": "act-2", "label": "Handover"},
            ],
        }
    )
    failed, unknown, blocked = s.remove_actions(c["id"], ["act-1", "act-2"])
    assert failed is None and unknown == [] and blocked == ["act-1"]
    assert len(s.find_by_id(c["id"])["actions"]) == 2  # all-or-nothing: act-2 survived

    s.update_action(c["id"], "act-1", {"reminder_automation_id": None})
    removed, unknown, blocked = s.remove_actions(c["id"], ["act-1"])
    assert unknown == [] and blocked == []
    assert [r["id"] for r in removed["actions"]] == ["act-2"]


def test_action_funding_id_and_missed_status(isolated):
    s = CommitmentsStore()
    c = s.create(
        {
            "name": "Apt",
            "actions": [
                {
                    "id": "act-1",
                    "label": "Receive windfall",
                    "status": "pending",
                    "funding_id": "fs-1",
                }
            ],
        }
    )
    assert c["actions"][0]["funding_id"] == "fs-1"
    updated = s.update_action(c["id"], "act-1", {"status": "missed"})
    assert updated["actions"][0]["status"] == "missed"
    # reversible
    restored = s.update_action(c["id"], "act-1", {"status": "pending"})
    assert restored["actions"][0]["status"] == "pending"


def test_action_reminder_automation_id_survives_create_and_patch_and_set_actions(isolated):
    """reminder_automation_id is additive (mirrors funding_id's precedent) — must survive both the
    single-row PATCH path (update_action) AND a full-array PUT (set_actions), since
    _make_action's whitelist runs on both and previously would have silently dropped an
    unlisted field on the latter."""
    s = CommitmentsStore()
    c = s.create(
        {
            "name": "Apt",
            "actions": [
                {"id": "act-1", "label": "Sign SPA", "status": "pending"},
            ],
        }
    )
    assert c["actions"][0]["reminder_automation_id"] is None

    updated = s.update_action(c["id"], "act-1", {"reminder_automation_id": "auto-99"})
    assert updated["actions"][0]["reminder_automation_id"] == "auto-99"

    # A subsequent full-array write (the addAction/promote path) must not strip it off the
    # OTHER existing row — _make_action re-derives every row from its own dict.
    actions = updated["actions"] + [{"id": "act-2", "label": "New action", "status": "pending"}]
    resaved = s.set_actions(c["id"], actions)
    assert resaved["actions"][0]["reminder_automation_id"] == "auto-99"
    assert resaved["actions"][1]["reminder_automation_id"] is None

    cleared = s.update_action(c["id"], "act-1", {"reminder_automation_id": None})
    assert cleared["actions"][0]["reminder_automation_id"] is None


def test_loan_stored_on_create_and_via_set_loan(isolated):
    s = CommitmentsStore()
    c = s.create({"name": "Meridian Bank loan", "kind": "loan_repayment"})
    assert c["loan"] is None
    updated = s.set_loan(
        c["id"], {"principal": 50000, "apr": 6.5, "term_months": 24, "payment": "1500.00"}
    )
    assert updated["loan"] == {
        "principal": 50000.0,
        "apr": 6.5,
        "term_months": 24,
        "payment": 1500.00,
    }
    assert s.set_loan("ghost", {"principal": 1}) is None

    created_with_loan = s.create(
        {
            "name": "Other loan",
            "loan": {"principal": 1000, "apr": 0, "term_months": 12, "payment": 83.34},
        }
    )
    assert created_with_loan["loan"]["principal"] == 1000.0


# ── queries ────────────────────────────────────────────────────────────────────


def test_actions_normalized_on_create_and_set(isolated):
    s = CommitmentsStore()
    c = s.create(
        {
            "name": "Apt",
            "actions": [
                {
                    "label": "Apply for loan",
                    "category": "Pre-arrangement",
                    "due_date": "2026-08-15",
                    "start_by": "2026-08-01",
                    "amount": "50000",
                    "currency": "sgd",
                    "status": "Not Started",
                    "counterparty": "Meridian Bank",
                },
            ],
        }
    )
    a = c["actions"][0]
    assert a["status"] == "pending"  # invalid → default
    assert a["amount"] == 50000.0 and a["currency"] == "SGD"
    assert a["counterparty"] == "Meridian Bank" and a["id"].startswith("act-")

    updated = s.set_actions(c["id"], [{"label": "Sign SPA", "due_date": "2026-09-30"}])
    assert len(updated["actions"]) == 1 and updated["actions"][0]["label"] == "Sign SPA"
    assert s.set_actions("ghost", []) is None


def test_update_action_marks_done(isolated):
    s = CommitmentsStore()
    c = s.create({"name": "Apt", "actions": [{"id": "act-1", "label": "x", "status": "pending"}]})
    updated = s.update_action(c["id"], "act-1", {"status": "done", "id": "hacked", "amount": "10"})
    a = updated["actions"][0]
    assert a["status"] == "done" and a["id"] == "act-1" and a["amount"] == 10.0
    assert s.update_action(c["id"], "nope", {"status": "done"}) is None


def test_query_commitments_filters(isolated):
    s = CommitmentsStore()
    s.create({"name": "Apt", "kind": "purchase", "status": "active"})
    s.create({"name": "Meridian Bank loan", "kind": "loan_repayment", "status": "active"})
    s.create({"name": "Old", "kind": "purchase", "status": "completed"})
    assert len(s.query_commitments(kind="purchase")) == 2
    assert len(s.query_commitments(status="active")) == 2
    assert len(s.query_commitments(kind="purchase", status="active")) == 1


def test_get_next_installment_skips_paid_and_orders_by_date(isolated):
    s = CommitmentsStore()
    c = s.create(
        {
            "name": "Apt",
            "installments": [
                {"label": "1st", "due_date": "2026-02-10", "amount": 1, "status": "paid"},
                {"label": "3rd", "due_date": "2026-05-19", "amount": 3, "status": "upcoming"},
                {"label": "2nd", "due_date": "2026-03-20", "amount": 2, "status": "due"},
            ],
        }
    )
    nxt = s.get_next_installment(c["id"])
    assert nxt["label"] == "2nd"  # earliest unpaid by due_date
    assert s.get_next_installment("ghost") is None


def test_get_next_installment_all_paid_returns_none(isolated):
    s = CommitmentsStore()
    c = s.create(
        {
            "name": "Apt",
            "installments": [
                {"label": "1st", "due_date": "2026-02-10", "amount": 1, "status": "paid"},
            ],
        }
    )
    assert s.get_next_installment(c["id"]) is None


# ── base CRUD still works (delete / find) ─────────────────────────────────────


def test_delete_and_find(isolated):
    s = CommitmentsStore()
    c = s.create({"name": "Apt"})
    assert s.find_by_id(c["id"]) is not None
    assert s.delete(c["id"]) is True
    assert s.find_by_id(c["id"]) is None
    assert s.all() == []


# ── funding sources gain execution state (2026-08-23) ─────────────────────────


def test_funding_source_defaults_to_planned_with_no_ledger_anchor(isolated):
    s = CommitmentsStore()
    c = s.create({"name": "Apt", "funding_plan": [{"type": "income", "label": "Set-aside"}]})
    src = c["funding_plan"][0]
    assert src["status"] == "planned"
    assert src["realized_txn_id"] is None


def test_funding_source_normalizes_an_unknown_status_to_planned(isolated):
    s = CommitmentsStore()
    c = s.create({"name": "Apt", "funding_plan": [{"type": "cash", "status": "banana"}]})
    assert c["funding_plan"][0]["status"] == "planned"


def test_update_funding_source_marks_it_done_with_a_ledger_anchor(isolated):
    s = CommitmentsStore()
    c = s.create(
        {"name": "Apt", "funding_plan": [{"id": "fs-1", "type": "income", "amount": 3000}]}
    )
    updated = s.update_funding_source(c["id"], "fs-1", {"status": "done", "realized_txn_id": "t1"})
    assert updated["funding_plan"][0]["status"] == "done"
    assert updated["funding_plan"][0]["realized_txn_id"] == "t1"


def test_update_funding_source_refuses_an_unknown_status(isolated):
    from backend.core.errors import ValidationError

    s = CommitmentsStore()
    c = s.create({"name": "Apt", "funding_plan": [{"id": "fs-1", "type": "cash"}]})
    with pytest.raises(ValidationError):
        s.update_funding_source(c["id"], "fs-1", {"status": "banana"})


def test_update_funding_source_refuses_an_empty_currency(isolated):
    """Regression: an empty-string currency used to pass straight through — no
    rejection, no coercion — and land silently on the stored funding source."""
    from backend.core.errors import ValidationError

    s = CommitmentsStore()
    c = s.create(
        {
            "name": "Apt",
            "funding_plan": [{"id": "fs-1", "type": "cash", "currency": "SGD"}],
        }
    )
    with pytest.raises(ValidationError):
        s.update_funding_source(c["id"], "fs-1", {"currency": ""})
    # refused in place — the prior valid currency is untouched
    unchanged = s.find_by_id(c["id"])
    assert unchanged["funding_plan"][0]["currency"] == "SGD"


def test_update_funding_source_refuses_an_unknown_currency_shape(isolated):
    from backend.core.errors import ValidationError

    s = CommitmentsStore()
    c = s.create({"name": "Apt", "funding_plan": [{"id": "fs-1", "type": "cash"}]})
    with pytest.raises(ValidationError):
        s.update_funding_source(c["id"], "fs-1", {"currency": "dollars"})


def test_update_funding_source_lowercases_and_trims_a_valid_currency(isolated):
    s = CommitmentsStore()
    c = s.create({"name": "Apt", "funding_plan": [{"id": "fs-1", "type": "cash"}]})
    updated = s.update_funding_source(c["id"], "fs-1", {"currency": " vnd "})
    assert updated["funding_plan"][0]["currency"] == "VND"


def test_a_legacy_funding_row_without_status_hydrates_as_planned(isolated):
    """The a production book holds 41 funding rows written before this field existed. They
    must read as `planned`, not as a missing key every consumer has to defend against."""
    from sqlalchemy import update as sa_update

    from backend.db.tables import commitments as commitments_table

    s = CommitmentsStore()
    c = s.create({"name": "Apt", "funding_plan": [{"id": "fs-1", "type": "cash", "amount": 10}]})
    legacy = json.loads(json.dumps(c))
    legacy["funding_plan"][0].pop("status")
    legacy["funding_plan"][0].pop("realized_txn_id")
    with s._eng().begin() as conn:
        conn.execute(
            sa_update(commitments_table)
            .where(commitments_table.c.id == c["id"])
            .values(payload=legacy)
        )
    src = s.find_by_id(c["id"])["funding_plan"][0]
    assert src["status"] == "planned"
    assert src["realized_txn_id"] is None
    assert s.all()[0]["funding_plan"][0]["status"] == "planned"
