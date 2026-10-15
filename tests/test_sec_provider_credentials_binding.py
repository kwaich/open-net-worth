"""M3 — a stored provider key is bound to its endpoint; AI tools cannot move either."""

from __future__ import annotations

import asyncio
import json

import pytest

from backend.ai.tools import execute_tool
from backend.db.model_config_store import ModelConfigStore


def _call(name, **kw):
    text, _ = asyncio.run(execute_tool(name, kw))
    return json.loads(text)


@pytest.fixture
def store(db_isolated):
    return ModelConfigStore()


def test_changing_base_url_without_a_key_clears_the_stored_key(store):
    store.update_provider("custom", {"base_url": "https://good.example/v1", "api_key": "sk-good"})
    assert store.read_stored_api_key("custom") == "sk-good"
    store.update_provider("custom", {"base_url": "https://evil.example/v1"})
    assert not store.read_stored_api_key("custom")


def test_changing_base_url_with_a_new_key_keeps_the_new_key(store):
    store.update_provider("custom", {"base_url": "https://good.example/v1", "api_key": "sk-good"})
    store.update_provider("custom", {"base_url": "https://other.example/v1", "api_key": "sk-new"})
    assert store.read_stored_api_key("custom") == "sk-new"


def test_resaving_the_same_base_url_keeps_the_key(store):
    store.update_provider("custom", {"base_url": "https://good.example/v1", "api_key": "sk-good"})
    store.update_provider("custom", {"base_url": "https://good.example/v1"})
    assert store.read_stored_api_key("custom") == "sk-good"


def test_default_provider_update_rebinds_too(store):
    store.update_provider("custom", {"base_url": "https://good.example/v1", "api_key": "sk-good"})
    store.update(
        {"provider": "openai_compatible", "base_url": "https://evil.example/v1", "model": "m"}
    )
    assert not store.read_stored_api_key("custom")


def test_non_editable_preset_ignores_a_stored_base_url(store):
    store.update_provider("openai", {"api_key": "sk-o", "model": "gpt-4o"})
    store._write_raw(
        {
            **store._read_raw(),
            "providers": {
                "openai": {
                    "api_key": "sk-o",
                    "base_url": "https://evil.example/v1",
                    "model": "gpt-4o",
                }
            },
        }
    )
    store.update({"default_model": "openai:gpt-4o"})
    assert store.resolved()["base_url"] == "https://api.openai.com/v1"
    assert store.resolve_for_provider("openai")["base_url"] == "https://api.openai.com/v1"


def test_editable_preset_honours_its_stored_base_url(store):
    store.update_provider("ollama", {"base_url": "http://lan-box:11434/v1", "model": "llama3.1"})
    assert store.resolve_for_provider("ollama")["base_url"] == "http://lan-box:11434/v1"


@pytest.mark.parametrize(
    "tool,args",
    [
        (
            "set_provider_credentials",
            {"provider_key": "custom", "base_url": "https://evil.example/v1"},
        ),
        ("set_provider_credentials", {"provider_key": "claude_cli", "base_url": "/tmp/evil"}),
        (
            "update_model_config",
            {"provider": "openai_compatible", "model": "m", "base_url": "https://evil.example/v1"},
        ),
    ],
)
def test_ai_tools_refuse_base_url(store, tool, args):
    out = _call(tool, **args)
    assert out["ok"] is False
    assert store.providers_public() == {}
