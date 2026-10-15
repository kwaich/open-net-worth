"""Smokes for the preferred-model shortlist: store + GET/PUT /api/preferred-models.

Succeeds test_routing_config_api, deleted with automatic model switching (ADR
2026-08-06-auto-model-switching-removed). Mirrors test_model_config_api's minimal-app
pattern (the single app-wide require_auth dependency, only the router under test
mounted). PreferredModelsStore is DB-backed (a row in the shared `settings` table via
NamespacedSettingsStore, plan 4d task 2) — the store is isolated via db_isolated, which
redirects the process-wide engine singleton to a fresh, empty database; it must never
touch the live tree.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from backend.core import config


@pytest.fixture
def isolated(db_isolated, monkeypatch):
    for key in ("AUTH_DISABLED", "AUTH_PASSWORD", "AUTH_PASSWORD_HASH", "AUTH_API_TOKEN"):
        monkeypatch.delenv(key, raising=False)
    config._settings = None
    yield
    config._settings = None


def _client() -> TestClient:
    from fastapi import Depends, FastAPI

    from backend.api._deps import require_auth
    from backend.api.preferred_models import router

    app = FastAPI(dependencies=[Depends(require_auth)])
    app.include_router(router)
    return TestClient(app)


# ── store ─────────────────────────────────────────────────────────────────────


def test_absent_file_resolves_to_an_empty_list(isolated):
    from backend.db.preferred_models_store import PreferredModelsStore

    assert PreferredModelsStore().resolved() == {"models": []}


def test_replace_round_trips_and_keeps_order(isolated):
    from backend.db.preferred_models_store import PreferredModelsStore

    store = PreferredModelsStore()
    store.replace(["anthropic:claude-haiku-4-5", "openai:gpt-4o-mini"])
    assert PreferredModelsStore().resolved() == {
        "models": ["anthropic:claude-haiku-4-5", "openai:gpt-4o-mini"]
    }


def test_replace_dedupes_trims_and_drops_non_strings(isolated):
    from backend.db.preferred_models_store import PreferredModelsStore

    out = PreferredModelsStore().replace(
        ["  anthropic:a  ", "anthropic:a", "", None, 7, "openai:b"]
    )
    assert out == {"models": ["anthropic:a", "openai:b"]}


def test_the_list_is_bounded(isolated):
    from backend.db.preferred_models_store import PreferredModelsStore

    out = PreferredModelsStore().replace([f"p:{i}" for i in range(200)])
    assert len(out["models"]) == 50
    assert len(PreferredModelsStore().replace(["x" * 500])["models"][0]) == 200


# ── endpoint ──────────────────────────────────────────────────────────────────


def test_get_returns_the_empty_default_shape(isolated):
    assert _client().get("/api/preferred-models").json() == {"models": []}


def test_put_replaces_and_get_reflects_it(isolated):
    client = _client()
    put = client.put("/api/preferred-models", json={"models": ["anthropic:claude-haiku-4-5"]})
    assert put.status_code == 200
    assert put.json() == {"models": ["anthropic:claude-haiku-4-5"]}
    assert client.get("/api/preferred-models").json() == {"models": ["anthropic:claude-haiku-4-5"]}


def test_put_with_an_empty_list_clears_the_shortlist(isolated):
    client = _client()
    client.put("/api/preferred-models", json={"models": ["anthropic:claude-haiku-4-5"]})
    assert client.put("/api/preferred-models", json={"models": []}).json() == {"models": []}


def test_put_requires_the_models_field(isolated):
    assert _client().put("/api/preferred-models", json={}).status_code == 422


# ── the routing surface is gone ───────────────────────────────────────────────


def test_the_routing_config_tools_no_longer_exist():
    """Deleted with no alias (owner decision) — nothing may re-register them."""
    from backend.ai.tools.registry import build_tool_schemas

    names = {schema["name"] for schema in build_tool_schemas()}
    assert "get_routing_config" not in names
    assert "update_routing_config" not in names


def test_the_routing_engine_module_is_gone():
    with pytest.raises(ImportError):
        import backend.ai.routing  # noqa: F401
