"""The `codex_cli` transport's wiring through the config seam.

Pins the plumbing a provider needs to be a first-class citizen rather than a special
case: the catalog entry, provider-key inference (where ``base_url`` means the CLI PATH,
not an endpoint), ``get_provider`` dispatch on both the override and instance-default
paths, effort capabilities, and the batch seam's graceful degradation. The twin of
``test_claude_cli_config_wiring.py`` — the two CLI transports must not shadow each other.
"""

from __future__ import annotations

import pytest

from backend.ai.assistant import get_provider
from backend.ai.batch.routing import resolve_adapter_key
from backend.ai.models.capabilities import effort_levels
from backend.ai.models.claude_cli_provider import ClaudeCliProvider
from backend.ai.models.codex_cli_provider import CodexCliProvider
from backend.db.model_config_store import (
    PROVIDER_CATALOG,
    ModelConfigStore,
    _infer_provider_key,
    _provider_entry,
)
from backend.models.model_config import VALID_PROVIDERS


@pytest.fixture()
def store():
    return ModelConfigStore()


def test_the_transport_is_a_valid_provider_kind_with_exactly_one_catalog_entry():
    assert "codex_cli" in VALID_PROVIDERS
    entries = [e for e in PROVIDER_CATALOG if e["transport"] == "codex_cli"]
    assert len(entries) == 1
    entry = entries[0]
    assert entry["key"] == "codex_cli"
    # There is no key to store — the CLI runs on the user's own `codex login`.
    assert entry["needs_api_key"] is False
    # base_url is the CLI PATH and must be editable (systemd/Docker rarely has it on PATH).
    assert entry["editable_base_url"] is True
    assert entry["base_url"] == ""


def test_a_custom_cli_path_still_infers_the_codex_cli_provider_key():
    """base_url is a PATH here, so exact base_url matching can't be the only route."""
    assert _infer_provider_key("codex_cli", "") == "codex_cli"
    assert _infer_provider_key("codex_cli", "/opt/homebrew/bin/codex") == "codex_cli"
    # …and the two CLI transports must not shadow each other or the HTTP ones.
    assert _infer_provider_key("claude_cli", "/opt/homebrew/bin/claude") == "claude_cli"
    assert _infer_provider_key("anthropic", "") == "anthropic"
    assert _infer_provider_key("openai_compatible", "https://unknown.example/v1") == "custom"


def test_resolve_for_provider_returns_the_transport_and_needs_no_key(store):
    creds = store.resolve_for_provider("codex_cli")
    assert creds is not None
    assert creds["transport"] == "codex_cli"
    assert creds["api_key"] == ""


def test_get_provider_builds_the_cli_provider_from_a_pinned_override(store, monkeypatch):
    monkeypatch.setattr("backend.ai.assistant.ModelConfigStore", type(store), raising=False)
    store.update_provider("codex_cli", {"base_url": "/custom/bin/codex"})
    resolved = store.resolve_override("codex_cli:gpt-5.1-codex")
    assert resolved["transport"] == "codex_cli"
    assert resolved["model"] == "gpt-5.1-codex"
    assert resolved["base_url"] == "/custom/bin/codex"

    provider = get_provider("codex_cli:gpt-5.1-codex")
    assert isinstance(provider, CodexCliProvider)
    # The stored base_url is threaded through as the CLI path, not an endpoint.
    assert provider.cli_path == "/custom/bin/codex"


def test_get_provider_builds_the_cli_provider_as_the_instance_default(store):
    store.update({"provider": "codex_cli", "model": "gpt-5.1-codex"})
    assert store.resolved()["provider"] == "codex_cli"
    provider = get_provider()
    assert isinstance(provider, CodexCliProvider)
    assert not isinstance(provider, ClaudeCliProvider)
    # Blank stored path → resolve `codex` on PATH.
    assert provider.cli_path == "codex"


def test_blank_instance_model_uses_the_codex_cli_default(store):
    store.update({"provider": "codex_cli", "model": ""})

    assert store.resolved()["model"] == ""


def test_a_provider_pinned_blank_model_override_stays_on_codex_cli(store):
    """``codex_cli:`` means Codex with its own default, never a model on another provider."""
    store.save({"default_model": "claude_cli:sonnet"})

    resolved = store.resolve_override("codex_cli:")

    assert resolved is not None
    assert resolved["provider_key"] == "codex_cli"
    assert resolved["transport"] == "codex_cli"
    assert resolved["model"] == ""


def test_a_blank_cli_path_defaults_to_the_bare_binary_name():
    assert CodexCliProvider(cli_path="").cli_path == "codex"
    assert CodexCliProvider(cli_path="  ").cli_path == "codex"


def test_the_effort_ladder_is_codex_own_three_rungs_and_ignores_the_cli_path():
    """Codex has no xhigh/max rung — advertising one would lie. base_url is a PATH here."""
    assert effort_levels("codex_cli", "", "gpt-5.1-codex") == ["low", "medium", "high"]
    assert effort_levels("codex_cli", "/usr/local/bin/codex", "gpt-5.1") == [
        "low",
        "medium",
        "high",
    ]


def test_the_batch_seam_degrades_to_sync_for_the_cli_transport():
    """The CLI has no batch endpoint; guessing a wire format is the 2026-07-15 incident."""
    assert resolve_adapter_key("codex_cli", "") == "sync"


def test_the_catalog_entry_survives_a_lookup_by_key():
    entry = _provider_entry("codex_cli")
    assert entry is not None and entry["transport"] == "codex_cli"
