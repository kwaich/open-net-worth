"""Behavioral tests for the AI model-config tools (UI↔Lucius parity).

``get_model_config`` (read) + ``update_model_config`` (write) mirror Settings → AI Model
(``GET/PUT /api/model-config``). They are thin over ``ModelConfigStore`` with the same
two-transport validation the REST PUT uses. The api_key is write-only — never echoed.

model_config is DB-backed now (Wave 5, JSON->DB) — conftest's autouse Tier-2 table clear
isolates the store per test (never the live data dir).
"""

from __future__ import annotations

import asyncio
import json

import pytest

from backend.ai.tools import TOOL_SCHEMAS, execute_tool
from backend.db import model_config_store


def _call(tool, **inp):
    text, proposed = asyncio.run(execute_tool(tool, inp))
    return json.loads(text), proposed


@pytest.fixture()
def isolated_config(tmp_path, monkeypatch):
    """Seed ModelConfigStore's row directly (env-seed is empty under test)."""
    # Neutralize any AI_* env so resolved() falls to the built-in defaults, not the host's.
    for var in (
        "AI_PROVIDER",
        "AI_MODEL",
        "ONW_TEST_DEFAULT_MODEL",
        "OPENAI_BASE_URL",
        "OPENAI_API_BASE",
    ):
        monkeypatch.delenv(var, raising=False)
    model_config_store.ModelConfigStore().save({"provider": "anthropic", "model": "test-model"})
    return model_config_store.ModelConfigStore()


def test_model_config_tools_in_registered_set():
    names = {t["name"] for t in TOOL_SCHEMAS}
    assert "get_model_config" in names
    assert "update_model_config" in names


def test_get_model_config_returns_public_view_plus_catalog(isolated_config):
    out, proposed = _call("get_model_config")
    assert proposed is None
    # Explicit test route.
    assert out["provider"] == "anthropic"
    assert out["model"] == "test-model"
    assert out["has_api_key"] is False
    # Catalog is surfaced so Lucius can suggest valid providers/models.
    assert isinstance(out["catalog"], list) and out["catalog"]
    # The secret key is NEVER in the read.
    assert "api_key" not in out


def test_update_model_config_sets_provider_and_model(isolated_config):
    out, proposed = _call("update_model_config", provider="openai_compatible", model="llama3.1")
    assert proposed is None
    assert out["ok"] is True
    assert out["config"]["provider"] == "openai_compatible"
    assert out["config"]["model"] == "llama3.1"
    # Persisted: a fresh read sees it.
    read_out, _ = _call("get_model_config")
    assert read_out["model"] == "llama3.1"


def test_update_model_config_rejects_unknown_provider(isolated_config):
    out, proposed = _call("update_model_config", provider="not_a_provider")
    assert proposed is None
    assert out["ok"] is False
    assert "provider must be one of" in out["error"]
    # Nothing persisted — still the default.
    read_out, _ = _call("get_model_config")
    assert read_out["provider"] == "anthropic"


def test_update_model_config_api_key_is_write_only(isolated_config):
    out, _ = _call("update_model_config", api_key="sk-secret-123")
    assert out["ok"] is True
    # Echoed config reports has_api_key but never the key itself.
    assert out["config"]["has_api_key"] is True
    assert "api_key" not in out["config"]
    # The raw store holds it (write happened) but the read tool never leaks it.
    read_out, _ = _call("get_model_config")
    assert read_out["has_api_key"] is True
    assert "api_key" not in read_out
    # A subsequent update with a blank key leaves the saved one intact.
    out2, _ = _call("update_model_config", model="claude-opus-4-8", api_key="")
    assert out2["config"]["has_api_key"] is True
    assert out2["config"]["model"] == "claude-opus-4-8"


def test_update_model_config_empty_call_is_no_op(isolated_config):
    """No provider/model/base_url/api_key at all — nothing to change, so this must
    report no_op instead of a plain success indistinguishable from a real edit."""
    out, proposed = _call("update_model_config")
    assert proposed is None
    assert out["ok"] is True
    assert out["no_op"] is True
    assert "message" in out
    # Same echo shape a real update would carry — a caller ignoring no_op still works.
    assert out["config"]["provider"] == "anthropic"
    assert out["config"]["model"] == "test-model"
    # The explicit test route remains unchanged — now stored as ONE resolvable ref. The
    # trio the fixture wrote is normalised on first read (ADR 2026-08-06-one-default-model),
    # which is a migration, not this tool editing anything.
    assert isolated_config.get() == {"default_model": "anthropic:test-model"}


def test_update_model_config_empty_call_does_not_rewrite_existing_file(isolated_config):
    """An empty call against an ALREADY-configured instance must not touch the stored
    row — not even a byte-identical rewrite. model_config is DB-backed now (Wave 5,
    JSON->DB), so "on disk" becomes "the persisted row" — no mtime to compare, so this
    checks the stored value is bit-for-bit unchanged instead."""
    _call("update_model_config", provider="openai_compatible", model="llama3.1")
    before = isolated_config.get()

    out, _ = _call("update_model_config")
    assert out["ok"] is True
    assert out["no_op"] is True
    assert out["config"]["model"] == "llama3.1"

    assert isolated_config.get() == before


def test_set_provider_credentials_tool_in_registered_set():
    names = {t["name"] for t in TOOL_SCHEMAS}
    assert "set_provider_credentials" in names


def test_set_provider_credentials_stores_api_key(isolated_config):
    out, proposed = _call(
        "set_provider_credentials", provider_key="openai", api_key="sk-openai-123"
    )
    assert proposed is None
    assert out["ok"] is True
    assert out["provider_key"] == "openai"
    assert out["configured_providers"]["openai"]["has_api_key"] is True
    assert "api_key" not in out["configured_providers"]["openai"]


def test_set_provider_credentials_rejects_unknown_provider(isolated_config):
    out, _ = _call("set_provider_credentials", provider_key="not_a_provider")
    assert out["ok"] is False
    assert "unknown provider key" in out["error"]


def test_set_provider_credentials_empty_call_is_no_op(isolated_config):
    """A known provider_key with neither api_key nor base_url has nothing to change —
    must report no_op instead of a phantom "configured" success."""
    out, proposed = _call("set_provider_credentials", provider_key="openai")
    assert proposed is None
    assert out["ok"] is True
    assert out["no_op"] is True
    assert "message" in out
    # Same echo shape a real credential save would carry.
    assert out["provider_key"] == "openai"
    assert "openai" not in out["configured_providers"]
    # The explicit test route remains unchanged — now stored as ONE resolvable ref. The
    # trio the fixture wrote is normalised on first read (ADR 2026-08-06-one-default-model),
    # which is a migration, not this tool editing anything.
    assert isolated_config.get() == {"default_model": "anthropic:test-model"}


def test_set_provider_credentials_empty_call_does_not_rewrite_existing_entry(isolated_config):
    """An empty call against an ALREADY-configured provider must not touch the stored
    row — not even a byte-identical rewrite of its own entry. model_config is DB-backed
    now (Wave 5, JSON->DB) — no mtime to compare, so this checks the stored value is
    bit-for-bit unchanged instead."""
    _call("set_provider_credentials", provider_key="openai", api_key="sk-openai-123")
    before = isolated_config.get()

    out, _ = _call("set_provider_credentials", provider_key="openai")
    assert out["ok"] is True
    assert out["no_op"] is True
    assert out["configured_providers"]["openai"]["has_api_key"] is True

    assert isolated_config.get() == before
