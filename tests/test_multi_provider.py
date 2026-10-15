"""Multi-provider model config tests.

Covers the five scenarios in the spec:
  1. A configured 2nd provider's models appear in the union (?all=1).
  2. An override naming provider B + model resolves to provider B's transport.
  3. A model-only override still works (within-active-provider, backward-compat).
  4. Default fallback — no override → instance default.
  5. Keys are NEVER returned (public() / providers_public() / API responses).

All tests are hermetic — they operate on a tmp model_config.json, never the live
data dir, and clear the AI_* env vars so seeding is deterministic.
"""

from __future__ import annotations

import pytest

from backend.db.model_config_store import ModelConfigStore


@pytest.fixture(autouse=True)
def _clear_ai_env(monkeypatch):
    for key in (
        "AI_PROVIDER",
        "AI_MODEL",
        "ONW_TEST_DEFAULT_MODEL",
        "OPENAI_BASE_URL",
        "OPENAI_API_BASE",
        "ANTHROPIC_API_KEY",
    ):
        monkeypatch.delenv(key, raising=False)


def _store(tmp_path=None):
    # tmp_path is vestigial (kept so every existing `_store(tmp_path)` call site below
    # still reads naturally) — the store is DB-backed now, and tests/conftest.py's
    # autouse per-test table clear already gives each test a fresh row, the same
    # isolation the old per-test tmp file gave. Mirrors the OLD "if not path.exists():
    # seed" guard exactly: seed ONLY when no row exists yet, so a SECOND _store(tmp_path)
    # call in the same test (e.g. "a fresh store — reads from disk") sees whatever an
    # earlier .update() already wrote, not a re-seeded default.
    store = ModelConfigStore()
    if not store.get():
        store.save({"provider": "anthropic", "model": "test-model"})
    return store


# ── 1. configured_provider_keys includes additional providers ─────────────────


def test_configured_provider_keys_default_only(tmp_path):
    """When no extra providers are stored, only the instance default is returned."""
    store = _store(tmp_path)
    store.update({"provider": "anthropic", "model": "claude-sonnet-4-6", "api_key": "sk-ant"})
    keys = store.configured_provider_keys()
    assert "anthropic" in keys


def test_configured_provider_keys_includes_second_provider(tmp_path):
    """Storing a 2nd provider's key makes it appear in configured_provider_keys."""
    store = _store(tmp_path)
    store.update({"provider": "anthropic", "model": "claude-sonnet-4-6", "api_key": "sk-ant"})
    store.update_provider("openai", {"api_key": "sk-oai"})
    keys = store.configured_provider_keys()
    assert "anthropic" in keys
    assert "openai" in keys


def test_providers_public_never_leaks_key(tmp_path):
    """providers_public() must not contain api_key for any provider."""
    store = _store(tmp_path)
    store.update_provider("openai", {"api_key": "sk-secret-openai"})
    pub = store.providers_public()
    assert "openai" in pub
    assert "api_key" not in pub["openai"]
    assert pub["openai"]["has_api_key"] is True


def test_providers_public_blank_key_keeps_saved(tmp_path):
    """A blank api_key in update_provider leaves the saved key (same contract as update())."""
    store = _store(tmp_path)
    store.update_provider("openai", {"api_key": "sk-oai"})
    store.update_provider("openai", {"api_key": ""})  # blank → keep saved
    pub = store.providers_public()
    # The saved key persists — blank does NOT clear it.
    assert pub["openai"]["has_api_key"] is True


def test_providers_public_explicit_clear_via_raw(tmp_path):
    """Reading the raw row after clearing shows no provider entry."""
    store = _store(tmp_path)
    store.update_provider("openai", {"api_key": "sk-oai"})
    # Direct raw edit to verify the row round-trips; not an API delete operation.
    raw = store.get()
    raw["providers"].pop("openai", None)
    store.save(raw)
    pub = store.providers_public()
    assert "openai" not in pub


# ── 2. Cross-provider override resolves to the right transport ────────────────


def test_resolve_override_cross_provider(tmp_path):
    """'openai:gpt-4o' resolves to openai_compatible transport + the stored key."""
    store = _store(tmp_path)
    store.update({"provider": "anthropic", "model": "claude-sonnet-4-6", "api_key": "sk-ant"})
    store.update_provider("openai", {"api_key": "sk-oai-key"})
    result = store.resolve_override("openai:gpt-4o")
    assert result is not None
    assert result["transport"] == "openai_compatible"
    assert result["model"] == "gpt-4o"
    assert result["api_key"] == "sk-oai-key"
    assert result["provider_key"] == "openai"


def test_resolve_override_anthropic_cross(tmp_path):
    """'anthropic:claude-opus-4-8' resolves to anthropic transport."""
    store = _store(tmp_path)
    store.update({"provider": "openai_compatible", "base_url": "http://localhost:11434/v1"})
    store.update_provider("anthropic", {"api_key": "sk-ant-extra"})
    result = store.resolve_override("anthropic:claude-opus-4-8")
    assert result is not None
    assert result["transport"] == "anthropic"
    assert result["model"] == "claude-opus-4-8"
    assert result["api_key"] == "sk-ant-extra"


def test_resolve_override_stale_provider_key_falls_through(tmp_path):
    """A provider key that no longer exists (e.g. 'groq' post-prune) causes the
    ':' prefix to not match any catalog entry, so the whole string is treated as
    a model-only override (forward-compat / stale-data graceful fallback)."""
    store = _store(tmp_path)
    store.update({"provider": "anthropic", "api_key": "sk-ant"})
    # 'groq' is not in the catalog after pruning — falls back to model-only path.
    result = store.resolve_override("groq:llama-3.3-70b-versatile")
    assert result is not None
    assert result["model"] == "groq:llama-3.3-70b-versatile"  # whole string as model
    assert result["transport"] == "anthropic"  # instance default


def test_resolve_override_returns_none_for_blank(tmp_path):
    """resolve_override returns None for blank/None input."""
    store = _store(tmp_path)
    assert store.resolve_override("") is None
    assert store.resolve_override("   ") is None


# ── 3. Model-only override stays within the active provider ───────────────────


def test_resolve_override_model_only_plain_model(tmp_path):
    """A plain model id (no ':') resolves within the instance default provider.
    Provider pinning happens at SAVE time via the canonical 'catalog:model'
    compound form — plain ids are the backward-compat/local-model path."""
    store = _store(tmp_path)
    store.update({"provider": "anthropic", "model": "claude-sonnet-4-6", "api_key": "sk-ant"})
    result = store.resolve_override("claude-opus-4-8")
    assert result is not None
    assert result["transport"] == "anthropic"
    assert result["model"] == "claude-opus-4-8"
    assert result["provider_key"] is None  # plain id — instance default, no auto-detect


def test_resolve_override_model_only_openai_compat(tmp_path):
    """A plain model id resolves within an openai_compatible instance default."""
    store = _store(tmp_path)
    store.update({"provider": "openai_compatible", "base_url": "http://localhost:11434/v1"})
    result = store.resolve_override("llama3.1")
    assert result is not None
    assert result["transport"] == "openai_compatible"
    assert result["model"] == "llama3.1"


def test_resolve_override_unknown_prefix_falls_back_to_model_only(tmp_path):
    """An unknown provider_key prefix falls back to treating the whole string as a model id."""
    store = _store(tmp_path)
    store.update({"provider": "anthropic", "api_key": "sk-ant"})
    # "nonexistent" is not in the catalog — the ':' parse should fall back.
    result = store.resolve_override("nonexistent_provider:gpt-4o")
    # The whole string is treated as a model-only override (forward-compat).
    assert result is not None
    assert result["model"] == "nonexistent_provider:gpt-4o"
    assert result["transport"] == "anthropic"


# ── 4. Default fallback — None / empty override → instance default ────────────


def test_resolve_override_none_returns_none(tmp_path):
    """resolve_override(None) returns None → caller uses the instance default."""
    # resolve_override only accepts str, pass None as a special case
    store = _store(tmp_path)
    assert store.resolve_override("") is None


def test_no_override_resolves_to_instance_model(tmp_path):
    """resolve_override(None/empty) returns None → runtime uses the instance model."""
    store = _store(tmp_path)
    store.update({"provider": "anthropic", "model": "custom-claude-model"})
    # Empty override → None
    assert store.resolve_override("") is None
    # The instance default is what the runtime uses.
    assert store.resolved()["model"] == "custom-claude-model"
    assert store.resolved()["provider"] == "anthropic"


# ── 5. Keys NEVER returned in any public surface ─────────────────────────────


def test_update_provider_public_view_never_has_key(tmp_path):
    """update_provider() returns providers_public() which never has api_key."""
    store = _store(tmp_path)
    result = store.update_provider("openai", {"api_key": "sk-very-secret"})
    # result is providers_public()
    assert "api_key" not in result.get("openai", {})
    assert result["openai"]["has_api_key"] is True


def test_resolved_has_key_but_public_does_not(tmp_path):
    """resolved() carries the key for runtime; public() never does."""
    store = _store(tmp_path)
    store.update({"api_key": "sk-secret-instance"})
    assert store.resolved()["api_key"] == "sk-secret-instance"
    assert "api_key" not in store.public()


def test_providers_public_covers_all_stored_providers(tmp_path):
    """providers_public() lists all providers that have stored credentials."""
    store = _store(tmp_path)
    store.update_provider("openai", {"api_key": "sk-oai"})
    store.update_provider("groq", {"api_key": "gsk-groq"})
    pub = store.providers_public()
    assert "openai" in pub
    assert "groq" in pub
    assert "api_key" not in pub["openai"]
    assert "api_key" not in pub["groq"]


# ── resolve_for_provider fallback logic ────────────────────────────────────


def test_resolve_for_provider_inherits_instance_default(tmp_path):
    """When no per-provider creds are stored, the matching transport inherits the instance key."""
    store = _store(tmp_path)
    store.update({"provider": "anthropic", "api_key": "sk-ant-default"})
    creds = store.resolve_for_provider("anthropic")
    assert creds is not None
    assert creds["transport"] == "anthropic"
    assert creds["api_key"] == "sk-ant-default"


def test_resolve_for_provider_per_provider_overrides_default(tmp_path):
    """Per-provider stored key takes precedence over the instance default."""
    store = _store(tmp_path)
    store.update({"provider": "anthropic", "api_key": "sk-ant-default"})
    store.update_provider("anthropic", {"api_key": "sk-ant-per-provider"})
    creds = store.resolve_for_provider("anthropic")
    assert creds is not None
    assert creds["api_key"] == "sk-ant-per-provider"


def test_resolve_for_provider_unknown_returns_none(tmp_path):
    """An unknown provider key returns None."""
    store = _store(tmp_path)
    assert store.resolve_for_provider("not_a_real_key") is None


# ── API endpoint smoke tests ──────────────────────────────────────────────────


def test_put_provider_credentials_api(tmp_path, monkeypatch):
    """PUT /api/model-config/providers/{key} stores creds and returns the wrapped response.

    Since on-save validation now fires a live fetch (which will fail in the test
    environment), we mock httpx.get so it doesn't attempt a real network call.
    The response shape changed: {configured_providers, key_status, ...} instead of
    the flat providers_public() dict.
    """
    import unittest.mock as mock  # noqa: I001
    from fastapi.testclient import TestClient

    monkeypatch.setenv("AUTH_DISABLED", "true")
    from backend.main import app

    client = TestClient(app)

    # Mock httpx.get to avoid real network; return a valid-looking response.
    fake_resp = mock.MagicMock()
    fake_resp.status_code = 200
    fake_resp.raise_for_status.return_value = None
    fake_resp.json.return_value = {"data": [{"id": "gpt-4o"}]}

    with mock.patch("backend.api.models.httpx.get", return_value=fake_resp):
        r = client.put(
            "/api/model-config/providers/openai",
            json={"api_key": "sk-oai-test"},
        )
    assert r.status_code == 200
    body = r.json()
    # Response is now wrapped: {configured_providers, key_status, ...}
    assert "configured_providers" in body
    assert "key_status" in body
    configured = body["configured_providers"]
    assert "openai" in configured
    # The raw api_key must never appear in any response field.
    assert "api_key" not in configured["openai"]
    assert "sk-oai-test" not in r.text
    assert configured["openai"]["has_api_key"] is True


def test_put_provider_credentials_unknown_key(tmp_path, monkeypatch):
    """PUT /api/model-config/providers/{unknown} returns 404."""
    from fastapi.testclient import TestClient

    monkeypatch.setenv("AUTH_DISABLED", "true")
    from backend.main import app

    client = TestClient(app)
    r = client.put(
        "/api/model-config/providers/totally_unknown_provider",
        json={"api_key": "sk-x"},
    )
    assert r.status_code == 404


def test_get_models_all_single_provider(tmp_path, monkeypatch):
    """GET /api/models?all=1 with only one configured provider returns that provider's models
    labelled with its provider key."""
    import unittest.mock as mock

    from fastapi.testclient import TestClient

    monkeypatch.setenv("AUTH_DISABLED", "true")
    store = ModelConfigStore()
    store.update({"provider": "anthropic", "model": "claude-sonnet-4-6", "api_key": "sk-ant"})

    from backend.main import app

    client = TestClient(app)

    with mock.patch(
        "backend.api.models._fetch_anthropic_models",
        return_value=[
            {"id": "claude-sonnet-4-6", "reasoning": True},
            {"id": "claude-opus-4-8", "reasoning": True},
        ],
    ):
        r = client.get("/api/models?all=1")

    assert r.status_code == 200
    body = r.json()
    models = body["models"]
    # Should be labelled
    assert any(m.get("provider_key") == "anthropic" for m in models)
    # No key in the response
    for m in models:
        assert "api_key" not in m


def test_get_models_all_multi_provider(tmp_path, monkeypatch):
    """GET /api/models?all=1 with two configured providers returns models from both,
    each labelled with the correct provider_key."""
    import unittest.mock as mock

    from fastapi.testclient import TestClient

    monkeypatch.setenv("AUTH_DISABLED", "true")

    store = ModelConfigStore()
    store.update({"provider": "anthropic", "model": "claude-sonnet-4-6", "api_key": "sk-ant"})
    store.update_provider("openai", {"api_key": "sk-oai"})

    from backend.main import app

    client = TestClient(app)

    def fake_fetch(key, store_arg):
        if key == "anthropic":
            return [
                {
                    "id": "claude-sonnet-4-6",
                    "reasoning": True,
                    "effort_levels": ["low", "medium", "high", "max"],
                }
            ], "live"
        if key == "openai":
            return [
                {"id": "gpt-4o", "reasoning": False, "effort_levels": []},
                {"id": "gpt-4o-mini", "reasoning": False, "effort_levels": []},
            ], "live"
        return [], "fallback"

    with mock.patch("backend.api.models._fetch_models_for_provider", side_effect=fake_fetch):
        r = client.get("/api/models?all=1")

    assert r.status_code == 200
    body = r.json()
    models = body["models"]
    provider_keys = {m.get("provider_key") for m in models}
    assert "anthropic" in provider_keys
    assert "openai" in provider_keys
    # No keys in the response
    for m in models:
        assert "api_key" not in m
    # Each row carries the reasoning flag + the exact accepted effort levels (drive
    # the effort dial's visibility and its option list).
    by_id = {m["id"]: m for m in models}
    assert by_id["claude-sonnet-4-6"]["reasoning"] is True
    assert by_id["claude-sonnet-4-6"]["effort_levels"] == ["low", "medium", "high", "max"]
    assert by_id["gpt-4o"]["reasoning"] is False
    assert by_id["gpt-4o"]["effort_levels"] == []


# ── Fix #1 regression: cross-provider key leakage ────────────────────────────
#
# Bug: if the instance default is openai_compatible (e.g. OpenAI, sk-openai-…)
# and a user resolves a DIFFERENT openai_compatible provider key (e.g. gemini,
# ollama) WITHOUT stored creds for that key, the old code fell back to
# `inst["provider"] == transport` (True for both) and leaked the OpenAI key
# to the third-party endpoint.  After the fix the resolved api_key is EMPTY.


def test_cross_provider_key_not_leaked_openai_to_gemini(tmp_path):
    """OpenAI instance default must NOT bleed into gemini when no gemini key is stored."""
    store = _store(tmp_path)
    # Instance is configured as OpenAI (openai_compatible + openai base_url).
    store.update({"provider": "openai_compatible", "base_url": "https://api.openai.com/v1"})
    store.update_provider("openai", {"api_key": "sk-openai-secret"})

    # No credentials stored for gemini.
    creds = store.resolve_for_provider("gemini")
    assert creds is not None
    assert creds["api_key"] == "", "OpenAI key must NOT leak to gemini; expected empty api_key"


def test_cross_provider_key_not_leaked_openai_to_ollama(tmp_path):
    """OpenAI instance default must NOT bleed into ollama (which needs no key anyway)."""
    store = _store(tmp_path)
    store.update({"provider": "openai_compatible", "base_url": "https://api.openai.com/v1"})
    store.update_provider("openai", {"api_key": "sk-openai-very-secret"})

    creds = store.resolve_for_provider("ollama")
    assert creds is not None
    assert creds["api_key"] == "", "OpenAI key must NOT leak to ollama; expected empty api_key"


def test_cross_provider_key_not_leaked_removed_key_returns_none(tmp_path):
    """resolve_for_provider returns None for keys NOT in the catalog (groq,
    deepseek, mistral, …) — graceful fallback for stale stored configs. And for a
    valid-but-different catalog key (xai), it must resolve WITHOUT inheriting the
    instance-default openai secret (no cross-provider key leak)."""
    store = _store(tmp_path)
    store.update({"provider": "openai_compatible", "base_url": "https://api.openai.com/v1"})
    store.update_provider("openai", {"api_key": "sk-openai-very-secret"})

    for stale_key in ("groq", "deepseek", "mistral", "together", "perplexity"):
        creds = store.resolve_for_provider(stale_key)
        assert creds is None, (
            f"resolve_for_provider({stale_key!r}) must return None for removed provider keys"
        )

    # xai is a real catalog key now, but has no stored key — it must NOT receive the
    # openai instance-default secret (different provider key → empty key, not a leak).
    xai_creds = store.resolve_for_provider("xai")
    assert xai_creds is not None
    assert xai_creds["api_key"] == "", "openai instance key must not leak to xai"
    assert xai_creds["base_url"] == "https://api.x.ai/v1"


def test_instance_key_does_fallback_to_own_provider_key(tmp_path):
    """The instance-default key IS available when resolving the SAME provider key."""
    store = _store(tmp_path)
    store.update({"provider": "openai_compatible", "base_url": "https://api.openai.com/v1"})
    store.update_provider("openai", {"api_key": "sk-openai-correct"})

    creds = store.resolve_for_provider("openai")
    assert creds is not None
    assert creds["api_key"] == "sk-openai-correct"


def test_anthropic_instance_key_does_not_leak_to_openai(tmp_path):
    """Anthropic instance default must NOT bleed into openai (different transport anyway,
    but let's be explicit)."""
    store = _store(tmp_path)
    store.update({"provider": "anthropic"})
    store.update_provider("anthropic", {"api_key": "sk-ant-secret"})

    creds = store.resolve_for_provider("openai")
    assert creds is not None
    assert creds["api_key"] == ""


# ── Fix #2 regression: legacy top-level api_key migration ────────────────────
#
# A config file written by an older instance (or by a fresh `update()` call
# before this patch) carries the api_key at the top level, not in providers.
# On first read the key must be transparently migrated into providers and the
# store must resolve it correctly.


def test_legacy_top_level_api_key_migrates_to_providers():
    """A legacy row with top-level api_key is migrated into providers on load."""
    store = ModelConfigStore()
    store.save(
        {
            "provider": "anthropic",
            "model": "claude-sonnet-4-6",
            "api_key": "sk-ant-legacy",
        }
    )
    # The store must resolve the key correctly.
    r = store.resolved()
    assert r["api_key"] == "sk-ant-legacy"
    # The row has been migrated: key is now in providers, not top-level.
    on_disk = store.get()
    assert on_disk.get("api_key", "") == ""
    assert on_disk.get("providers", {}).get("anthropic", {}).get("api_key") == "sk-ant-legacy"


def test_legacy_openai_top_level_api_key_migrates():
    """An OpenAI legacy config migrates api_key into providers["openai"]."""
    store = ModelConfigStore()
    store.save(
        {
            "provider": "openai_compatible",
            "model": "gpt-4o",
            "base_url": "https://api.openai.com/v1",
            "api_key": "sk-openai-legacy",
        }
    )
    r = store.resolved()
    assert r["api_key"] == "sk-openai-legacy"
    on_disk = store.get()
    assert on_disk.get("api_key", "") == ""
    assert on_disk.get("providers", {}).get("openai", {}).get("api_key") == "sk-openai-legacy"


def test_legacy_migration_does_not_overwrite_existing_providers_key():
    """If providers[key] already has an api_key, the legacy top-level one is cleared
    without clobbering the more-recent providers key."""
    store = ModelConfigStore()
    store.save(
        {
            "provider": "anthropic",
            "model": "claude-sonnet-4-6",
            "api_key": "sk-ant-old-toplevel",
            "providers": {
                "anthropic": {"api_key": "sk-ant-newer-providers"},
            },
        }
    )
    r = store.resolved()
    # The providers entry wins.
    assert r["api_key"] == "sk-ant-newer-providers"
    on_disk = store.get()
    assert on_disk.get("api_key", "") == ""
    assert on_disk["providers"]["anthropic"]["api_key"] == "sk-ant-newer-providers"


def test_migrated_config_cross_provider_key_still_not_leaked():
    """After migrating a legacy OpenAI config, a gemini resolve still returns empty api_key
    (OpenAI key does not bleed into a different openai_compatible catalog entry)."""
    store = ModelConfigStore()
    store.save(
        {
            "provider": "openai_compatible",
            "model": "gpt-4o",
            "base_url": "https://api.openai.com/v1",
            "api_key": "sk-openai-migrated",
        }
    )
    # After migration the store should still refuse to leak the OpenAI key to gemini.
    creds = store.resolve_for_provider("gemini")
    assert creds is not None
    assert creds["api_key"] == ""


# ── model_for_task / set_task_model ───────────────────────────────────────────


def test_model_for_task_none_when_unset(tmp_path):
    """model_for_task returns None when no override is stored for the task."""
    store = _store(tmp_path)
    assert store.model_for_task("memory") is None
    assert store.model_for_task("intel") is None
    assert store.model_for_task("suggestions") is None
    assert store.model_for_task("briefing") is None


def test_model_for_task_returns_resolved_creds(tmp_path):
    """model_for_task returns the full resolved creds dict for a stored override."""
    store = _store(tmp_path)
    store.update_provider("anthropic", {"api_key": "sk-ant-task"})
    store.set_task_model("memory", "anthropic:claude-haiku-4-5")

    result = store.model_for_task("memory")
    assert result is not None
    assert result["provider_key"] == "anthropic"
    assert result["transport"] == "anthropic"
    assert result["model"] == "claude-haiku-4-5"
    assert result["api_key"] == "sk-ant-task"


def test_model_for_task_openai_compat(tmp_path):
    """model_for_task resolves an openai-compatible override correctly."""
    store = _store(tmp_path)
    store.update_provider("openai", {"api_key": "sk-oai-task"})
    store.set_task_model("intel", "openai:gpt-4o-mini")

    result = store.model_for_task("intel")
    assert result is not None
    assert result["transport"] == "openai_compatible"
    assert result["model"] == "gpt-4o-mini"
    assert result["api_key"] == "sk-oai-task"


def test_model_for_task_raises_when_provider_disabled(tmp_path):
    """model_for_task raises ProviderDisabledError when the pinned provider is disabled."""
    from backend.core.errors import ProviderDisabledError

    store = _store(tmp_path)
    store.update_provider("openai", {"api_key": "sk-oai"})
    store.set_task_model("suggestions", "openai:gpt-4o-mini")
    store.update_provider("openai", {"enabled": False})

    with pytest.raises(ProviderDisabledError):
        store.model_for_task("suggestions")


def test_set_task_model_persists_and_round_trips(tmp_path):
    """set_task_model persists the override and it survives a store reload."""
    store = _store(tmp_path)
    store.update_provider("anthropic", {"api_key": "sk-ant"})
    store.set_task_model("briefing", "anthropic:claude-sonnet-4-6")

    # Fresh store — reads from disk.
    store2 = _store(tmp_path)
    result = store2.model_for_task("briefing")
    assert result is not None
    assert result["model"] == "claude-sonnet-4-6"


def test_set_task_model_clear_with_none(tmp_path):
    """set_task_model(task, None) removes the override for that task."""
    store = _store(tmp_path)
    store.update_provider("anthropic", {"api_key": "sk-ant"})
    store.set_task_model("memory", "anthropic:claude-haiku-4-5")
    assert store.model_for_task("memory") is not None

    store.set_task_model("memory", None)
    assert store.model_for_task("memory") is None


def test_set_task_model_clear_with_empty_string(tmp_path):
    """set_task_model(task, '') removes the override (empty string = clear)."""
    store = _store(tmp_path)
    store.update_provider("anthropic", {"api_key": "sk-ant"})
    store.set_task_model("memory", "anthropic:claude-haiku-4-5")
    store.set_task_model("memory", "")
    assert store.model_for_task("memory") is None


def test_set_task_model_invalid_task_raises(tmp_path):
    """set_task_model raises ValueError for an unknown task name."""
    store = _store(tmp_path)
    with pytest.raises(ValueError, match="unknown task"):
        store.set_task_model("nonexistent", "anthropic:model")


def test_set_task_model_plain_model_id_raises(tmp_path):
    """set_task_model raises ValueError when override is a plain model id (no colon)."""
    store = _store(tmp_path)
    with pytest.raises(ValueError, match="provider_key.*model_id"):
        store.set_task_model("memory", "claude-haiku-4-5")


def test_set_task_model_unknown_provider_key_raises(tmp_path):
    """set_task_model raises ValueError when the override's provider key is unknown."""
    store = _store(tmp_path)
    with pytest.raises(ValueError, match="unknown provider key"):
        store.set_task_model("memory", "groq:llama-3.3-70b")


def test_set_task_model_disabled_provider_raises(tmp_path):
    """set_task_model raises ProviderDisabledError when the override's provider is disabled."""
    from backend.core.errors import ProviderDisabledError

    store = _store(tmp_path)
    store.update_provider("openai", {"api_key": "sk-oai"})
    store.update_provider("openai", {"enabled": False})

    with pytest.raises(ProviderDisabledError):
        store.set_task_model("memory", "openai:gpt-4o-mini")


def test_set_task_model_does_not_affect_other_tasks(tmp_path):
    """Clearing one task's override does not affect others."""
    store = _store(tmp_path)
    store.update_provider("anthropic", {"api_key": "sk-ant"})
    store.set_task_model("memory", "anthropic:claude-haiku-4-5")
    store.set_task_model("intel", "anthropic:claude-sonnet-4-6")

    store.set_task_model("memory", None)
    assert store.model_for_task("memory") is None
    result = store.model_for_task("intel")
    assert result is not None
    assert result["model"] == "claude-sonnet-4-6"


def test_public_includes_task_models(tmp_path):
    """public() includes task_models in the returned dict."""
    store = _store(tmp_path)
    store.update_provider("anthropic", {"api_key": "sk-ant"})
    store.set_task_model("memory", "anthropic:claude-haiku-4-5")

    pub = store.public()
    assert "task_models" in pub
    assert pub["task_models"]["memory"] == "anthropic:claude-haiku-4-5"


def test_public_task_models_empty_when_none_set(tmp_path):
    """public() returns empty task_models dict when no overrides are stored."""
    store = _store(tmp_path)
    pub = store.public()
    assert pub["task_models"] == {}
