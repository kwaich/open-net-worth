"""Behavioral tests for the three Lucius parity tools added to close audit gaps.

  * ``set_connector_enabled`` (#3)  — enable/disable a connector or capability.
  * ``run_automation_now`` — run an Automation immediately (was ``run_scheduled_task_now``
    until Scheduled Tasks were retired, 2026-08-02).
  * ``set_provider_credentials`` (#4) — store secondary-provider API credentials.

Tests drive ``execute_tool`` (asyncio.run). All stores are isolated via tmp_path or
monkeypatching; no LLM and no network are touched, so no 'integration' marker.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from backend.ai.tools import TOOL_SCHEMAS, execute_tool


def call(tool, **inp):
    text, proposed = asyncio.run(execute_tool(tool, inp))
    return json.loads(text), proposed


# ── set_connector_enabled: registry + headless checks ────────────────────────


def test_set_connector_enabled_in_registered_set():
    names = {t["name"] for t in TOOL_SCHEMAS}
    assert "set_connector_enabled" in names


def test_set_connector_enabled_returns_none_proposed_change(tmp_path, monkeypatch):
    """The tool is not approve-tier (no confirm card) — proposed_change is always None."""
    # Return a stub view so the service call doesn't hit the real data dir.
    from backend.models.connector import ConnectorView
    from backend.services import connectors as conn_svc

    stub_view = ConnectorView(
        id="telegram",
        kind="telegram",
        name="Telegram",
        enabled=True,
        secrets_public={},
        status="active",
    )
    monkeypatch.setattr(conn_svc, "set_connector_enabled", lambda cid, en: stub_view)

    _, proposed = call("set_connector_enabled", connector_id="telegram", enabled=True)
    assert proposed is None


def test_set_connector_enabled_success(tmp_path, monkeypatch):
    from backend.models.connector import ConnectorView
    from backend.services import connectors as conn_svc

    toggled: list[tuple] = []

    def _fake_set(connector_id: str, enabled: bool):
        toggled.append((connector_id, enabled))
        return ConnectorView(
            id=connector_id,
            kind="telegram",
            name="Telegram",
            enabled=enabled,
            secrets_public={},
            status="active" if enabled else "configured",
        )

    monkeypatch.setattr(conn_svc, "set_connector_enabled", _fake_set)

    res, proposed = call("set_connector_enabled", connector_id="telegram", enabled=False)
    assert proposed is None
    assert res["ok"] is True
    assert res["connector"]["enabled"] is False
    assert toggled == [("telegram", False)]


def test_set_connector_enabled_missing_id():
    res, proposed = call("set_connector_enabled", connector_id="", enabled=True)
    assert proposed is None
    assert res["ok"] is False
    assert "connector_id" in res["error"]


def test_set_connector_enabled_unknown_connector_error(monkeypatch):
    """NotFoundError from the service is surfaced as {ok: false, error: ...}."""
    from backend.core.errors import NotFoundError
    from backend.services import connectors as conn_svc

    monkeypatch.setattr(
        conn_svc,
        "set_connector_enabled",
        lambda cid, en: (_ for _ in ()).throw(NotFoundError(f"unknown connector: {cid}")),
    )
    res, proposed = call("set_connector_enabled", connector_id="ghost", enabled=True)
    assert proposed is None
    assert res["ok"] is False
    assert "unknown connector" in res["error"]


# ── run_scheduled_task_now ────────────────────────────────────────────────────
#
# Retired with Scheduled Tasks (2026-08-02): its successor is `run_automation_now`, covered
# with the rest of the Automation surface. Two tests lived here — registered, and a missing
# task_id reporting the field by name — and both are properties of the tool that replaced
# it, not of a name that no longer resolves.

# ── set_provider_credentials: registry + headless checks ─────────────────────


def test_set_provider_credentials_in_registered_set():
    names = {t["name"] for t in TOOL_SCHEMAS}
    assert "set_provider_credentials" in names


@pytest.fixture()
def isolated_model_config(monkeypatch):
    # ModelConfigStore is DB-backed (secret_config table) — no CONFIG_FILE left to patch.
    # tests/conftest.py's autouse per-test table clear already isolates the row.
    for var in (
        "AI_PROVIDER",
        "AI_MODEL",
        "ONW_TEST_DEFAULT_MODEL",
        "OPENAI_BASE_URL",
        "OPENAI_API_BASE",
    ):
        monkeypatch.delenv(var, raising=False)


def test_set_provider_credentials_stores_key_write_only(isolated_model_config):
    res, proposed = call("set_provider_credentials", provider_key="openai", api_key="sk-test-999")
    assert proposed is None
    assert res["ok"] is True
    assert res["provider_key"] == "openai"
    # The raw secret value is NEVER in the response — only has_api_key (a boolean).
    assert "sk-test-999" not in json.dumps(res)
    # has_api_key reflects that a key is stored (write-only).
    assert res["configured_providers"]["openai"]["has_api_key"] is True


def test_set_provider_credentials_blank_key_keeps_saved(isolated_model_config):
    # Store a real key first.
    call("set_provider_credentials", provider_key="openai", api_key="sk-original")
    # Update with a blank key — should NOT clear the saved key.
    res, _ = call("set_provider_credentials", provider_key="openai", api_key="")
    assert res["configured_providers"]["openai"]["has_api_key"] is True


def test_set_provider_credentials_refuses_base_url(isolated_model_config):
    res, _ = call(
        "set_provider_credentials",
        provider_key="ollama",
        base_url="http://myhost:11434/v1",
    )
    assert res["ok"] is False
    assert "Settings" in res["error"]


def test_set_provider_credentials_unknown_provider_key(isolated_model_config):
    res, proposed = call("set_provider_credentials", provider_key="not_a_real_provider")
    assert proposed is None
    assert res["ok"] is False
    assert "unknown provider key" in res["error"]


def test_set_provider_credentials_missing_provider_key(isolated_model_config):
    res, proposed = call("set_provider_credentials", provider_key="")
    assert proposed is None
    assert res["ok"] is False
    assert "provider_key" in res["error"]


def test_set_provider_credentials_does_not_change_instance_default(isolated_model_config):
    """Storing per-provider creds must NOT change the instance's active provider/model."""
    from backend.db.model_config_store import ModelConfigStore

    # Record the instance defaults before.
    before = ModelConfigStore().public()

    call("set_provider_credentials", provider_key="groq", api_key="gsk-xyz")

    after = ModelConfigStore().public()
    assert after["provider"] == before["provider"]
    assert after["model"] == before["model"]
