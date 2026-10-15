"""
Tests for the category hide/unhide feature (services.categories + api/ledger).

Categories carry opaque ids (docs/adr/2026-07-05-category-opaque-ids.md) — hidden state is a
``hidden`` field on the store row, addressed by id (never a label/slug).

Design contract:
  * ``set_category_hidden(category_id, hidden=True)`` sets ``hidden=True`` on the store row, where
    ``category_id`` is the category's opaque id.
  * Only the structural ``split`` id cannot be hidden.
  * Hiding is PRESENTATIONAL — transactions/assignments/math are untouched.
  * ``get_all_categories()`` excludes rows where ``hidden=True`` by default.
  * ``hidden_categories()`` is a store-derived helper ({id | row.hidden}).
  * Writes are checkpoint-covered (store write fires ``core.io.fire_write_hook``).

The AI TOOL wrapping this service was renamed ``set_category_hidden`` → ``set_categories_hidden``
(param ``category_id`` → ``category_ids``) 2026-07-19 — the service function above is
UNCHANGED, only the model-facing tool name/param. See the "Tool smoke tests" section below.
"""

from __future__ import annotations

import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api.ledger import get_all_categories
from backend.api.ledger import router as ledger_router
from backend.core.errors import NotFoundError, ValidationError
from backend.db.category_store import CategoryStore
from backend.db.profile_store import ProfileStore
from backend.models.category_defs import BUILTIN_CATEGORIES
from backend.services import categories as cat


class _TxnsProxy:
    """Drop-in replacement for the old ``txns_path`` (a bare ``Path``) now that
    transaction_store is DB-backed (plan 3 task 4) — ``env.txns.write_text(json_str,
    encoding=...)``/``env.txns.read_text()`` keep working exactly as every existing test
    body already calls them, just routed through the real store's ``replace_all``/``all``
    (wipe + reinsert = the same "this IS the whole ledger" semantics a raw full-file
    overwrite had) instead of a JSON file."""

    def write_text(self, data: str, encoding: str = "utf-8") -> None:
        del encoding
        cat.transaction_store.replace_all(json.loads(data))

    def read_text(self) -> str:
        return json.dumps(cat.transaction_store.all())


@pytest.fixture
def env(tmp_path, monkeypatch, db_isolated):
    # budget_assignments/category_groups/profile are all DB-backed now (Wave 5, JSON->DB) —
    # db_isolated already gives each of them a fresh, empty table/row; profile is seeded
    # directly through ProfileStore below (mirrors _TxnsProxy's DB-routed drop-in above).
    profile_store = ProfileStore()
    profile_store.set_raw({"base_currency": "SGD"})

    CategoryStore().bulk_insert(BUILTIN_CATEGORIES)

    def _profile():
        return profile_store.get_raw()

    def _set_profile(**keys):
        cur = _profile()
        cur.update(keys)
        profile_store.set_raw(cur)

    def _store():
        return CategoryStore()

    return type(
        "Env",
        (),
        {
            "txns": _TxnsProxy(),
            "profile": staticmethod(_profile),
            "set_profile": staticmethod(_set_profile),
            "store": staticmethod(_store),
        },
    )


@pytest.fixture
def api_client(env):
    app = FastAPI()
    app.include_router(ledger_router)
    return TestClient(app)


# ── Service unit tests ────────────────────────────────────────────────────────────


def test_hide_category(env):
    """Hiding a category sets hidden=True on the store row."""
    out = cat.set_category_hidden("cat_13095ba70000", hidden=True)
    assert out == {"ok": True, "category_id": "cat_13095ba70000", "hidden": True}
    assert env.store().get("cat_13095ba70000")["hidden"] is True
    assert "cat_13095ba70000" in cat.hidden_categories()


def test_hide_custom_category(env):
    """Hiding a custom category works identically."""
    category_id = cat.create_category("Startup")["id"]
    out = cat.set_category_hidden(category_id, hidden=True)
    assert out["ok"] and out["hidden"] is True
    assert category_id in cat.hidden_categories()
    assert env.store().get(category_id)["hidden"] is True


def test_unhide_restores_category(env):
    """Unhiding clears the hidden flag on the store row."""
    cat.set_category_hidden("cat_13095ba70000", hidden=True)
    assert "cat_13095ba70000" in cat.hidden_categories()

    out = cat.set_category_hidden("cat_13095ba70000", hidden=False)
    assert out == {"ok": True, "category_id": "cat_13095ba70000", "hidden": False}
    assert "cat_13095ba70000" not in cat.hidden_categories()
    assert not env.store().get("cat_13095ba70000").get("hidden")


def test_hide_with_label_instead_of_id_not_found(env):
    """set_category_hidden takes an opaque id, not a label — passing a label raises
    NotFoundError."""
    with pytest.raises(NotFoundError):
        cat.set_category_hidden("Food & Dining", hidden=True)


def test_hide_structural_keys_refused(env):
    for category_id in cat.STRUCTURAL_KEYS:
        with pytest.raises(ValidationError, match="structural"):
            cat.set_category_hidden(category_id, hidden=True)


def test_hide_missing_key_raises_not_found(env):
    with pytest.raises(NotFoundError):
        cat.set_category_hidden("cat_ghost_category", hidden=True)


def test_hide_deleted_category_raises_not_found(env):
    """A deleted category is no longer in the store — cannot be hidden."""
    cat.delete_category("cat_13095ba70000")
    with pytest.raises(NotFoundError):
        cat.set_category_hidden("cat_13095ba70000", hidden=True)


def test_unhide_already_visible_is_idempotent(env):
    out = cat.set_category_hidden("cat_13095ba70000", hidden=False)
    assert out["ok"] and "cat_13095ba70000" not in cat.hidden_categories()


def test_hide_idempotent(env):
    cat.set_category_hidden("cat_13095ba70000", hidden=True)
    cat.set_category_hidden("cat_13095ba70000", hidden=True)
    assert list(cat.hidden_categories()).count("cat_13095ba70000") == 1


# ── get_all_categories exclude/include ────────────────────────────────────────────


def test_get_all_categories_excludes_hidden_by_default(env):
    cat.set_category_hidden("cat_13095ba70000", hidden=True)
    ids = [c["id"] for c in get_all_categories()]
    assert "cat_13095ba70000" not in ids
    assert "cat_db5642ab0000" in ids


def test_get_all_categories_include_hidden_restores(env):
    cat.set_category_hidden("cat_13095ba70000", hidden=True)
    ids_default = [c["id"] for c in get_all_categories()]
    ids_all = [c["id"] for c in get_all_categories(include_hidden=True)]
    assert "cat_13095ba70000" not in ids_default
    assert "cat_13095ba70000" in ids_all


def test_get_all_categories_hidden_custom(env):
    category_id = cat.create_category("Startup")["id"]
    cat.set_category_hidden(category_id, hidden=True)
    assert category_id in cat.hidden_categories()

    cat.set_category_hidden(category_id, hidden=False)
    assert category_id not in cat.hidden_categories()

    cat.set_category_hidden(category_id, hidden=True)
    assert category_id in cat.hidden_categories()


# ── Math / data unaffected by hiding ─────────────────────────────────────────────


def test_transactions_unaffected_by_hiding(env):
    env.txns.write_text(
        json.dumps(
            [
                {
                    "id": "t1",
                    "kind": "expense",
                    "date": "2026-06-01",
                    "category": "cat_13095ba70000",
                    "from_amount": 50.0,
                    "from_currency": "SGD",
                }
            ]
        ),
        encoding="utf-8",
    )
    cat.set_category_hidden("cat_13095ba70000", hidden=True)
    txns = json.loads(env.txns.read_text())
    assert txns[0]["category"] == "cat_13095ba70000"
    assert txns[0]["from_amount"] == 50.0


def test_hiding_does_not_touch_profile_amounts(env):
    env.set_profile(
        monthly_budgets={"cat_13095ba70000": {"limit": 300.0, "rollover": False}},
        category_targets={"cat_13095ba70000": {"type": "monthly", "amount": 250.0}},
    )
    cat.set_category_hidden("cat_13095ba70000", hidden=True)
    prof = env.profile()
    assert prof["monthly_budgets"]["cat_13095ba70000"]["limit"] == 300.0
    assert prof["category_targets"]["cat_13095ba70000"]["amount"] == 250.0


# ── REST API smoke tests ──────────────────────────────────────────────────────────


def test_api_hide_endpoint(api_client):
    r = api_client.post("/api/categories/cat_13095ba70000/hide")
    assert r.status_code == 200
    assert r.json() == {"ok": True, "category_id": "cat_13095ba70000", "hidden": True}


def test_api_unhide_endpoint(api_client):
    api_client.post("/api/categories/cat_13095ba70000/hide")
    r = api_client.post("/api/categories/cat_13095ba70000/unhide")
    assert r.status_code == 200
    assert r.json() == {"ok": True, "category_id": "cat_13095ba70000", "hidden": False}


def test_api_hide_structural_key_422(api_client):
    # "split" is the only structural category_id — hiding it is refused with 422.
    r = api_client.post("/api/categories/split/hide")
    assert r.status_code == 422
    # "uncategorized" is no longer seeded → 404 (not structural, just absent).
    r = api_client.post("/api/categories/uncategorized/hide")
    assert r.status_code == 404


def test_api_hide_unknown_key_404(api_client):
    r = api_client.post("/api/categories/does_not_exist/hide")
    assert r.status_code == 404


def test_api_list_categories_excludes_hidden_by_default(api_client):
    api_client.post("/api/categories/cat_13095ba70000/hide")
    r = api_client.get("/api/categories")
    assert r.status_code == 200
    ids = [c["id"] for c in r.json()]
    assert "cat_13095ba70000" not in ids
    assert "cat_db5642ab0000" in ids


def test_api_list_categories_include_hidden(api_client):
    api_client.post("/api/categories/cat_13095ba70000/hide")
    r = api_client.get("/api/categories?include_hidden=true")
    assert r.status_code == 200
    ids = [c["id"] for c in r.json()]
    assert "cat_13095ba70000" in ids


# ── Checkpoint coverage ───────────────────────────────────────────────────────────


def test_hide_is_checkpoint_covered(env):
    """Hiding is captured by an active turn checkpoint — the categories table is NOT in
    ai.checkpoint._EXCLUDED_TABLES (plan 4a task 2; DB-backed, replacing the old
    file-hook-on-categories.json check). Mirrors
    tests/db/test_merge_journal_store_db.py's own checkpoint-hook test."""
    from backend.ai import checkpoint as ckpt

    ctx, token = ckpt.begin_turn("s1", 0, "t")
    try:
        cat.set_category_hidden("cat_13095ba70000", hidden=True)
    finally:
        entry = ckpt.end_turn(ctx, token)

    assert entry is not None, "hide is NOT checkpoint-covered"
    assert "categories" in entry["db_tables"]


# ── Tool smoke tests ──────────────────────────────────────────────────────────────
# ``set_category_hidden`` (the AI tool) was renamed to ``set_categories_hidden`` 2026-07-19
# (owner complaint — a singular name that silently accepted a list is a trap); param
# ``category_id`` → ``category_ids``. The old tool name still dispatches via the registry
# alias table — see ``test_tool_set_category_hidden_old_name_still_dispatches_via_alias``.


@pytest.mark.asyncio
async def test_tool_set_category_hidden(env):
    from backend.ai.tools.categories import set_categories_hidden as tool_fn

    result_text, proposed_change = await tool_fn(
        {"category_ids": "cat_13095ba70000", "hidden": True}
    )
    data = json.loads(result_text)
    assert data == {"ok": True, "category_id": "cat_13095ba70000", "hidden": True}
    assert proposed_change is None


@pytest.mark.asyncio
async def test_tool_set_category_hidden_old_name_still_dispatches_via_alias(env):
    """A tool name is persisted data (agents.json/skills) — the OLD name must keep
    dispatching, through the registry alias table, to the renamed handler."""
    from backend.ai.tools import execute_tool

    result_text, proposed_change = await execute_tool(
        "set_categories_hidden", {"category_ids": "cat_13095ba70000", "hidden": True}
    )
    data = json.loads(result_text)
    assert data == {"ok": True, "category_id": "cat_13095ba70000", "hidden": True}
    assert proposed_change is None


@pytest.mark.asyncio
async def test_tool_set_category_hidden_structural_returns_error(env):
    from backend.ai.tools.categories import set_categories_hidden as tool_fn

    # "split" is structural — hiding it is refused.
    result_text, _ = await tool_fn({"category_ids": "split", "hidden": True})
    data = json.loads(result_text)
    assert data["ok"] is False
    assert "structural" in data["error"].lower()


@pytest.mark.asyncio
async def test_tool_list_categories_include_hidden(env):
    from backend.ai.tools.categories import list_categories as tool_fn

    cat.set_category_hidden("cat_13095ba70000", hidden=True)

    result_default, _ = await tool_fn({})
    ids_default = [c["id"] for c in json.loads(result_default)["categories"]]
    assert "cat_13095ba70000" not in ids_default

    result_all, _ = await tool_fn({"include_hidden": True})
    ids_all = [c["id"] for c in json.loads(result_all)["categories"]]
    assert "cat_13095ba70000" in ids_all


# ── Tool batch (`category_ids` as a list) — hide/unhide SEVERAL in ONE call ──────


@pytest.mark.asyncio
async def test_tool_set_category_hidden_batch_hides_several(env):
    """'Hide these 5 categories' — a LIST for category_ids, one shared hidden value,
    no per-item payload repetition."""
    from backend.ai.tools.categories import set_categories_hidden as tool_fn

    ids = ["cat_13095ba70000", "cat_db5642ab0000", "cat_504bed6e0000"]
    result_text, proposed_change = await tool_fn({"category_ids": ids, "hidden": True})
    data = json.loads(result_text)

    assert proposed_change is None
    assert data["ok"] is True
    assert data["count"] == 3
    assert len(data["results"]) == 3
    assert all(r["ok"] and r["hidden"] is True for r in data["results"])
    for cid in ids:
        assert cid in cat.hidden_categories()


@pytest.mark.asyncio
async def test_tool_set_category_hidden_batch_unhides_several(env):
    from backend.ai.tools.categories import set_categories_hidden as tool_fn

    ids = ["cat_13095ba70000", "cat_db5642ab0000"]
    for cid in ids:
        cat.set_category_hidden(cid, hidden=True)

    result_text, _ = await tool_fn({"category_ids": ids, "hidden": False})
    data = json.loads(result_text)

    assert data["ok"] is True
    assert data["count"] == 2
    for cid in ids:
        assert cid not in cat.hidden_categories()


@pytest.mark.asyncio
async def test_tool_set_category_hidden_batch_partial_failure_is_legible(env):
    """One item hits the structural-key guard; the rest still succeed, and the caller can
    tell exactly which one failed and why."""
    from backend.ai.tools.categories import set_categories_hidden as tool_fn

    result_text, _ = await tool_fn(
        {"category_ids": ["split", "cat_13095ba70000", "no_such_category"], "hidden": True}
    )
    data = json.loads(result_text)

    assert data["ok"] is False
    assert data["count"] == 1
    assert len(data["results"]) == 3
    assert data["results"][0]["ok"] is False
    assert "structural" in data["results"][0]["error"].lower()
    assert data["results"][1]["ok"] is True
    assert data["results"][1]["category_id"] == "cat_13095ba70000"
    assert data["results"][2]["ok"] is False
    assert data["results"][2]["category_id"] == "no_such_category"
    assert "cat_13095ba70000" in cat.hidden_categories()


@pytest.mark.asyncio
async def test_tool_set_category_hidden_scalar_string_unchanged(env):
    """A bare string still returns the ORIGINAL single-item shape — the batch path is
    never taken just because the caller happens to pass one id."""
    from backend.ai.tools.categories import set_categories_hidden as tool_fn

    result_text, _ = await tool_fn({"category_ids": "cat_13095ba70000", "hidden": True})
    data = json.loads(result_text)
    assert data == {"ok": True, "category_id": "cat_13095ba70000", "hidden": True}
    assert "results" not in data
