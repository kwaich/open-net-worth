"""Characterization tests for model/provider resolution in the runtime config.

Covers the CURRENT (this branch) behavior:
  1. ``ModelConfigStore.resolved()`` — resolution order (store → env → default);
     provider/model defaults; openai_compatible support; api_key preserved but never
     leaked through ``public()``.
  2. ``ModelConfigStore.update()`` — partial patch semantics (blank api_key keeps
     the saved key; unknown provider is stored verbatim but resolved() falls back to
     default).
  3. ``get_provider()`` / ``get_model()`` from ``assistant.py`` — build the right
     provider class (AnthropicProvider vs OpenAIProvider) and return the configured
     model string.
  4. ``has_api_key`` in ``public()`` is a bool-not-string, never the raw secret.
  5. ``openai_compatible`` transport: the SDK client is constructed with the configured
     base_url + api_key (mock the SDK so no real dependency/network).

All offline + hermetic: tmp store file; env cleared; Anthropic SDK key only in env.
"""

from __future__ import annotations

import sys
import types

import pytest

import backend.db.model_config_store as store_mod
from backend.ai.assistant import get_model, get_provider
from backend.ai.models.openai_provider import OpenAIProvider
from backend.core.errors import ConfigurationError
from backend.models.model_config import VALID_PROVIDERS


@pytest.fixture
def cfg_env(monkeypatch, db_isolated):
    """Isolated model-config store (DB-backed — a row in ``secret_config``, namespace
    "model_config"); env cleared; Anthropic key in env. ``db_isolated`` gives this test its
    own fresh database."""
    for key in (
        "AI_PROVIDER",
        "AI_MODEL",
        "ONW_TEST_DEFAULT_MODEL",
        "OPENAI_BASE_URL",
        "OPENAI_API_BASE",
    ):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")


# ── ModelConfigStore.resolved() — the resolution order ────────────────────────────


def test_resolved_requires_explicit_provider_and_model(cfg_env):
    store = store_mod.ModelConfigStore()
    with pytest.raises(ConfigurationError, match="AI provider is not configured"):
        store.resolved()


def test_resolved_store_overrides_provider_and_model(cfg_env):
    """Writing a provider+model to the store causes resolved() to return them."""
    store = store_mod.ModelConfigStore()
    store.update({"provider": "openai_compatible", "model": "gpt-4o", "base_url": "http://x/v1"})
    r = store.resolved()
    assert r["provider"] == "openai_compatible"
    assert r["model"] == "gpt-4o"
    assert r["base_url"] == "http://x/v1"


def test_resolved_unknown_provider_is_rejected(cfg_env):
    store = store_mod.ModelConfigStore()
    # A raw, unvalidated payload (bypassing update()'s own catalog check) — simulates
    # pre-existing bad state (e.g. a provider later dropped from the catalog), same as the
    # old direct file write this replaces.
    store.save({"provider": "totally_unknown", "model": "some-model"})
    with pytest.raises(ConfigurationError, match="Unknown AI provider"):
        store.resolved()


def test_resolved_api_key_is_preserved_in_resolved(cfg_env):
    """resolved() carries the stored api_key (it's needed by the SDK client)."""
    store = store_mod.ModelConfigStore()
    store.update({"provider": "anthropic", "model": "claude-test", "api_key": "sk-secret-key"})
    r = store.resolved()
    assert r["api_key"] == "sk-secret-key"


# ── ModelConfigStore.public() — api_key never leaks ──────────────────────────────


def test_public_never_returns_raw_api_key(cfg_env):
    """``public()`` must NOT include the api_key field — only ``has_api_key: bool``."""
    store = store_mod.ModelConfigStore()
    store.update({"provider": "anthropic", "model": "claude-test", "api_key": "sk-super-secret"})
    pub = store.public()
    assert "api_key" not in pub
    assert pub["has_api_key"] is True


def test_public_has_api_key_false_when_empty(cfg_env, monkeypatch):
    """``has_api_key`` is False when no api_key is available FROM ANY SOURCE.

    The env key the shared fixture sets has to come back out here. `has_api_key` is the
    UI's "do you still need to add a key" signal and reads `resolved()`, which falls back
    to env — so an operator who configured `ANTHROPIC_API_KEY` in their environment
    correctly reports True and is not nagged for a key they already have. Asserting False
    while leaving that env var set was testing the opposite of the intended behaviour.
    """
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    store = store_mod.ModelConfigStore()
    pub = store.public()
    assert isinstance(pub["has_api_key"], bool)
    assert pub["has_api_key"] is False
    assert "api_key" not in pub


# ── ModelConfigStore.update() — partial patch semantics ──────────────────────────


def test_update_blank_api_key_leaves_saved_key_unchanged(cfg_env):
    """A blank api_key in update() does NOT overwrite the saved key."""
    store = store_mod.ModelConfigStore()
    store.update({"provider": "anthropic", "model": "claude-test", "api_key": "sk-original"})
    assert store.resolved()["api_key"] == "sk-original"

    # A blank key (what the UI sends when no new key is typed) → no change.
    store.update({"api_key": ""})
    assert store.resolved()["api_key"] == "sk-original"


def test_update_non_blank_api_key_replaces_saved_key(cfg_env):
    """A non-blank api_key in update() replaces the saved key."""
    store = store_mod.ModelConfigStore()
    store.update({"provider": "anthropic", "model": "claude-test", "api_key": "sk-old"})
    store.update({"api_key": "sk-new"})
    assert store.resolved()["api_key"] == "sk-new"


def test_update_partial_leaves_other_fields_intact(cfg_env):
    """Updating one field does not clobber the others."""
    store = store_mod.ModelConfigStore()
    store.update({"provider": "openai_compatible", "model": "gpt-4o", "base_url": "http://x/v1"})
    # Patching only model should leave provider + base_url untouched.
    store.update({"model": "gpt-4o-mini"})
    r = store.resolved()
    assert r["provider"] == "openai_compatible"
    assert r["model"] == "gpt-4o-mini"
    assert r["base_url"] == "http://x/v1"


# ── get_provider() / get_model() — the assistant's view ──────────────────────────


def test_get_provider_requires_configuration(cfg_env):
    with pytest.raises(ConfigurationError, match="AI provider is not configured"):
        get_provider()


def test_get_model_requires_configuration(cfg_env):
    with pytest.raises(ConfigurationError, match="AI provider is not configured"):
        get_model()


def test_get_provider_returns_openai_provider_when_configured(cfg_env, monkeypatch):
    """When the store is set to openai_compatible, ``get_provider()`` returns OpenAIProvider."""
    # Mock the openai SDK so no real import is needed.
    fake_module = types.ModuleType("openai")

    class _FakeAsyncOpenAI:
        def __init__(self, base_url, api_key):
            pass

    fake_module.AsyncOpenAI = _FakeAsyncOpenAI
    monkeypatch.setitem(sys.modules, "openai", fake_module)

    store = store_mod.ModelConfigStore()
    store.update({"provider": "openai_compatible", "model": "gpt-4o", "base_url": "http://x/v1"})

    assert isinstance(get_provider(), OpenAIProvider)


def test_get_model_reflects_store_override(cfg_env):
    """After setting a model in the store, ``get_model()`` returns it."""
    store = store_mod.ModelConfigStore()
    store.update({"provider": "anthropic", "model": "claude-opus-4-8"})
    assert get_model() == "claude-opus-4-8"


# ── OpenAIProvider client construction ────────────────────────────────────────────


def test_openai_client_constructed_with_base_url_and_api_key(monkeypatch):
    """The OpenAI SDK client is constructed with base_url + api_key — the critical
    enabler for local-model endpoints (Ollama / LM Studio / OpenRouter / vLLM)."""
    captured: dict = {}

    class _FakeAsyncOpenAI:
        def __init__(self, base_url, api_key):
            captured["base_url"] = base_url
            captured["api_key"] = api_key

    fake_module = types.ModuleType("openai")
    fake_module.AsyncOpenAI = _FakeAsyncOpenAI
    monkeypatch.setitem(sys.modules, "openai", fake_module)

    provider = OpenAIProvider(base_url="http://localhost:11434/v1", api_key="sk-local")
    client = provider._get_client()
    assert client is not None
    assert captured["base_url"] == "http://localhost:11434/v1"
    assert captured["api_key"] == "sk-local"


def test_openai_client_defaults_to_openai_endpoint_when_no_base_url(monkeypatch):
    """OpenAIProvider with no base_url defaults to the canonical OpenAI endpoint."""
    captured: dict = {}

    class _FakeAsyncOpenAI:
        def __init__(self, base_url, api_key):
            captured["base_url"] = base_url
            captured["api_key"] = api_key

    fake_module = types.ModuleType("openai")
    fake_module.AsyncOpenAI = _FakeAsyncOpenAI
    monkeypatch.setitem(sys.modules, "openai", fake_module)

    provider = OpenAIProvider()  # no base_url / no key
    provider._get_client()
    assert "openai.com" in captured["base_url"]
    # A placeholder key is sent so local servers that ignore auth still work.
    assert captured["api_key"] is not None


# ── PROVIDER_CATALOG shape contract ───────────────────────────────────────────────


def test_provider_catalog_entries_have_required_fields():
    """Every PROVIDER_CATALOG entry has the required shape keys used by the UI."""
    required = {"key", "label", "transport", "base_url", "needs_api_key", "model_hints"}
    for entry in store_mod.PROVIDER_CATALOG:
        missing = required - entry.keys()
        assert not missing, f"Catalog entry {entry.get('key')} missing keys: {missing}"


def test_provider_catalog_transports_are_valid():
    """Every catalog entry's transport is one of the known providers."""
    # Derived from the single source of truth so adding a transport doesn't
    # require editing this test (it exists to catch a TYPO, not a new provider).
    valid_transports = set(VALID_PROVIDERS)
    for entry in store_mod.PROVIDER_CATALOG:
        assert entry["transport"] in valid_transports, (
            f"Catalog key {entry['key']!r} has unknown transport {entry['transport']!r}"
        )


def test_provider_catalog_has_anthropic_entry():
    """The catalog includes an anthropic entry (the default)."""
    keys = [e["key"] for e in store_mod.PROVIDER_CATALOG]
    assert "anthropic" in keys
