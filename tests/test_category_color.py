"""
Tests for the category colour-persistence feature (services.categories + api/ledger).

Categories carry opaque ids (docs/adr/2026-07-05-category-opaque-ids.md) — color is a field
on the store row, addressed by id (never a label/slug).

Design contract:
  * ``set_category_color(category_id, "#rrggbb")`` persists the swatch colour on the store row, where
    ``category_id`` is the category's opaque id.
  * ``color`` must be a 6-digit hex string; anything else → ValidationError (422 over REST).
  * Unknown / deleted id → NotFoundError (404 over REST).
  * Writes are checkpoint-covered (store write fires ``core.io.fire_write_hook``).
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api.ledger import router as ledger_router
from backend.core.errors import NotFoundError, ValidationError
from backend.db.category_store import CategoryStore
from backend.db.profile_store import ProfileStore
from backend.models.category_defs import BUILTIN_CATEGORIES
from backend.services import categories as cat


@pytest.fixture
def env(tmp_path, monkeypatch, db_isolated):

    ProfileStore().set_raw({"base_currency": "SGD"})

    CategoryStore().bulk_insert(BUILTIN_CATEGORIES)

    def _profile():
        return ProfileStore().get_raw()

    def _store():
        return CategoryStore()

    return type("Env", (), {"profile": staticmethod(_profile), "store": staticmethod(_store)})


@pytest.fixture
def api_client(env):
    app = FastAPI()
    app.include_router(ledger_router)
    return TestClient(app)


# ── Service unit tests ────────────────────────────────────────────────────────────


def _color_of(category_id: str) -> str | None:
    return next((c["color"] for c in cat.get_all_categories() if c["id"] == category_id), None)


def test_set_category_color_persists_on_store_row(env):
    """set_category_color writes the color field directly to the store row."""
    out = cat.set_category_color("cat_13095ba70000", "#123ABC")
    assert out == {
        "ok": True,
        "category_id": "cat_13095ba70000",
        "color": "#123abc",
    }  # normalised lower-case
    # Store row reflects the new color.
    assert env.store().get("cat_13095ba70000")["color"] == "#123abc"
    # No profile override map written.
    assert "category_color_overrides" not in env.profile()


def test_set_color_same_api_for_custom(env):
    out = cat.create_category("Startup", color="#111111")
    category_id = out["id"]
    result = cat.set_category_color(category_id, "#abcdef")
    assert result == {"ok": True, "category_id": category_id, "color": "#abcdef"}
    assert _color_of(category_id) == "#abcdef"
    assert env.store().get(category_id)["color"] == "#abcdef"


def test_set_color_with_label_instead_of_id_not_found(env):
    """set_category_color takes an opaque id, not a label — passing a label raises
    NotFoundError (resolve_category_ref exists separately for label resolution; this
    function never resolves one implicitly)."""
    with pytest.raises(NotFoundError):
        cat.set_category_color("Food & Dining", "#3b86dd")


@pytest.mark.parametrize("bad", ["red", "#fff", "#12345", "#1234567", "3b86dd", "", "#GGGGGG"])
def test_invalid_color_rejected(env, bad):
    with pytest.raises(ValidationError, match="hex"):
        cat.set_category_color("cat_13095ba70000", bad)


def test_unknown_key_raises_not_found(env):
    with pytest.raises(NotFoundError):
        cat.set_category_color("cat_ghost", "#3b86dd")


def test_deleted_category_raises_not_found(env):
    """After deletion, the row is gone — setting color raises NotFoundError."""
    cat.delete_category("cat_13095ba70000")
    with pytest.raises(NotFoundError):
        cat.set_category_color("cat_13095ba70000", "#3b86dd")


def test_color_is_checkpoint_covered(env):
    """set_category_color's DB write is captured by an active turn checkpoint — the
    categories table is NOT in ai.checkpoint._EXCLUDED_TABLES (plan 4a task 2; DB-backed,
    replacing the old file-hook-on-categories.json check). Mirrors
    tests/db/test_merge_journal_store_db.py's own checkpoint-hook test."""
    from backend.ai import checkpoint as ckpt

    ctx, token = ckpt.begin_turn("s1", 0, "t")
    try:
        cat.set_category_color("cat_13095ba70000", "#3b86dd")
    finally:
        entry = ckpt.end_turn(ctx, token)
    assert entry is not None
    assert "categories" in entry["db_tables"]


# ── REST API smoke tests ──────────────────────────────────────────────────────────


def test_api_set_color_endpoint(api_client):
    r = api_client.post("/api/categories/cat_13095ba70000/color", json={"color": "#3b86dd"})
    assert r.status_code == 200
    assert r.json() == {"ok": True, "category_id": "cat_13095ba70000", "color": "#3b86dd"}


def test_api_set_color_invalid_422(api_client):
    r = api_client.post("/api/categories/cat_13095ba70000/color", json={"color": "nope"})
    assert r.status_code == 422


def test_api_set_color_unknown_404(api_client):
    r = api_client.post("/api/categories/cat_ghost/color", json={"color": "#3b86dd"})
    assert r.status_code == 404


def test_api_list_reflects_new_color(api_client):
    api_client.post("/api/categories/cat_13095ba70000/color", json={"color": "#abcdef"})
    r = api_client.get("/api/categories")
    food = next(c for c in r.json() if c["id"] == "cat_13095ba70000")
    assert food["color"] == "#abcdef"
