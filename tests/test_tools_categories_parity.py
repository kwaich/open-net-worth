"""
AI tool-dispatch tests for the extended categories tools (LANE A parity):
  - set_category_color   (new)
  - add_category         (extended: group)

All tests are offline — no LLM, no network.

Run:
    .venv/bin/python3 -m pytest tests/test_tools_categories_parity.py -v
"""

from __future__ import annotations

import asyncio
import json

import pytest

from backend.ai.tools import TOOL_SCHEMAS, execute_tool
from backend.db.category_store import CategoryStore
from backend.models.category_defs import BUILTIN_CATEGORIES

# ── helpers ────────────────────────────────────────────────────────────────────


def call(tool: str, **inp):
    """Run execute_tool synchronously; return (parsed_json, proposed_change)."""
    text, proposed = asyncio.run(execute_tool(tool, inp))
    return json.loads(text), proposed


@pytest.fixture()
def iso(tmp_path, monkeypatch, db_isolated):
    """Isolate every store the categories service touches to throwaway files.
    cat_svc.transaction_store is DB-backed now (plan 3 task 4) — db_isolated redirects
    it to a fresh, empty database. automations is DB-backed now (plan 4d task 2) —
    db_isolated already covers it."""
    profile_path = tmp_path / "profile.json"
    # profile is DB-backed now (Wave 5, JSON->DB) — db_isolated already gives it a fresh,
    # empty row, no monkeypatch needed; profile_path stays a plain identity key below.
    # CategoryStore is DB-backed (plan 4a task 2); db_isolated redirects it to a fresh, empty
    # database — reseed BUILTIN_CATEGORIES here (tests/conftest.py's shared autouse seed ran
    # BEFORE db_isolated activated, so it landed in the old, now-orphaned default engine).
    CategoryStore().replace_all(list(BUILTIN_CATEGORIES))

    yield {"tmp_path": tmp_path, "profile": profile_path}


# ── tool registration ──────────────────────────────────────────────────────────


def test_new_tools_registered():
    names = {t["name"] for t in TOOL_SCHEMAS}
    assert "set_categories_color" in names
    assert "set_categories_taxable" not in names


# ── set_category_color ─────────────────────────────────────────────────────────


def test_set_category_color_builtin(iso):
    res, proposed = call("set_categories_color", category_id="cat_13095ba70000", color="#aabbcc")
    assert proposed is None
    assert res["ok"] is True
    assert res["category_id"] == "cat_13095ba70000"
    assert res["color"] == "#aabbcc"


def test_set_category_color_custom(iso):
    # Create a custom category first, then colour it.
    created, _ = call("create_categories", label="My Cat", kind="expense")
    cat_id = created["id"]
    res, proposed = call("set_categories_color", category_id=cat_id, color="#123456")
    assert proposed is None
    assert res == {"ok": True, "category_id": cat_id, "color": "#123456"}


def test_set_category_color_invalid_hex_handled(iso):
    res, proposed = call("set_categories_color", category_id="cat_13095ba70000", color="not-a-hex")
    assert proposed is None
    assert res["ok"] is False
    assert "hex" in res["error"].lower()


def test_set_category_color_missing_key_handled(iso):
    res, proposed = call("set_categories_color", category_id="ghost_key", color="#aabbcc")
    assert proposed is None
    assert res["ok"] is False


def test_add_categories_schema_has_no_category_tax_field(iso):
    schema = next(t for t in TOOL_SCHEMAS if t["name"] == "create_categories")["input_schema"]
    item_properties = schema["properties"]["categories"]["items"]["properties"]
    assert "taxable_default" not in item_properties


def test_add_category_discards_removed_taxable_default_field(iso):
    res, proposed = call(
        "create_categories",
        label="Stipend",
        kind="income",
        taxable_default=False,
    )
    assert proposed is None
    assert res["ok"] is True
    assert "taxable_default" not in CategoryStore().get(res["id"])


# ── add_category extended: group ───────────────────────────────────────────────


def test_create_categories_never_creates_a_group_even_when_handed_one(iso):
    """A category is a pure label: the tool takes no ``group`` any more, and a stale caller
    passing one (single or batch form) creates neither a group nor a membership."""
    from backend.services.categories import groups_store

    before = groups_store.all()
    single, _ = call("create_categories", label="Startup Costs", kind="expense", group="Business")
    batch, _ = call(
        "create_categories",
        categories=[{"label": "Salary Advance", "kind": "income", "group": "Work"}],
    )
    assert single["ok"] is True and batch["ok"] is True
    assert groups_store.all() == before
    assert all(g["name"] not in ("Business", "Work") for g in groups_store.all())


def test_create_categories_schema_advertises_no_group():
    from backend.ai.tools.registry import build_tool_schemas

    schema = next(s for s in build_tool_schemas() if s["name"] == "create_categories")
    text = json.dumps(schema["input_schema"])
    assert '"group"' not in text and "group?" not in text


# ── add_category existing behaviour still works (regression guard) ─────────────


def test_add_category_basic(iso):
    res, proposed = call("create_categories", label="Hobbies 2")
    assert proposed is None
    assert res["ok"] is True
    assert res["id"].startswith("cat_")
    assert res["label"] == "Hobbies 2"


def test_add_category_duplicate_label_rejected(iso):
    # Category labels are a flat user-facing namespace — a duplicate (case-insensitive) is
    # refused with a conflict, so the second same-named create returns ok=False.
    first, _ = call("create_categories", label="Same Label")
    second, _ = call("create_categories", label="Same Label")
    assert first["ok"] is True
    assert second["ok"] is False and "already exists" in second["error"]


def test_add_category_with_color(iso):
    res, _ = call("create_categories", label="Sports", color="#ff5500")
    assert res["ok"] is True
    assert res.get("color") == "#ff5500"


_XSS = '"><img src=x onerror=alert(1)>'


def test_create_category_rejects_non_hex_colour(iso):
    res, _ = call("create_categories", label="Bad Colour", color=_XSS)
    assert res["ok"] is False
    assert CategoryStore().all() == [] or all(
        r["label"] != "Bad Colour" for r in CategoryStore().all()
    )


def test_create_category_service_rejects_non_hex_colour(iso):
    from backend.core.errors import ValidationError
    from backend.services import categories as cat_svc

    with pytest.raises(ValidationError):
        cat_svc.create_category("Sneaky", color="red;background:url(x)")
    assert cat_svc.create_category("Fine", color="#ABCDEF")["color"] == "#abcdef"


def test_create_categories_schema_pins_colour_to_hex():
    schema = next(t for t in TOOL_SCHEMAS if t["name"] == "create_categories")
    props = schema["input_schema"]["properties"]
    assert "pattern" in props["categories"]["items"]["properties"]["color"]


def test_rest_create_category_rejects_non_hex_colour(iso):
    from fastapi.testclient import TestClient

    from backend.main import app

    r = TestClient(app).post("/api/categories", json={"label": "REST Bad", "color": _XSS})
    assert r.status_code == 422 or r.status_code == 400


# ── add_category batch (`categories`) — N creates in ONE call ──────────────────


def test_add_category_batch_creates_several(iso):
    res, proposed = call(
        "create_categories",
        categories=[{"label": "Batch A"}, {"label": "Batch B", "kind": "income"}],
    )
    assert proposed is None
    assert res["ok"] is True
    assert res["count"] == 2
    assert len(res["results"]) == 2
    assert all(r["ok"] for r in res["results"])
    labels = {r["label"] for r in res["results"]}
    assert labels == {"Batch A", "Batch B"}
    assert all(r["id"].startswith("cat_") for r in res["results"])


def test_add_category_batch_partial_failure_is_legible(iso):
    # Two items share a label — the SECOND one clashes with the FIRST, which the batch
    # already persisted (sequential, not parallel) — so item 0 succeeds and item 1 reports
    # its own failure, not an all-or-nothing abort.
    res, _ = call(
        "create_categories",
        categories=[{"label": "Dup"}, {"label": "Dup"}, {"label": "Third"}],
    )
    assert res["ok"] is False
    assert res["count"] == 2
    assert len(res["results"]) == 3
    assert res["results"][0]["ok"] is True
    assert res["results"][1]["ok"] is False
    assert "already exists" in res["results"][1]["error"]
    assert res["results"][1]["label"] == "Dup"
    assert res["results"][1]["index"] == 1
    assert res["results"][2]["ok"] is True
    assert res["results"][2]["label"] == "Third"


def test_add_category_batch_ignores_singular_fields(iso):
    # When `categories` is non-empty, the single-item label/kind/... fields are ignored
    # rather than silently creating a THIRD, unrequested category.
    res, _ = call(
        "create_categories", label="should-not-be-created", categories=[{"label": "Only"}]
    )
    assert res["ok"] is True
    assert res["count"] == 1
    listed, _ = call("list_categories")
    labels = {c["label"] for c in listed["categories"]}
    assert "Only" in labels
    assert "should-not-be-created" not in labels


# ── rename_category batch (`renames`) — relabel/merge several in ONE call ──────


def test_rename_category_batch_relabels_several(iso):
    a, _ = call("create_categories", label="Alpha")
    b, _ = call("create_categories", label="Bravo")
    res, proposed = call(
        "rename_categories",
        renames=[
            {"category_id": a["id"], "new_label": "Alpha 2"},
            {"category_id": b["id"], "new_label": "Bravo 2"},
        ],
    )
    assert proposed is None
    assert res["ok"] is True
    assert res["count"] == 2
    assert all(r["ok"] and not r["merged"] for r in res["results"])
    listed, _ = call("list_categories")
    labels = {c["id"]: c["label"] for c in listed["categories"]}
    assert labels[a["id"]] == "Alpha 2"
    assert labels[b["id"]] == "Bravo 2"


def test_rename_category_batch_chained_merges_apply_in_order(iso):
    # A landmine called out for this lane: merge is a cross-store fold, riskier to batch
    # than a relabel. Chaining A→B then B→C must resolve against the CURRENT state at each
    # step (not a stale pre-batch snapshot), so both folds land on C.
    a, _ = call("create_categories", label="Chain A")
    b, _ = call("create_categories", label="Chain B")
    c, _ = call("create_categories", label="Chain C")
    res, _ = call(
        "rename_categories",
        renames=[
            {"category_id": a["id"], "merge_into": b["id"]},
            {"category_id": b["id"], "merge_into": c["id"]},
        ],
    )
    assert res["ok"] is True
    assert res["count"] == 2
    assert res["results"][0]["merged"] is True
    assert res["results"][0]["merged_into"] == b["id"]
    assert res["results"][1]["merged"] is True
    assert res["results"][1]["merged_into"] == c["id"]
    listed, _ = call("list_categories")
    ids = {cat["id"] for cat in listed["categories"]}
    assert a["id"] not in ids and b["id"] not in ids and c["id"] in ids


def test_rename_category_batch_item_depending_on_earlier_deletion_fails_legibly(iso):
    # item 1 merges A away; item 2 then tries to touch the now-deleted A. Each item is
    # validated fresh (never a batched/cached resolve), so this fails CLEARLY on item 2
    # instead of silently corrupting or resurrecting A.
    a, _ = call("create_categories", label="Gone Soon")
    b, _ = call("create_categories", label="Survivor")
    res, _ = call(
        "rename_categories",
        renames=[
            {"category_id": a["id"], "merge_into": b["id"]},
            {"category_id": a["id"], "new_label": "Should Not Land"},
        ],
    )
    assert res["ok"] is False
    assert res["count"] == 1
    assert res["results"][0]["ok"] is True and res["results"][0]["merged"] is True
    assert res["results"][1]["ok"] is False
    assert res["results"][1]["category_id"] == a["id"]
    assert "not found" in res["results"][1]["error"].lower()


def test_rename_category_batch_refuses_structural_key_per_item(iso):
    valid, _ = call("create_categories", label="Renameable")
    res, _ = call(
        "rename_categories",
        renames=[
            {"category_id": "split", "new_label": "Nope"},
            {"category_id": valid["id"], "new_label": "Renamed"},
        ],
    )
    assert res["ok"] is False
    assert res["count"] == 1
    assert res["results"][0]["ok"] is False
    assert "structural" in res["results"][0]["error"].lower()
    assert res["results"][1]["ok"] is True


# ── delete_categories batch (`deletions`) — delete several in ONE call ────────────


def test_remove_category_batch_removes_several(iso):
    a, _ = call("create_categories", label="Remove A")
    b, _ = call("create_categories", label="Remove B")
    res, proposed = call(
        "delete_categories",
        deletions=[{"category_id": a["id"]}, {"category_id": b["id"]}],
    )
    assert proposed is None
    assert res["ok"] is True
    assert res["count"] == 2
    listed, _ = call("list_categories")
    ids = {c["id"] for c in listed["categories"]}
    assert a["id"] not in ids and b["id"] not in ids


def test_remove_category_batch_partial_failure_structural_per_item(iso):
    valid, _ = call("create_categories", label="Removable")
    res, _ = call(
        "delete_categories",
        deletions=[{"category_id": "split"}, {"category_id": valid["id"]}],
    )
    assert res["ok"] is False
    assert res["count"] == 1
    assert res["results"][0]["ok"] is False
    assert "cannot be removed" in res["results"][0]["error"]
    assert res["results"][1]["ok"] is True


# ── set_category_color batch (`updates`) — colour several in ONE call ──────────


def test_set_category_color_batch_updates_several(iso):
    a, _ = call("create_categories", label="Color A")
    b, _ = call("create_categories", label="Color B")
    res, proposed = call(
        "set_categories_color",
        updates=[
            {"category_id": a["id"], "color": "#111111"},
            {"category_id": b["id"], "color": "#222222"},
        ],
    )
    assert proposed is None
    assert res["ok"] is True
    assert res["count"] == 2
    colors = {r["category_id"]: r["color"] for r in res["results"]}
    assert colors[a["id"]] == "#111111"
    assert colors[b["id"]] == "#222222"


def test_set_category_color_batch_partial_failure_invalid_hex(iso):
    a, _ = call("create_categories", label="Color OK")
    res, _ = call(
        "set_categories_color",
        updates=[
            {"category_id": a["id"], "color": "#333333"},
            {"category_id": "cat_13095ba70000", "color": "not-a-hex"},
        ],
    )
    assert res["ok"] is False
    assert res["count"] == 1
    assert res["results"][0]["ok"] is True
    assert res["results"][1]["ok"] is False
    assert "hex" in res["results"][1]["error"].lower()
