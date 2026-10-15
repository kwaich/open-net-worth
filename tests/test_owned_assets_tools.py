"""
AI tool-dispatch tests for backend.ai.tools.assets and .asset_values (ADR
2026-09-06-assets-tab-faithful-port, decision 11).

Offline + isolated: OwnedAssetsStore is DB-backed — db_isolated gives each test its own
fresh, migrated database; execute_tool constructs the store per call via
services.owned_assets, so the isolation takes effect transparently. execute_tool is async,
driven here via asyncio.run. No LLM, no network.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from backend.ai.tools import TOOL_SCHEMAS, execute_tool
from backend.ai.tools.registry import mutated_domains
from backend.db.account_store import AccountStore


@pytest.fixture
def isolated(db_isolated):
    return db_isolated


def call(tool, **inp):
    text, proposed = asyncio.run(execute_tool(tool, inp))
    return json.loads(text), proposed


def test_all_ten_asset_tools_registered():
    names = {t["name"] for t in TOOL_SCHEMAS}
    assert {
        "list_assets",
        "get_asset",
        "create_asset",
        "update_asset",
        "record_asset_value",
        "correct_asset_value",
        "remove_asset_value",
        "dispose_asset",
        "delete_asset",
        "create_asset_category",
    } <= names


# ── write classification ─────────────────────────────────────────────────────────────────


def test_reads_are_not_write_classified():
    assert mutated_domains("list_assets") == ()
    assert mutated_domains("get_asset") == ()


def test_dispose_and_delete_are_write_classified():
    assert mutated_domains("dispose_asset")
    assert mutated_domains("delete_asset")
    assert mutated_domains("create_asset")
    assert mutated_domains("update_asset")
    assert mutated_domains("record_asset_value")
    assert mutated_domains("create_asset_category")
    assert mutated_domains("correct_asset_value") == ("assets",)
    assert mutated_domains("remove_asset_value") == ("assets",)


# ── create_asset / get_asset / list_assets ───────────────────────────────────────────────


def test_create_then_get_asset(isolated):
    body, proposed = call(
        "create_asset",
        name="Rolex Submariner",
        category="watch",
        value=14800,
        cost=9000,
        cost_date="2019-08-01",
    )
    assert proposed is None
    assert body["ok"] is True
    asset_id = body["asset"]["id"]

    got, _ = call("get_asset", asset_id=asset_id)
    assert got["ok"] is True
    assert got["asset"]["value"] == 14800
    assert len(got["asset"]["marks"]) == 2  # today's mark + the cost mark


def test_asset_tool_contract_uses_typed_details_and_has_no_review_fields(isolated):
    """Catches Lucius being offered the removed scheduler contract or loose maturity text."""
    create_schema = next(t for t in TOOL_SCHEMAS if t["name"] == "create_asset")["input_schema"]
    update_schema = next(t for t in TOOL_SCHEMAS if t["name"] == "update_asset")["input_schema"]
    for schema in (create_schema, update_schema):
        assert "insured" in schema["properties"]
        assert "maturity_date" in schema["properties"]
        assert "cadence_months" not in schema["properties"]
        assert "matures" not in schema["properties"]

    body, _ = call(
        "create_asset",
        name="Endowment",
        category="policy",
        value=42000,
        insured=True,
        maturity_date="2031-06-17",
    )
    assert body["asset"]["insured"] is True
    assert body["asset"]["maturity_date"] == "2031-06-17"


def test_create_unknown_category_returns_ok_false(isolated):
    body, _ = call("create_asset", name="x", category="nope", value=1)
    assert body["ok"] is False
    assert "error" in body


def test_list_assets_bounded_and_excludes_docs_content(isolated):
    for i in range(3):
        call("create_asset", name=f"Item {i}", category="art", value=1000 + i)
    body, _ = call("list_assets")
    assert body["total"] == 3
    assert body["showing"] == 3
    row = body["assets"][0]
    assert set(row) >= {"id", "name", "category", "value", "docs"}
    assert "marks" not in row
    assert "facts" not in row
    for doc in row["docs"]:
        assert set(doc) == {"name", "lucius_readable"}


def test_list_assets_rows_carry_share_value(isolated):
    call("create_asset", name="Joint flat", category="property", value=900_000, share_pct=50)
    body, _ = call("list_assets")
    row = body["assets"][0]
    assert (row["value"], row["share_pct"], row["share_value"]) == (900_000, 50, 450_000)


def test_list_assets_filters_by_category(isolated):
    call("create_asset", name="House", category="property", value=100000)
    call("create_asset", name="Watch", category="watch", value=5000)
    body, _ = call("list_assets", category="property")
    assert body["total"] == 1
    assert body["assets"][0]["category"] == "property"


def test_list_assets_excludes_archived_by_default(isolated):
    cash = AccountStore().create({"name": "DBS", "type": "cash", "kind": "asset"})
    body, _ = call("create_asset", name="Old Car", category="vehicle", value=8000)
    call("dispose_asset", asset_id=body["asset"]["id"], proceeds=8000, account_id=cash["id"])
    listed, _ = call("list_assets")
    assert listed["total"] == 0
    listed_all, _ = call("list_assets", include_archived=True)
    assert listed_all["total"] == 1


def test_get_asset_missing_is_ok_false(isolated):
    body, _ = call("get_asset", asset_id="oa_nope")
    assert body["ok"] is False


# ── update_asset (identity only) ─────────────────────────────────────────────────────────


def test_update_asset_never_accepts_value(isolated):
    created, _ = call("create_asset", name="Condo", category="property", value=500000)
    asset_id = created["asset"]["id"]
    body, _ = call("update_asset", asset_id=asset_id, name="Condo (renamed)")
    assert body["ok"] is True
    assert body["asset"]["name"] == "Condo (renamed)"
    assert body["asset"]["value"] == 500000  # unchanged — no `value` field exists on the tool


def test_update_asset_no_fields_is_no_op(isolated):
    created, _ = call("create_asset", name="Condo", category="property", value=500000)
    body, _ = call("update_asset", asset_id=created["asset"]["id"])
    assert body["ok"] is True
    assert body["no_op"] is True


def test_update_asset_can_clear_maturity_date(isolated):
    created, _ = call(
        "create_asset",
        name="Endowment",
        category="policy",
        value=50000,
        maturity_date="2031-06-01",
    )
    body, _ = call("update_asset", asset_id=created["asset"]["id"], maturity_date=None)
    assert body["ok"] is True
    assert body["asset"]["maturity_date"] is None


# ── record_asset_value ────────────────────────────────────────────────────────────────────────


def test_record_asset_value_updates_value(isolated):
    created, _ = call("create_asset", name="Gold bars", category="metal", value=5000)
    body, _ = call(
        "record_asset_value", asset_id=created["asset"]["id"], value=6000, note="appraisal"
    )
    assert body["ok"] is True
    assert body["asset"]["value"] == 6000
    assert len(body["asset"]["marks"]) == 2


def test_correct_asset_value_fixes_a_typo_in_place(isolated):
    created, _ = call("create_asset", name="Watch", category="watch", value=16000)
    asset_id, mark_id = created["asset"]["id"], created["asset"]["marks"][0]["id"]
    body, proposed = call("correct_asset_value", asset_id=asset_id, mark_id=mark_id, value=16400)
    assert proposed is None
    assert body["ok"] is True
    assert body["asset"]["value"] == 16400
    assert [m["id"] for m in body["asset"]["marks"]] == [mark_id]  # replaced, not appended
    assert body["asset"]["marks"][0]["edited_at"]

    same, _ = call("correct_asset_value", asset_id=asset_id, mark_id=mark_id, value=16400)
    assert (same["ok"], same["no_op"]) == (True, True)
    empty, _ = call("correct_asset_value", asset_id=asset_id, mark_id=mark_id)
    assert (empty["ok"], empty["no_op"]) == (True, True)


def test_correct_asset_value_refuses_purchase_point(isolated):
    created, _ = call(
        "create_asset",
        name="Watch",
        category="watch",
        value=16000,
        cost=9000,
        cost_date="2019-08-01",
    )
    body, _ = call(
        "correct_asset_value", asset_id=created["asset"]["id"], mark_id="purchase", value=1
    )
    assert body == {
        "ok": False,
        "error": (
            "The purchase point comes from the purchase price and date — change or clear "
            "them in Purchase."
        ),
    }


def test_correct_asset_value_with_nothing_to_change_still_checks_the_value(isolated):
    """Code review R3: with no fields the tool answered a no-op without looking the value up,
    so the purchase point or a value that doesn't exist "succeeded"."""
    created, _ = call(
        "create_asset",
        name="Watch",
        category="watch",
        value=16000,
        cost=9000,
        cost_date="2019-08-01",
    )
    asset_id = created["asset"]["id"]
    purchase, _ = call("correct_asset_value", asset_id=asset_id, mark_id="purchase")
    assert purchase == {
        "ok": False,
        "error": (
            "The purchase point comes from the purchase price and date — change or clear "
            "them in Purchase."
        ),
    }
    missing, _ = call("correct_asset_value", asset_id=asset_id, mark_id="mk_000000000000")
    assert missing == {"ok": False, "error": "That value no longer exists."}
    gone, _ = call("correct_asset_value", asset_id="oa_000000000000", mark_id="mk_000000000000")
    assert gone == {"ok": False, "error": "That asset ('oa_000000000000') no longer exists."}


def test_remove_asset_value_refuses_last_value(isolated):
    created, _ = call("create_asset", name="Watch", category="watch", value=16000)
    asset = created["asset"]
    body, _ = call("remove_asset_value", asset_id=asset["id"], mark_id=asset["marks"][0]["id"])
    assert body == {
        "ok": False,
        "error": "An asset needs at least one value. Record a new value first, or delete the asset.",
    }
    added, _ = call("record_asset_value", asset_id=asset["id"], value=17000, date="2026-01-01")
    body, _ = call("remove_asset_value", asset_id=asset["id"], mark_id=asset["marks"][0]["id"])
    assert body["ok"] is True
    assert body["asset"]["value"] == 17000


def test_update_asset_null_cost_clears_purchase_point(isolated):
    created, _ = call(
        "create_asset",
        name="Flat",
        category="property",
        value=1_150_000,
        cost=900_000,
        cost_date="2019-03-15",
    )
    asset_id = created["asset"]["id"]
    assert [m["kind"] for m in created["asset"]["marks"]] == ["valuation", "purchase"]
    cleared, _ = call("update_asset", asset_id=asset_id, cost=None)
    assert cleared["asset"]["cost"] is None
    assert [m["kind"] for m in cleared["asset"]["marks"]] == ["valuation"]
    repriced, _ = call("update_asset", asset_id=asset_id, cost=950_000)
    assert repriced["asset"]["marks"][-1]["value"] == 950_000
    undated, _ = call("update_asset", asset_id=asset_id, cost_date=None)
    assert undated["asset"]["cost_date"] is None
    assert [m["kind"] for m in undated["asset"]["marks"]] == ["valuation"]


# ── dispose_asset / delete_asset ─────────────────────────────────────────────────────────


def test_dispose_asset_posts_transaction_and_archives(isolated):
    cash = AccountStore().create({"name": "DBS", "type": "cash", "kind": "asset"})
    created, _ = call("create_asset", name="Old Car", category="vehicle", value=8000)
    body, _ = call(
        "dispose_asset", asset_id=created["asset"]["id"], proceeds=8000, account_id=cash["id"]
    )
    assert body["ok"] is True
    assert body["asset"]["archived"] is True
    assert body["asset"]["disposal_txn_id"]


def test_dispose_asset_bad_account_is_ok_false(isolated):
    created, _ = call("create_asset", name="Old Car", category="vehicle", value=8000)
    brokerage = AccountStore().create({"name": "IBKR", "type": "investment", "kind": "asset"})
    body, _ = call(
        "dispose_asset",
        asset_id=created["asset"]["id"],
        proceeds=100,
        account_id=brokerage["id"],
    )
    assert body["ok"] is False


def test_delete_asset(isolated):
    created, _ = call("create_asset", name="Junk", category="other", value=1)
    body, _ = call("delete_asset", asset_id=created["asset"]["id"])
    assert body["ok"] is True
    listed, _ = call("list_assets", include_archived=True)
    assert listed["total"] == 0


# ── create_asset_category ────────────────────────────────────────────────────────────────


def test_create_asset_category_is_idempotent(isolated):
    body, _ = call("create_asset_category", label="Vinyl & hi-fi")
    assert body["ok"] is True
    key = body["category"]["key"]
    body2, _ = call("create_asset_category", label="vinyl & hi-fi")
    assert body2["category"]["key"] == key


# ── C1: Lucius cannot mint an automation via create_asset/update_asset ────────────────────


def test_create_asset_schema_has_no_automate_key():
    schema = next(t for t in TOOL_SCHEMAS if t["name"] == "create_asset")["input_schema"]
    assert "automate" not in schema["properties"]


def test_update_asset_schema_has_no_automate_key():
    schema = next(t for t in TOOL_SCHEMAS if t["name"] == "update_asset")["input_schema"]
    assert "automate" not in schema["properties"]


def test_create_asset_with_automate_is_rejected(isolated):
    """`automate` is gone from the declared schema (an honest model is never offered it),
    but the registry's own dispatch validation does not enforce `additionalProperties`
    (by design, for the propose→apply replay paths), so the handler explicitly rejects a
    malformed/injected retired field and never mints a system Automation."""
    body, proposed = call(
        "create_asset", name="House", category="property", value=100000, automate=True
    )
    assert proposed is None
    assert body["ok"] is False
    assert "retired" in body["error"]
    from backend.db.automation_store import AutomationStore

    assert AutomationStore().all() == []  # no automation minted anywhere


def test_update_asset_with_automate_is_rejected(isolated):
    """The update path rejects the same retired field instead of treating it as a no-op."""
    created, _ = call("create_asset", name="House", category="property", value=100000)
    body, _ = call("update_asset", asset_id=created["asset"]["id"], automate=True)
    assert body["ok"] is False
    assert "retired" in body["error"]
    from backend.db.automation_store import AutomationStore

    assert AutomationStore().all() == []


# ── H2: get_asset withholds a non-readable doc's ref_id ─────────────────────────────────


@pytest.fixture
def attachment(tmp_path, monkeypatch):
    """A real attachment row (the projection hides a document whose attachment is gone)."""
    import backend.db.attachment_store as attachment_store
    from backend.services import attachments as attachments_service

    directory = tmp_path / "attachments"
    directory.mkdir()
    monkeypatch.setattr(attachment_store, "ATTACHMENTS_DIR", directory)
    attachments_service._cache_drop()

    def _make(name: str) -> str:
        return attachments_service.create_attachment(
            {"kind": "text", "summary": name, "format": "txt"},
            content=b"deed",
            filename=name,
            media_type="text/plain",
            source_channel="assets:docs",
        )

    yield _make
    attachments_service._cache_drop()


def test_get_asset_withholds_ref_for_non_readable_doc(isolated, attachment):
    from backend.services import owned_assets as _oa

    created, _ = call("create_asset", name="House", category="property", value=100000)
    asset_id = created["asset"]["id"]
    ref = attachment("deed.pdf")
    _oa.apply_owned_asset_change(
        {"op": "add_doc", "id": asset_id, "ref_id": ref, "name": "deed.pdf"}
    )
    _oa.apply_owned_asset_change(
        {
            "op": "update_doc",
            "id": asset_id,
            "ref_id": ref,
            "changes": {"lucius_readable": False},
        }
    )

    got, _ = call("get_asset", asset_id=asset_id)
    docs = got["asset"]["docs"]
    assert len(docs) == 1
    assert docs[0] == {"name": "deed.pdf", "lucius_readable": False, "withheld": True}
    assert "ref_id" not in docs[0]
    assert ref not in json.dumps(got)  # no content pointer anywhere in the payload

    # list_assets' doc summary is unaffected by this projection — still name + lucius_readable.
    listed, _ = call("list_assets")
    assert listed["assets"][0]["docs"] == [{"name": "deed.pdf", "lucius_readable": False}]


def test_get_asset_readable_doc_keeps_ref_id(isolated, attachment):
    from backend.services import owned_assets as _oa

    created, _ = call("create_asset", name="House", category="property", value=100000)
    asset_id = created["asset"]["id"]
    ref = attachment("appraisal.pdf")
    _oa.apply_owned_asset_change(
        {"op": "add_doc", "id": asset_id, "ref_id": ref, "name": "appraisal.pdf"}
    )
    got, _ = call("get_asset", asset_id=asset_id)
    assert got["asset"]["docs"][0]["ref_id"] == ref


def test_get_asset_caps_marks_at_fifty(isolated):
    created, _ = call("create_asset", name="Gold", category="metal", value=100)
    asset_id = created["asset"]["id"]
    for i in range(60):
        call(
            "record_asset_value",
            asset_id=asset_id,
            value=100 + i,
            date=f"2020-{(i % 12) + 1:02d}-01",
        )

    got, _ = call("get_asset", asset_id=asset_id)
    assert len(got["asset"]["marks"]) == 50
    assert got["asset"]["marks_total"] == 61  # the 60 added + the original creation mark
