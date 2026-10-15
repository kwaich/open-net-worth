"""
AI tool-dispatch tests for commitments (RFC Phase 1.4).

Offline + isolated: CommitmentsStore is DB-backed (plan 4a task 3) — db_isolated gives
each test its own fresh, migrated database; execute_tool constructs CommitmentsStore() per
call, so the isolation takes effect transparently. execute_tool is async, driven here via
asyncio.run to avoid any asyncio-plugin mode dependency. No LLM, no network.

Run:
    .venv/bin/pytest tests/test_commitments_tools.py -v
"""

from __future__ import annotations

import asyncio
import json

import pytest

from backend.ai.tools import TOOL_SCHEMAS, execute_tool

# A renamed tool name no longer resolves at runtime (ADR 2026-08-25-tool-names-are-final) —
# the migration's rename table is the only record that a rename happened.


@pytest.fixture
def isolated(tmp_path, db_isolated):
    _seed_ledger_txns("txn-1", "txn-2", "txn-7", "txn-9")
    return tmp_path


def _seed_ledger_txns(*txn_ids):
    """Installments may only link a transaction that is really in the ledger (2026-10-13)."""
    from backend.db.transaction_store import TransactionStore

    for txn_id in txn_ids:
        TransactionStore().insert(
            {
                "id": txn_id,
                "date": "2026-06-01",
                "kind": "expense",
                "description": f"payment {txn_id}",
                "source": "manual",
            }
        )


def call(tool, **inp):
    text, proposed = asyncio.run(execute_tool(tool, inp))
    return json.loads(text), proposed


def test_all_six_tools_registered():
    names = {t["name"] for t in TOOL_SCHEMAS}
    assert {
        "get_commitments",
        "create_commitment",
        "set_commitment_installments",
        "update_commitment_installments",
        "set_funding_plan",
        "delete_commitment",
    } <= names


def test_former_singular_names_are_not_offered_directly():
    """Aliases stay out of the offered schemas (zero prompt cost, one name per tool) — the
    2026-07-19b arity-naming fixup (owner incident, "why is delete_account still singular /
    account_id even though it's a list?")."""
    names = {t["name"] for t in TOOL_SCHEMAS}
    formerly = {"update_commitment", "update_commitment_action", "update_commitment_installment"}
    assert not (formerly & names)


@pytest.mark.parametrize(
    ("old_name", "new_name"),
    [
        ("update_commitment", "update_commitments"),
        ("update_commitment_action", "update_commitment_actions"),
        ("update_commitment_installment", "update_commitment_installments"),
    ],
)
def test_old_name_resolves_to_new_tool(old_name, new_name):
    from backend.ai.tools import registry

    assert new_name in registry.registered_tool_names()


def test_a_legacy_singular_name_still_dispatches(isolated):
    """The exact outage this convention exists to prevent: a stored playbook calling a tool
    by its former name must still work, not silently vanish."""
    cid = call("create_commitment", name="Apt")[0]["commitment"]["id"]
    res, proposed = call("update_commitments", commitment_id=cid, note="via legacy name")
    assert proposed is None
    assert res["ok"] is True
    assert res["commitment"]["note"] == "via legacy name"


def test_create_and_get(isolated):
    res, proposed = call(
        "create_commitment",
        name="Renovation",
        currency="sgd",
        kind="purchase",
        installments=[{"label": "Deposit", "due_date": "2026-07-01", "amount": 5000}],
    )
    assert res["ok"] and proposed is None  # direct write, not a proposed-change tool
    cid = res["commitment"]["id"]
    assert res["commitment"]["currency"] == "SGD"

    result, _ = call("get_commitments")
    assert result["showing"] == 1 and result["total"] == 1
    assert len(result["commitments"]) == 1 and result["commitments"][0]["id"] == cid


def test_get_filters(isolated):
    call("create_commitment", name="Apt", kind="purchase")
    call("create_commitment", name="Meridian Bank", kind="loan_repayment")
    result, _ = call("get_commitments", kind="purchase")
    purchases = result["commitments"]
    assert len(purchases) == 1 and purchases[0]["name"] == "Apt"
    assert result["showing"] == 1 and result["total"] == 2


def test_get_query_searches_name_and_note(isolated):
    call("create_commitment", name="Bangkok condo down payment", kind="purchase")
    call(
        "create_commitment", name="Meridian Bank loan", kind="loan_repayment", note="condo related"
    )
    call("create_commitment", name="School fees", kind="goal")

    result, _ = call("get_commitments", query="condo")
    names = {c["name"] for c in result["commitments"]}
    assert names == {"Bangkok condo down payment", "Meridian Bank loan"}
    assert result["showing"] == 2 and result["total"] == 3


def test_get_query_no_match_reports_total_not_empty_book(isolated):
    call("create_commitment", name="Apt", kind="purchase")
    result, _ = call("get_commitments", query="nonexistent")
    assert result["commitments"] == []
    assert result["showing"] == 0 and result["total"] == 1
    assert "0 of 1" in result["note"]


def test_get_empty_book_reports_no_commitments(isolated):
    result, _ = call("get_commitments")
    assert result["commitments"] == []
    assert result["showing"] == 0 and result["total"] == 0
    assert "no commitments" in result["note"]


def test_get_offset_past_matched_end_does_not_blame_the_filter(isolated):
    """Regression: offset overshooting a REAL match set must not be conflated with a filter
    that excluded everything — the earlier note text told the model to 'drop or loosen the
    filter' even when the filter matched fine and only the offset was wrong."""
    call("create_commitment", name="Apt", kind="purchase")
    call("create_commitment", name="Meridian Bank", kind="loan_repayment")

    result, _ = call("get_commitments", kind="purchase", offset=100)
    assert result["commitments"] == []
    assert result["showing"] == 0 and result["total"] == 2
    assert "offset=100" in result["note"]
    assert "past the end" in result["note"]
    assert "drop or loosen the filter" not in result["note"].lower()


def test_get_limit_and_offset_page_through_results(isolated):
    for i in range(5):
        call("create_commitment", name=f"C{i}", kind="goal")

    first, _ = call("get_commitments", limit=2)
    assert first["showing"] == 2 and first["total"] == 5
    assert "offset=2" in first["note"]

    second, _ = call("get_commitments", limit=2, offset=2)
    assert second["showing"] == 2
    assert "offset=4" in second["note"]

    third, _ = call("get_commitments", limit=2, offset=4)
    assert third["showing"] == 1
    assert "note" not in third

    seen_ids = {c["id"] for page in (first, second, third) for c in page["commitments"]}
    assert len(seen_ids) == 5


def test_set_installments_and_update_one_row(isolated):
    cid = call("create_commitment", name="Apt")[0]["commitment"]["id"]
    res, _ = call(
        "set_commitment_installments",
        commitment_id=cid,
        installments=[
            {"id": "i1", "label": "A", "amount": 100},
            {"id": "i2", "label": "B", "amount": 200},
        ],
    )
    assert res["ok"] and len(res["commitment"]["installments"]) == 2

    res, _ = call(
        "update_commitment_installments",
        commitment_id=cid,
        installment_id="i2",
        changes={"status": "paid", "paid_txn_id": "txn-7", "amount": 250},
    )
    rows = {i["id"]: i for i in res["commitment"]["installments"]}
    assert rows["i2"]["status"] == "paid" and rows["i2"]["paid_txn_id"] == "txn-7"
    assert rows["i2"]["amount"] == 250
    assert rows["i1"]["amount"] == 100  # untouched

    # missing installment → ok False, not a crash
    res, _ = call(
        "update_commitment_installments",
        commitment_id=cid,
        installment_id="nope",
        changes={"amount": 1},
    )
    assert res["ok"] is False


def test_set_funding_plan_asset_sale_keeps_frozen_amount(isolated):
    """Regression (2026-08-07): asset_sale's typed amount used to be dropped at the write
    choke — stored None, valued 0.0 — contradicting the 2026-07-26 frozen-plan decision."""
    cid = call("create_commitment", name="Apt")[0]["commitment"]["id"]
    res, _ = call(
        "set_funding_plan",
        commitment_id=cid,
        funding_plan=[
            {
                "type": "asset_sale",
                "label": "ONWAI",
                "ref_position_id": "p1",
                "sell_quantity": 1000,
                "available_date": "2026-08-01",
                "amount": 99999,
                "currency": "SGD",
            },
            {"type": "loan", "label": "Meridian Bank", "amount": 200000, "currency": "SGD"},
        ],
    )
    fs = res["commitment"]["funding_plan"]
    assert fs[0]["amount"] == 99999.0 and fs[0]["sell_quantity"] == 1000.0
    assert fs[1]["amount"] == 200000.0


def test_granular_child_collection_tools_registered():
    names = {t["name"] for t in TOOL_SCHEMAS}
    assert {
        "add_funding_sources",
        "remove_funding_sources",
        "add_commitment_installments",
        "remove_commitment_installments",
        "add_commitment_actions",
        "remove_commitment_actions",
    } <= names


def test_add_funding_sources_appends_and_keeps_existing_ids(isolated):
    cid = call("create_commitment", name="Apt")[0]["commitment"]["id"]
    first = call(
        "set_funding_plan",
        commitment_id=cid,
        funding_plan=[
            {"type": "loan", "label": "Meridian Bank", "amount": 50000, "currency": "SGD"}
        ],
    )[0]["commitment"]["funding_plan"]
    existing_id = first[0]["id"]

    res, proposed = call(
        "add_funding_sources",
        commitment_id=cid,
        sources=[{"type": "windfall", "label": "Bonus", "amount": 25000, "currency": "SGD"}],
    )
    assert proposed is None and res["ok"] is True
    plan = res["commitment"]["funding_plan"]
    assert len(plan) == 2
    assert plan[0]["id"] == existing_id  # the point of the tool: existing rows untouched
    assert plan[1]["label"] == "Bonus" and plan[1]["amount"] == 25000.0


def test_add_funding_sources_requires_sources(isolated):
    cid = call("create_commitment", name="Apt")[0]["commitment"]["id"]
    res, _ = call("add_funding_sources", commitment_id=cid, sources=[])
    assert res["ok"] is False and "required" in res["error"]


def test_remove_funding_sources_removes_only_the_named_ids(isolated):
    cid = call("create_commitment", name="Apt")[0]["commitment"]["id"]
    plan = call(
        "set_funding_plan",
        commitment_id=cid,
        funding_plan=[
            {"type": "loan", "label": "Meridian Bank", "amount": 50000, "currency": "SGD"},
            {"type": "cash", "label": "Earmark", "amount": 7000, "currency": "SGD"},
        ],
    )[0]["commitment"]["funding_plan"]

    res, proposed = call("remove_funding_sources", commitment_id=cid, funding_ids=[plan[1]["id"]])
    assert proposed is None and res["ok"] is True
    kept = res["commitment"]["funding_plan"]
    assert [f["id"] for f in kept] == [plan[0]["id"]]


def test_remove_funding_sources_unknown_id_fails_whole_call_and_names_it(isolated):
    cid = call("create_commitment", name="Apt")[0]["commitment"]["id"]
    plan = call(
        "set_funding_plan",
        commitment_id=cid,
        funding_plan=[
            {"type": "loan", "label": "Meridian Bank", "amount": 50000, "currency": "SGD"}
        ],
    )[0]["commitment"]["funding_plan"]

    res, _ = call(
        "remove_funding_sources", commitment_id=cid, funding_ids=[plan[0]["id"], "fs-ghost"]
    )
    assert res["ok"] is False
    assert "fs-ghost" in res["error"] and "get_commitments" in res["error"]
    # all-or-nothing: the known id survived too
    still = call("get_commitments")[0]["commitments"][0]["funding_plan"]
    assert [f["id"] for f in still] == [plan[0]["id"]]


def test_remove_funding_sources_unknown_commitment(isolated):
    res, _ = call("remove_funding_sources", commitment_id="ghost", funding_ids=["fs-1"])
    assert res["ok"] is False and "ghost" in res["error"]


def test_add_and_remove_commitment_installments_tools(isolated):
    cid = call(
        "create_commitment",
        name="Apt",
        installments=[{"label": "Deposit", "due_date": "2026-07-01", "amount": 5000}],
    )[0]["commitment"]["id"]
    existing_id = call("get_commitments")[0]["commitments"][0]["installments"][0]["id"]

    res, proposed = call(
        "add_commitment_installments",
        commitment_id=cid,
        installments=[{"label": "Fee", "due_date": "2026-09-01", "amount": 300}],
    )
    assert proposed is None and res["ok"] is True
    rows = res["commitment"]["installments"]
    assert len(rows) == 2 and rows[0]["id"] == existing_id

    res, proposed = call(
        "remove_commitment_installments", commitment_id=cid, installment_ids=[rows[1]["id"]]
    )
    assert proposed is None and res["ok"] is True
    assert [r["id"] for r in res["commitment"]["installments"]] == [existing_id]


def test_remove_commitment_installments_refuses_paid_and_names_the_fix(isolated):
    cid = call("create_commitment", name="Apt")[0]["commitment"]["id"]
    call(
        "set_commitment_installments",
        commitment_id=cid,
        installments=[{"label": "Deposit", "due_date": "2026-07-01", "amount": 5000}],
    )
    iid = call("get_commitments")[0]["commitments"][0]["installments"][0]["id"]
    call(
        "update_commitment_installments",
        commitment_id=cid,
        installment_id=iid,
        changes={"paid_txn_id": "txn-9"},
    )
    res, _ = call("remove_commitment_installments", commitment_id=cid, installment_ids=[iid])
    assert res["ok"] is False
    assert iid in res["error"] and "paid_txn_id" in res["error"]
    assert len(call("get_commitments")[0]["commitments"][0]["installments"]) == 1


def test_add_and_remove_commitment_actions_tools(isolated):
    cid = call("create_commitment", name="Apt")[0]["commitment"]["id"]
    call(
        "set_commitment_actions",
        commitment_id=cid,
        actions=[{"label": "Sign contract", "due_date": "2026-08-20"}],
    )
    existing_id = call("get_commitments")[0]["commitments"][0]["actions"][0]["id"]

    res, proposed = call(
        "add_commitment_actions",
        commitment_id=cid,
        actions=[{"label": "Apply for loan", "due_date": "2026-09-01"}],
    )
    assert proposed is None and res["ok"] is True
    rows = res["commitment"]["actions"]
    assert len(rows) == 2 and rows[0]["id"] == existing_id

    res, proposed = call("remove_commitment_actions", commitment_id=cid, action_ids=[rows[1]["id"]])
    assert proposed is None and res["ok"] is True
    assert [r["id"] for r in res["commitment"]["actions"]] == [existing_id]


def test_remove_tool_reports_unknown_and_blocked_in_one_error(isolated):
    """One call surfaces everything to fix — never fix-retry-discover."""
    cid = call("create_commitment", name="Apt")[0]["commitment"]["id"]
    call(
        "set_commitment_installments",
        commitment_id=cid,
        installments=[{"label": "Deposit", "due_date": "2026-07-01", "amount": 5000}],
    )
    iid = call("get_commitments")[0]["commitments"][0]["installments"][0]["id"]
    call(
        "update_commitment_installments",
        commitment_id=cid,
        installment_id=iid,
        changes={"paid_txn_id": "txn-9"},
    )
    res, _ = call(
        "remove_commitment_installments", commitment_id=cid, installment_ids=[iid, "inst-ghost"]
    )
    assert res["ok"] is False
    assert "inst-ghost" in res["error"] and iid in res["error"]  # both reasons, one message


def test_remove_commitment_actions_refuses_live_reminder_and_names_the_fix(isolated):
    cid = call("create_commitment", name="Apt")[0]["commitment"]["id"]
    call(
        "set_commitment_actions",
        commitment_id=cid,
        actions=[{"label": "Sale window", "due_date": "2026-08-20"}],
    )
    aid = call("get_commitments")[0]["commitments"][0]["actions"][0]["id"]
    call(
        "update_commitment_actions",
        commitment_id=cid,
        action_id=aid,
        changes={"reminder_automation_id": "auto-7"},
    )
    res, _ = call("remove_commitment_actions", commitment_id=cid, action_ids=[aid])
    assert res["ok"] is False
    assert aid in res["error"] and "delete_automations" in res["error"]
    assert len(call("get_commitments")[0]["commitments"][0]["actions"]) == 1


def test_create_loan_repayment_builds_amortized_commitment(isolated):
    res, _ = call(
        "create_loan_repayment",
        name="Meridian Bank repayment",
        principal=50000,
        annual_rate_pct=6.5,
        months=24,
        start_date="2026-10-15",
        currency="sgd",
    )
    assert res["ok"] and res["monthly_payment"] > 2000
    c = res["commitment"]
    assert c["kind"] == "loan_repayment" and c["currency"] == "SGD"
    assert len(c["installments"]) == 24
    assert c["installments"][0]["due_date"] == "2026-10-15"
    # it shows up in a subsequent get_commitments
    assert any(x["id"] == c["id"] for x in call("get_commitments")[0]["commitments"])


def test_set_and_update_actions_via_tools(isolated):
    cid = call("create_commitment", name="Apt")[0]["commitment"]["id"]
    res, _ = call(
        "set_commitment_actions",
        commitment_id=cid,
        actions=[
            {
                "id": "a1",
                "label": "Apply for loan",
                "category": "Pre-arrangement",
                "due_date": "2026-08-15",
                "status": "pending",
            }
        ],
    )
    assert res["ok"] and len(res["commitment"]["actions"]) == 1
    res, _ = call(
        "update_commitment_actions", commitment_id=cid, action_id="a1", changes={"status": "done"}
    )
    assert res["commitment"]["actions"][0]["status"] == "done"


def test_delete(isolated):
    cid = call("create_commitment", name="Apt")[0]["commitment"]["id"]
    res, _ = call("delete_commitment", commitment_id=cid)
    assert res["ok"] is True
    assert call("get_commitments")[0]["commitments"] == []
    assert call("delete_commitment", commitment_id="ghost")[0]["ok"] is False


# ── Batch fields (2026-07-19, "act on ONE thing should accept MANY") ──────────────


def test_update_commitment_batch_patches_several_different_commitments_in_one_call(isolated):
    apt = call("create_commitment", name="Apt")[0]["commitment"]["id"]
    meridian = call("create_commitment", name="Meridian Bank", kind="loan_repayment")[0][
        "commitment"
    ]["id"]

    res, proposed = call(
        "update_commitments",
        updates=[
            {"commitment_id": apt, "status": "completed"},
            {"commitment_id": meridian, "name": "Bank X FlexiLoan"},
        ],
    )
    assert proposed is None  # direct write, not a proposed-change tool
    assert res["ok"] is True
    assert res["count"] == 2 and res["updated"] == 2
    assert all(r["ok"] for r in res["results"])

    rows = {r["id"]: r for r in call("get_commitments")[0]["commitments"]}
    assert rows[apt]["status"] == "completed"
    assert rows[meridian]["name"] == "Bank X FlexiLoan"


def test_update_commitment_batch_partial_failure_reports_which_row_failed(isolated):
    apt = call("create_commitment", name="Apt")[0]["commitment"]["id"]
    res, _ = call(
        "update_commitments",
        updates=[
            {"commitment_id": apt, "note": "refinanced"},
            {"commitment_id": "ghost", "note": "x"},
        ],
    )
    assert res["ok"] is True  # the batch call itself succeeds — per-row status is inside
    assert res["updated"] == 1
    assert res["results"][0]["ok"] is True
    assert res["results"][1]["ok"] is False
    assert "error" in res["results"][1]
    rows = {r["id"]: r for r in call("get_commitments")[0]["commitments"]}
    assert rows[apt]["note"] == "refinanced"


def test_update_commitment_single_form_unchanged_when_no_batch_field(isolated):
    apt = call("create_commitment", name="Apt")[0]["commitment"]["id"]
    res, proposed = call("update_commitments", commitment_id=apt, note="single-form")
    assert proposed is None
    assert "results" not in res  # single-form response shape is untouched
    assert res["ok"] and res["commitment"]["note"] == "single-form"


def test_update_commitment_installment_batch_across_two_commitments(isolated):
    apt = call("create_commitment", name="Apt")[0]["commitment"]["id"]
    meridian = call("create_commitment", name="Meridian Bank")[0]["commitment"]["id"]
    call(
        "set_commitment_installments",
        commitment_id=apt,
        installments=[{"id": "i1", "label": "Deposit", "amount": 5000}],
    )
    call(
        "set_commitment_installments",
        commitment_id=meridian,
        installments=[{"id": "i1", "label": "Month 1", "amount": 1200}],
    )

    res, _ = call(
        "update_commitment_installments",
        updates=[
            {
                "commitment_id": apt,
                "installment_id": "i1",
                "changes": {"status": "paid", "paid_txn_id": "txn-1"},
            },
            {
                "commitment_id": meridian,
                "installment_id": "i1",
                "changes": {"status": "paid", "paid_txn_id": "txn-2"},
            },
        ],
    )
    assert res["ok"] is True and res["updated"] == 2
    apt_rows = {i["id"]: i for i in res["results"][0]["commitment"]["installments"]}
    meridian_rows = {i["id"]: i for i in res["results"][1]["commitment"]["installments"]}
    assert apt_rows["i1"]["paid_txn_id"] == "txn-1"
    assert meridian_rows["i1"]["paid_txn_id"] == "txn-2"

    # one bad row among two — the good one still applies
    res, _ = call(
        "update_commitment_installments",
        updates=[
            {"commitment_id": apt, "installment_id": "nope", "changes": {"amount": 1}},
            {"commitment_id": meridian, "installment_id": "i1", "changes": {"amount": 999}},
        ],
    )
    assert res["updated"] == 1
    assert res["results"][0]["ok"] is False
    assert res["results"][1]["ok"] is True


def test_update_commitment_action_batch_across_two_commitments(isolated):
    apt = call("create_commitment", name="Apt")[0]["commitment"]["id"]
    meridian = call("create_commitment", name="Meridian Bank")[0]["commitment"]["id"]
    call(
        "set_commitment_actions",
        commitment_id=apt,
        actions=[{"id": "a1", "label": "Apply for loan", "status": "pending"}],
    )
    call(
        "set_commitment_actions",
        commitment_id=meridian,
        actions=[{"id": "a1", "label": "Sign contract", "status": "pending"}],
    )

    res, _ = call(
        "update_commitment_actions",
        updates=[
            {"commitment_id": apt, "action_id": "a1", "changes": {"status": "done"}},
            {"commitment_id": meridian, "action_id": "a1", "changes": {"status": "done"}},
        ],
    )
    assert res["ok"] is True and res["updated"] == 2
    assert res["results"][0]["commitment"]["actions"][0]["status"] == "done"
    assert res["results"][1]["commitment"]["actions"][0]["status"] == "done"


# ── Honest no-op for an empty patch (2026-07-21) ───────────────────────────────────
#
# An id-only call (no changeable field) used to return a plain success shape echoing
# the unchanged row — indistinguishable from a real edit. Guarded per
# ai/tools/_shared.no_op_write_result: ok stays True (well-formed call), no_op is
# True, and the tool's own echo shape (`commitment`) rides along unchanged.


def test_update_commitments_single_form_empty_patch_is_a_no_op(isolated):
    cid = call("create_commitment", name="Apt", note="original")[0]["commitment"]["id"]
    res, proposed = call("update_commitments", commitment_id=cid)
    assert proposed is None
    assert res["ok"] is True
    assert res["no_op"] is True
    assert res["commitment"]["id"] == cid
    assert res["commitment"]["note"] == "original"  # untouched
    # no store write happened — a second read shows the identical row
    assert call("get_commitments")[0]["commitments"][0]["note"] == "original"


def test_update_commitments_single_form_real_edit_is_unaffected(isolated):
    """Pin rule 4: a real single-field edit stays byte-identical — no no_op key at all."""
    cid = call("create_commitment", name="Apt")[0]["commitment"]["id"]
    res, _ = call("update_commitments", commitment_id=cid, note="refinanced")
    assert res["ok"] is True
    assert "no_op" not in res
    assert res["commitment"]["note"] == "refinanced"


def test_update_commitments_batch_marks_only_the_empty_row_as_no_op(isolated):
    apt = call("create_commitment", name="Apt")[0]["commitment"]["id"]
    meridian = call("create_commitment", name="Meridian Bank")[0]["commitment"]["id"]
    res, _ = call(
        "update_commitments",
        updates=[
            {"commitment_id": apt},  # empty patch — no_op
            {"commitment_id": meridian, "note": "refinanced"},  # real edit
        ],
    )
    assert res["ok"] is True
    assert res["results"][0]["no_op"] is True
    assert res["results"][0]["commitment"]["id"] == apt
    assert "no_op" not in res["results"][1]
    assert res["results"][1]["commitment"]["note"] == "refinanced"


def test_update_commitments_unknown_id_stays_not_found_not_no_op(isolated):
    """An id that resolves to nothing keeps its existing not-found error — a no_op is
    only for an EXISTING entity + empty patch (rule 1)."""
    res, _ = call("update_commitments", commitment_id="ghost")
    assert res["ok"] is False
    assert "no_op" not in res

    res, _ = call("update_commitments", updates=[{"commitment_id": "ghost"}])
    assert res["results"][0]["ok"] is False
    assert "no_op" not in res["results"][0]


def test_update_commitment_installments_single_form_empty_changes_is_a_no_op(isolated):
    cid = call("create_commitment", name="Apt")[0]["commitment"]["id"]
    call(
        "set_commitment_installments",
        commitment_id=cid,
        installments=[{"id": "i1", "label": "Deposit", "amount": 5000}],
    )
    res, proposed = call("update_commitment_installments", commitment_id=cid, installment_id="i1")
    assert proposed is None
    assert res["ok"] is True
    assert res["no_op"] is True
    rows = {i["id"]: i for i in res["commitment"]["installments"]}
    assert rows["i1"]["amount"] == 5000  # untouched


def test_update_commitment_installments_single_form_real_edit_is_unaffected(isolated):
    cid = call("create_commitment", name="Apt")[0]["commitment"]["id"]
    call(
        "set_commitment_installments",
        commitment_id=cid,
        installments=[{"id": "i1", "label": "Deposit", "amount": 5000}],
    )
    res, _ = call(
        "update_commitment_installments",
        commitment_id=cid,
        installment_id="i1",
        changes={"amount": 250},
    )
    assert "no_op" not in res
    rows = {i["id"]: i for i in res["commitment"]["installments"]}
    assert rows["i1"]["amount"] == 250


def test_update_commitment_installments_empty_changes_on_missing_installment_stays_not_found(
    isolated,
):
    """A no_op is only for an EXISTING installment — a bad id keeps the existing
    not-found shape rather than a false no_op."""
    cid = call("create_commitment", name="Apt")[0]["commitment"]["id"]
    res, _ = call("update_commitment_installments", commitment_id=cid, installment_id="nope")
    assert res["ok"] is False
    assert "no_op" not in res
    assert res["commitment"] is None


def test_update_commitment_installments_batch_marks_only_the_empty_row_as_no_op(isolated):
    apt = call("create_commitment", name="Apt")[0]["commitment"]["id"]
    meridian = call("create_commitment", name="Meridian Bank")[0]["commitment"]["id"]
    call(
        "set_commitment_installments",
        commitment_id=apt,
        installments=[{"id": "i1", "label": "Deposit", "amount": 5000}],
    )
    call(
        "set_commitment_installments",
        commitment_id=meridian,
        installments=[{"id": "i1", "label": "Month 1", "amount": 1200}],
    )
    res, _ = call(
        "update_commitment_installments",
        updates=[
            {"commitment_id": apt, "installment_id": "i1"},  # empty patch — no_op
            {
                "commitment_id": meridian,
                "installment_id": "i1",
                "changes": {"status": "paid", "paid_txn_id": "txn-2"},
            },
        ],
    )
    assert res["results"][0]["no_op"] is True
    assert "no_op" not in res["results"][1]
    meridian_rows = {i["id"]: i for i in res["results"][1]["commitment"]["installments"]}
    assert meridian_rows["i1"]["paid_txn_id"] == "txn-2"


def test_update_commitment_actions_single_form_empty_changes_is_a_no_op(isolated):
    cid = call("create_commitment", name="Apt")[0]["commitment"]["id"]
    call(
        "set_commitment_actions",
        commitment_id=cid,
        actions=[{"id": "a1", "label": "Apply for loan", "status": "pending"}],
    )
    res, proposed = call("update_commitment_actions", commitment_id=cid, action_id="a1")
    assert proposed is None
    assert res["ok"] is True
    assert res["no_op"] is True
    assert res["commitment"]["actions"][0]["status"] == "pending"  # untouched


def test_update_commitment_actions_single_form_real_edit_is_unaffected(isolated):
    cid = call("create_commitment", name="Apt")[0]["commitment"]["id"]
    call(
        "set_commitment_actions",
        commitment_id=cid,
        actions=[{"id": "a1", "label": "Apply for loan", "status": "pending"}],
    )
    res, _ = call(
        "update_commitment_actions", commitment_id=cid, action_id="a1", changes={"status": "done"}
    )
    assert "no_op" not in res
    assert res["commitment"]["actions"][0]["status"] == "done"


def test_update_commitment_actions_empty_changes_on_missing_action_stays_not_found(isolated):
    cid = call("create_commitment", name="Apt")[0]["commitment"]["id"]
    res, _ = call("update_commitment_actions", commitment_id=cid, action_id="nope")
    assert res["ok"] is False
    assert "no_op" not in res
    assert res["commitment"] is None


def test_update_commitment_actions_batch_marks_only_the_empty_row_as_no_op(isolated):
    apt = call("create_commitment", name="Apt")[0]["commitment"]["id"]
    meridian = call("create_commitment", name="Meridian Bank")[0]["commitment"]["id"]
    call(
        "set_commitment_actions",
        commitment_id=apt,
        actions=[{"id": "a1", "label": "Apply for loan", "status": "pending"}],
    )
    call(
        "set_commitment_actions",
        commitment_id=meridian,
        actions=[{"id": "a1", "label": "Sign contract", "status": "pending"}],
    )
    res, _ = call(
        "update_commitment_actions",
        updates=[
            {"commitment_id": apt, "action_id": "a1"},  # empty patch — no_op
            {"commitment_id": meridian, "action_id": "a1", "changes": {"status": "done"}},
        ],
    )
    assert res["results"][0]["no_op"] is True
    assert res["results"][0]["commitment"]["actions"][0]["status"] == "pending"
    assert "no_op" not in res["results"][1]
    assert res["results"][1]["commitment"]["actions"][0]["status"] == "done"
