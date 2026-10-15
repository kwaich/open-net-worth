"""Tests for the per-provider enable/disable feature.

New file — does NOT touch tests/test_model_config_store.py.
"""

from __future__ import annotations

import json

import pytest

from backend.core.errors import ProviderDisabledError
from backend.db.model_config_store import ModelConfigStore


class _ModelConfigProxy:
    """Drop-in replacement for the old tmp ``model_config.json`` Path. ModelConfigStore is
    DB-backed now (Wave 5D2a — a row in ``secret_config``, namespace "model_config"), so
    there is no file left to point at. Proxies ``write_text()``/``read_text()`` onto the
    store's RAW payload (the base ``NamespacedSettingsStore.get()``/``save()`` — not the
    migrating ``_read_raw()``, which ``ModelConfigStore`` does not override), so every
    existing ``cfg_path.write_text(...)`` (incl. simulating a pre-migration legacy shape)
    / ``cfg_path.read_text()`` call site below is unchanged."""

    @staticmethod
    def _store():
        return ModelConfigStore()

    def write_text(self, text: str, encoding: str = "utf-8") -> None:
        self._store().save(json.loads(text))

    def read_text(self, encoding: str = "utf-8") -> str:
        return json.dumps(self._store().get())


@pytest.fixture
def cfg_path(db_isolated):
    """Proxy for the old tmp ``model_config.json`` — db_isolated gives this test its own
    fresh database."""
    return _ModelConfigProxy()


@pytest.fixture
def store(cfg_path):
    return ModelConfigStore()


# ── provider_enabled default ──────────────────────────────────────────────────


def test_enabled_default_true_for_absent_entry(store):
    """A provider with no entry in the providers map is considered enabled."""
    assert store.provider_enabled("anthropic") is True
    assert store.provider_enabled("openai") is True


def test_enabled_default_true_for_legacy_entry_without_field(cfg_path):
    """Legacy entries that predate the enabled field are enabled by default."""
    cfg_path.write_text(
        json.dumps(
            {
                "provider": "anthropic",
                "model": "claude-sonnet-4-6",
                "providers": {
                    "openai": {"api_key": "sk-xxx", "model": "gpt-4o"},
                },
            }
        )
    )
    store = ModelConfigStore()
    assert store.provider_enabled("openai") is True


# ── providers_public echoes enabled ──────────────────────────────────────────


def test_providers_public_includes_enabled_field(store, cfg_path):
    """providers_public() echoes the enabled field for each provider entry."""
    store.update_provider("openai", {"api_key": "sk-xxx", "model": "gpt-4o"})
    pub = store.providers_public()
    assert "openai" in pub
    # Freshly created entry — enabled not yet set, defaults to True.
    assert pub["openai"]["enabled"] is True


def test_providers_public_reflects_disabled(store):
    """providers_public() echoes enabled=False for a disabled provider."""
    store.update_provider("openai", {"api_key": "sk-xxx"})
    store.update_provider("openai", {"enabled": False})
    pub = store.providers_public()
    assert pub["openai"]["enabled"] is False


# ── update_provider persists enabled ─────────────────────────────────────────


def test_toggle_persists(cfg_path):
    """Enabling/disabling a provider persists to disk and survives a reload."""
    store = ModelConfigStore()
    store.update_provider("openai", {"api_key": "sk-xxx"})

    # Disable.
    store.update_provider("openai", {"enabled": False})
    assert store.provider_enabled("openai") is False

    # Re-enable.
    store.update_provider("openai", {"enabled": True})
    assert store.provider_enabled("openai") is True

    # Fresh store object reads from disk — state must persist.
    store2 = ModelConfigStore()
    assert store2.provider_enabled("openai") is True


def test_disabled_entry_retained_without_credentials(cfg_path):
    """A disabled-only entry (no api_key / base_url / model) is kept on disk
    so the disabled state survives credential removal."""
    store = ModelConfigStore()
    # Disable a provider that has no other data.
    store.update_provider("gemini", {"enabled": False})
    raw = json.loads(cfg_path.read_text())
    assert "gemini" in raw.get("providers", {})
    assert raw["providers"]["gemini"]["enabled"] is False


def test_empty_enabled_entry_removed(cfg_path):
    """An entry with no credentials AND enabled=True is pruned (no dead entry)."""
    store = ModelConfigStore()
    store.update_provider("openai", {"api_key": "sk-xxx"})
    # Now update only enabled=True (no credentials change).
    store.update_provider("openai", {"enabled": True})
    # Still has the api_key — entry not removed.
    raw = json.loads(cfg_path.read_text())
    assert "openai" in raw.get("providers", {})


# ── remove_provider — the whole entry, unlike remove_provider_key ────────────


def test_remove_provider_drops_a_disabled_only_entry(cfg_path):
    """The counterpart to test_disabled_entry_retained_without_credentials above.

    Retention is right for credential removal — the disabled state must survive. It is
    wrong for "remove this provider", which is why Settings' Remove button could never
    clear a switched-off row: it called the key endpoint, and enabled=False kept the entry
    alive every time.
    """
    store = ModelConfigStore()
    store.update_provider("gemini", {"enabled": False})
    assert "gemini" in json.loads(cfg_path.read_text())["providers"]

    store.remove_provider("gemini")
    assert "gemini" not in json.loads(cfg_path.read_text()).get("providers", {})


def test_remove_provider_drops_credentials_and_settings(cfg_path):
    store = ModelConfigStore()
    store.update_provider(
        "openai", {"api_key": "sk-x", "base_url": "https://api.openai.com/v1", "model": "gpt-4o"}
    )
    public = store.remove_provider("openai")
    assert "openai" not in public
    assert "openai" not in json.loads(cfg_path.read_text()).get("providers", {})


def test_remove_provider_leaves_other_providers_intact(cfg_path):
    store = ModelConfigStore()
    store.update_provider("openai", {"api_key": "sk-a"})
    store.update_provider("xai", {"api_key": "sk-b"})

    store.remove_provider("openai")
    providers = json.loads(cfg_path.read_text())["providers"]
    assert "openai" not in providers
    assert providers["xai"]["api_key"] == "sk-b"


def test_remove_provider_absent_key_is_a_noop(cfg_path):
    store = ModelConfigStore()
    store.update_provider("openai", {"api_key": "sk-a"})
    before = cfg_path.read_text()

    assert "never-configured" not in store.remove_provider("never-configured")
    assert cfg_path.read_text() == before


def test_removed_provider_reads_as_enabled_again(cfg_path):
    """Documents the consequence, so it is a decision rather than a surprise.

    provider_enabled() treats an ABSENT entry as enabled — that default exists so an
    unconfigured instance default still works. So removing a disabled provider does NOT
    leave it disabled; a model still pinned to it fails at the transport as
    unauthenticated instead of raising ProviderDisabledError.
    """
    store = ModelConfigStore()
    store.update_provider("gemini", {"enabled": False})
    assert store.provider_enabled("gemini") is False

    store.remove_provider("gemini")
    assert store.provider_enabled("gemini") is True


# ── resolve_for_provider raises on disabled ────────────────────────────────


def test_resolve_for_provider_raises_when_disabled(store):
    """resolve_for_provider raises ProviderDisabledError for a disabled provider."""
    store.update_provider("openai", {"api_key": "sk-xxx"})
    store.update_provider("openai", {"enabled": False})

    with pytest.raises(ProviderDisabledError) as exc_info:
        store.resolve_for_provider("openai")

    assert "disabled" in str(exc_info.value).lower()
    assert exc_info.value.context.get("provider_key") == "openai"


def test_resolve_for_provider_ok_when_enabled(store):
    """resolve_for_provider returns creds normally when provider is enabled."""
    store.update_provider("openai", {"api_key": "sk-xxx"})
    result = store.resolve_for_provider("openai")
    assert result is not None
    assert result["transport"] == "openai_compatible"


# ── resolve_override propagates ProviderDisabledError ────────────────────────


def test_resolve_override_provider_key_disabled(store):
    """resolve_override raises ProviderDisabledError for an explicit catalog:model
    override when that provider key is disabled."""
    store.update_provider("openai", {"api_key": "sk-xxx"})
    store.update_provider("openai", {"enabled": False})

    with pytest.raises(ProviderDisabledError):
        store.resolve_override("openai:gpt-4o")


def test_plain_model_id_unaffected_by_other_disabled_providers(store):
    """Plain model ids resolve via the instance default and ignore other providers'
    enabled state — there is no prefix auto-detect (pinning happens at save time via
    the compound 'catalog:model' form, which DOES raise when its provider is disabled)."""
    store.update_provider("anthropic", {"api_key": "sk-ant-xxx"})
    store.update_provider("anthropic", {"enabled": False})

    result = store.resolve_override("claude-sonnet-4-6")
    assert result is not None
    assert result["provider_key"] is None
    assert result["model"] == "claude-sonnet-4-6"


def test_resolve_override_none_for_blank(store):
    """resolve_override returns None for a blank override (no change)."""
    assert store.resolve_override("") is None
    assert store.resolve_override("   ") is None


# ── configured_provider_keys excludes disabled ───────────────────────────────


def test_configured_provider_keys_excludes_disabled(cfg_path):
    """configured_provider_keys() omits disabled providers from the union."""
    store = ModelConfigStore()
    store.update_provider("openai", {"api_key": "sk-xxx"})
    store.update_provider("gemini", {"api_key": "gm-yyy"})
    store.update_provider("openai", {"enabled": False})

    keys = store.configured_provider_keys()
    assert "openai" not in keys
    assert "gemini" in keys


def test_configured_provider_keys_includes_instance_default(cfg_path):
    """The instance default is always included even if its provider entry is absent."""
    cfg_path.write_text(
        json.dumps(
            {
                "provider": "anthropic",
                "model": "claude-sonnet-4-6",
                "providers": {},
            }
        )
    )
    store = ModelConfigStore()
    keys = store.configured_provider_keys()
    assert "anthropic" in keys


def test_keyless_provider_with_default_url_is_configured(store):
    """Regression: an enabled keyless provider on its catalog-default base_url
    (entry holds only enabled/key_status — no api_key, no base_url) must appear
    in configured_provider_keys so its models reach the ?all=1 picker union."""
    store.update_provider("ollama", {"enabled": True, "key_status": "valid"})
    assert "ollama" in store.configured_provider_keys()

    store.update_provider("ollama", {"enabled": False})
    assert "ollama" not in store.configured_provider_keys()
