"""GET /api/models must render a picker even when nothing is chosen yet.

Regression for a setup deadlock (real report, 2026-08-23): you add a provider, it reports
"Ready", and the Default-model dropdown says "No model matches" — because
``ModelConfigStore.resolved()`` RAISES while ``default_model`` is unset, the route let that
raise through as a 500, and the picker is the only place you can pick the model that would
stop it raising. The route's own docstring already promised "NEVER raises — the picker always
has a usable, non-empty list"; this pins that promise.

``resolved()`` keeps raising on purpose — it must never invent a vendor for a caller about to
send a request somewhere. The fix belongs in the route, which is the opposite case.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from backend.core.errors import ConfigurationError


def _client() -> TestClient:
    from fastapi import FastAPI

    from backend.api.models import router

    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


@pytest.fixture
def unconfigured(monkeypatch):
    """A store whose default model is unset — exactly the state after adding a provider."""
    from backend.db.model_config_store import ModelConfigStore

    def raise_unset(self):
        raise ConfigurationError(
            "AI provider is not configured. Choose one in Settings → AI Models."
        )

    monkeypatch.setattr(ModelConfigStore, "resolved", raise_unset)


@pytest.mark.usefixtures("unconfigured", "db_isolated")
def test_picker_still_answers_when_no_default_model_is_chosen():
    r = _client().get("/api/models")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["models"], "the picker must never be handed an empty list"
    assert all(m.get("id") for m in body["models"])


@pytest.mark.usefixtures("unconfigured", "db_isolated")
def test_picker_offers_the_configured_providers_models_not_a_blank_floor(monkeypatch):
    """A Claude Code CLI instance must be offered Claude models, not the OpenAI floor.

    With no default model there is no transport to read off ``resolved()``, so the route falls
    back to a CONFIGURED provider instead of a blank one. Getting this wrong is silent: the
    picker populates, just with another vendor's models, and nothing looks broken.
    """
    from backend.db.model_config_store import ModelConfigStore

    monkeypatch.setattr(ModelConfigStore, "configured_provider_keys", lambda self: ["claude_cli"])

    r = _client().get("/api/models")
    assert r.status_code == 200, r.text
    ids = {m["id"] for m in r.json()["models"]}
    assert {"sonnet", "opus"} <= ids, f"expected Claude Code CLI models, got {sorted(ids)}"


@pytest.mark.usefixtures("unconfigured", "db_isolated")
def test_the_union_picker_survives_it_too():
    r = _client().get("/api/models?all=1")
    assert r.status_code == 200, r.text
    assert r.json()["models"]
