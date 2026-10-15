"""Regression tests: AI provider keys resolve from the Settings store, not just env.

Live incident: removing ANTHROPIC_API_KEY from .env broke chat with the SDK's
"Could not resolve authentication method" — get_provider()'s instance-default
path constructed AnthropicProvider() keyless and relied on the env fallback.
"""

from backend.db.provider_keys import effective_api_key


def test_store_key_wins_over_env(monkeypatch):
    from backend.db.model_config_store import ModelConfigStore

    monkeypatch.setattr(ModelConfigStore, "read_stored_api_key", lambda self, k: "sk-store")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-env")
    assert effective_api_key("anthropic") == "sk-store"


def test_env_fallback_when_store_empty(monkeypatch):
    from backend.db.model_config_store import ModelConfigStore

    monkeypatch.setattr(ModelConfigStore, "read_stored_api_key", lambda self, k: None)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-env")
    assert effective_api_key("anthropic") == "sk-env"


def test_none_when_neither(monkeypatch):
    from backend.db.model_config_store import ModelConfigStore

    monkeypatch.setattr(ModelConfigStore, "read_stored_api_key", lambda self, k: None)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert effective_api_key("anthropic") is None


def test_get_provider_default_path_carries_store_key(monkeypatch):
    """The exact incident: no env key, store key set → chat provider must be keyed."""
    from backend.db.model_config_store import ModelConfigStore

    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(
        ModelConfigStore,
        "resolved",
        lambda self: {
            "provider": "anthropic",
            "model": "claude-sonnet-4-6",
            "base_url": "",
            "api_key": "sk-store-key",
        },
    )
    from backend.ai.assistant import get_provider

    provider = get_provider(None)
    assert provider.client.api_key == "sk-store-key"


def test_provider_ctor_falls_back_to_store(monkeypatch):
    from backend.db.model_config_store import ModelConfigStore

    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(ModelConfigStore, "read_stored_api_key", lambda self, k: "sk-store-2")
    from backend.ai.models.anthropic_provider import AnthropicProvider

    assert AnthropicProvider().client.api_key == "sk-store-2"
    assert AnthropicProvider(api_key="sk-explicit").client.api_key == "sk-explicit"
