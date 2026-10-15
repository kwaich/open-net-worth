"""The `claude_cli` transport's wiring through the config seam.

Pins the plumbing a provider needs to be a first-class citizen rather than a special
case: the catalog entry, provider-key inference (where ``base_url`` means the CLI PATH,
not an endpoint), ``get_provider`` dispatch on both the override and instance-default
paths, effort capabilities, and the batch seam's graceful degradation.
"""

from __future__ import annotations

import pytest

from backend.ai.assistant import get_provider
from backend.ai.batch.routing import resolve_adapter_key
from backend.ai.models.capabilities import effort_levels
from backend.ai.models.claude_cli_provider import ClaudeCliProvider
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
    assert "claude_cli" in VALID_PROVIDERS
    entries = [e for e in PROVIDER_CATALOG if e["transport"] == "claude_cli"]
    assert len(entries) == 1
    entry = entries[0]
    assert entry["key"] == "claude_cli"
    # There is no key to store — the CLI runs on the user's own login.
    assert entry["needs_api_key"] is False
    # base_url is the CLI PATH and must be editable (systemd/Docker rarely has it on PATH).
    assert entry["editable_base_url"] is True
    assert entry["base_url"] == ""


def test_a_custom_cli_path_still_infers_the_claude_cli_provider_key():
    """base_url is a PATH here, so exact base_url matching can't be the only route."""
    assert _infer_provider_key("claude_cli", "") == "claude_cli"
    assert _infer_provider_key("claude_cli", "/opt/homebrew/bin/claude") == "claude_cli"
    # …and it must not shadow the two pre-existing transports.
    assert _infer_provider_key("anthropic", "") == "anthropic"
    assert _infer_provider_key("openai_compatible", "https://unknown.example/v1") == "custom"


def test_resolve_for_provider_returns_the_transport_and_needs_no_key(store):
    creds = store.resolve_for_provider("claude_cli")
    assert creds is not None
    assert creds["transport"] == "claude_cli"
    assert creds["api_key"] == ""


def test_get_provider_builds_the_cli_provider_from_a_pinned_override(store, monkeypatch):
    monkeypatch.setattr("backend.ai.assistant.ModelConfigStore", type(store), raising=False)
    store.update_provider("claude_cli", {"base_url": "/custom/bin/claude"})
    resolved = store.resolve_override("claude_cli:opus")
    assert resolved["transport"] == "claude_cli"
    assert resolved["model"] == "opus"
    assert resolved["base_url"] == "/custom/bin/claude"

    provider = get_provider("claude_cli:opus")
    assert isinstance(provider, ClaudeCliProvider)
    # The stored base_url is threaded through as the CLI path, not an endpoint.
    assert provider.cli_path == "/custom/bin/claude"


def test_get_provider_builds_the_cli_provider_as_the_instance_default(store):
    store.update({"provider": "claude_cli", "model": "sonnet"})
    assert store.resolved()["provider"] == "claude_cli"
    provider = get_provider()
    assert isinstance(provider, ClaudeCliProvider)
    # Blank stored path → resolve `claude` on PATH.
    assert provider.cli_path == "claude"


def test_a_blank_cli_path_defaults_to_the_bare_binary_name():
    assert ClaudeCliProvider(cli_path="").cli_path == "claude"
    assert ClaudeCliProvider(cli_path="  ").cli_path == "claude"


def test_the_effort_ladder_is_the_full_cli_ladder_and_ignores_the_cli_path():
    """base_url is a PATH on this transport — the endpoint sniffing must not see it."""
    assert effort_levels("claude_cli", "", "sonnet") == ["low", "medium", "high", "xhigh", "max"]
    assert effort_levels("claude_cli", "/usr/local/bin/claude", "opus") == [
        "low",
        "medium",
        "high",
        "xhigh",
        "max",
    ]


def test_the_batch_seam_degrades_to_sync_for_the_cli_transport():
    """The CLI has no batch endpoint; guessing a wire format is the 2026-07-15 incident."""
    assert resolve_adapter_key("claude_cli", "") == "sync"


def test_the_catalog_entry_survives_a_lookup_by_key():
    entry = _provider_entry("claude_cli")
    assert entry is not None and entry["transport"] == "claude_cli"
