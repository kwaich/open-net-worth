"""get_provider()/get_model() resolve from the runtime store (→ env → default),
and the OpenAI-compatible provider builds its SDK client with the configured base_url.

Hermetic: tmp store file, cleared AI_* env, and the ``openai`` SDK is MOCKED (it's
an optional dependency that may not be installed) — we assert the client is
constructed with the configured base_url + api_key, which is what makes a local
Ollama/LM Studio endpoint reachable.
"""

from __future__ import annotations

import sys
import types

import pytest

import backend.db.model_config_store as store_mod
from backend.ai.assistant import get_model, get_provider
from backend.ai.models.anthropic_provider import AnthropicProvider
from backend.ai.models.openai_provider import OpenAIProvider


@pytest.fixture
def cfg_env(tmp_path, monkeypatch):
    # model_config is DB-backed now (Wave 5) — conftest's autouse Tier-2 table clear
    # already isolates it per test, no CONFIG_FILE monkeypatch needed.
    for key in (
        "AI_PROVIDER",
        "AI_MODEL",
        "ONW_TEST_DEFAULT_MODEL",
        "OPENAI_BASE_URL",
        "OPENAI_API_BASE",
    ):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    return monkeypatch


def test_explicit_anthropic_config(cfg_env):
    store_mod.ModelConfigStore().update({"provider": "anthropic", "model": "test-model"})
    assert isinstance(get_provider(), AnthropicProvider)
    assert get_model() == "test-model"


def test_store_overrides_to_openai_compatible(cfg_env):
    store_mod.ModelConfigStore().update(
        {"provider": "openai_compatible", "model": "gpt-4o", "base_url": "http://x/v1"}
    )
    provider = get_provider()
    assert isinstance(provider, OpenAIProvider)
    assert provider.base_url == "http://x/v1"
    assert get_model() == "gpt-4o"


def test_openai_client_built_with_base_url(monkeypatch):
    """The crux of the local-model story: the OpenAI SDK client is constructed with
    the configured base_url + api_key (mock the SDK so no real dependency/network)."""
    captured = {}

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


def test_openai_blank_base_url_defaults_to_openai(monkeypatch):
    captured = {}

    class _FakeAsyncOpenAI:
        def __init__(self, base_url, api_key):
            captured["base_url"] = base_url
            captured["api_key"] = api_key

    fake_module = types.ModuleType("openai")
    fake_module.AsyncOpenAI = _FakeAsyncOpenAI
    monkeypatch.setitem(sys.modules, "openai", fake_module)

    provider = OpenAIProvider()  # no base_url / no key
    provider._get_client()
    assert captured["base_url"] == "https://api.openai.com/v1"
    # A local server that ignores auth still works: a placeholder key is sent.
    assert captured["api_key"] == "not-needed"
