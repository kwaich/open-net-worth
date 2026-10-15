"""Local-CLI providers enrol by DETECTION, not by stored credentials.

``claude_cli`` / ``codex_cli`` have no api_key and no base_url to save (their base_url is
repurposed as an optional binary PATH), so the store's usual "configured = something was
stored" rule excluded them from ``configured_provider_keys()`` forever — which meant
``GET /api/models?all=1`` never carried them and no group for either CLI ever appeared in
any model picker, even with both CLIs installed and their toggles on.

The rule for these transports is: **installed + not toggled off = configured**
(ADR 2026-07-31-local-cli-providers-enrol-by-detection).

Hermetic: every test drives a tmp ``model_config.json`` and stubs binary detection, so it
never depends on whether the dev machine happens to have `claude`/`codex` on PATH.
"""

from __future__ import annotations

import pytest

from backend.db.model_config_store import (
    CLI_PROVIDER_KEYS,
    ModelConfigStore,
    cli_is_installed,
)


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


def _store(tmp_path):
    """``tmp_path`` is kept as a parameter purely for call-site compatibility — ModelConfigStore
    is DB-backed now (a row in ``secret_config``, namespace "model_config"), so there is no
    longer a file at that path to seed. ``_read_raw`` already handles the absent-row case
    (env seed, or ``{}`` when ``_clear_ai_env`` has cleared it — this file's autouse fixture),
    so an unconditional bare construction reproduces the old "the file exists" baseline."""
    return ModelConfigStore()


def _install(monkeypatch, *keys: str, record: list | None = None):
    """Stub detection so exactly ``keys`` count as installed."""
    installed = set(keys)

    # Stub the PATH RESOLVER, not cli_is_installed — the latter now delegates to it (the row
    # shows WHERE a CLI resolves, not just whether it does), so stubbing the bool alone was
    # silently bypassed.
    def fake(provider_key: str, cli_path: str = "") -> str:
        if record is not None:
            record.append((provider_key, cli_path))
        return f"/usr/local/bin/{provider_key}" if provider_key in installed else ""

    monkeypatch.setattr("backend.db.model_config_store.cli_binary_path", fake)


# ── cli_is_installed: the PATH lookup itself ──────────────────────────────────


def test_cli_is_installed_resolves_the_catalog_binary(monkeypatch):
    """A blank path means "resolve the catalog's binary name on PATH"."""
    seen: list[str] = []

    class _Shutil:
        @staticmethod
        def which(cmd):
            seen.append(cmd)
            return "/usr/local/bin/claude" if cmd == "claude" else None

    monkeypatch.setattr("backend.db.model_config_store.shutil", _Shutil)
    assert cli_is_installed("claude_cli") is True
    assert cli_is_installed("codex_cli") is False
    assert cli_is_installed("antigravity_cli") is False
    assert seen == ["claude", "codex", "agy"]


def test_cli_is_installed_honours_a_stored_path(monkeypatch):
    """A stored path wins over the catalog binary name — that is what the field is for."""

    class _Shutil:
        @staticmethod
        def which(cmd):
            return "/opt/claude" if cmd == "/opt/claude" else None

    monkeypatch.setattr("backend.db.model_config_store.shutil", _Shutil)
    assert cli_is_installed("claude_cli", "/opt/claude") is True
    assert cli_is_installed("claude_cli", "") is False


def test_cli_is_installed_false_for_a_non_cli_provider(monkeypatch):
    """Only entries carrying ``cli_binary`` are CLI transports — nothing else can be
    'installed', however the caller asks."""

    class _Shutil:
        @staticmethod
        def which(cmd):  # would resolve anything, if it were ever consulted
            return "/usr/bin/anything"

    monkeypatch.setattr("backend.db.model_config_store.shutil", _Shutil)
    assert cli_is_installed("openrouter") is False
    assert cli_is_installed("nope") is False


def test_catalog_marks_exactly_the_three_cli_transports():
    assert CLI_PROVIDER_KEYS == ("claude_cli", "codex_cli", "antigravity_cli")


# ── configured_provider_keys: the presence gate ───────────────────────────────


def test_installed_cli_is_configured_with_no_stored_entry(tmp_path, monkeypatch):
    """The whole point: nothing saved, nothing clicked — installed is enough."""
    _install(monkeypatch, "claude_cli", "codex_cli")
    store = _store(tmp_path)
    store.update({"provider": "anthropic", "model": "claude-sonnet-4-6", "api_key": "sk-ant"})

    assert store.get().get("providers", {}).keys() == {"anthropic"}
    keys = store.configured_provider_keys()
    assert "claude_cli" in keys
    assert "codex_cli" in keys


def test_uninstalled_cli_is_not_configured(tmp_path, monkeypatch):
    _install(monkeypatch, "codex_cli")
    store = _store(tmp_path)
    store.update({"provider": "anthropic", "model": "claude-sonnet-4-6", "api_key": "sk-ant"})

    keys = store.configured_provider_keys()
    assert "claude_cli" not in keys
    assert "codex_cli" in keys


def test_disabled_cli_is_not_configured_even_when_installed(tmp_path, monkeypatch):
    """The enable toggle is the owner's off switch — detection must never override it."""
    _install(monkeypatch, "claude_cli", "codex_cli")
    store = _store(tmp_path)
    store.update({"provider": "anthropic", "model": "claude-sonnet-4-6", "api_key": "sk-ant"})
    store.update_provider("claude_cli", {"enabled": False})

    keys = store.configured_provider_keys()
    assert "claude_cli" not in keys
    assert "codex_cli" in keys


def test_re_enabling_a_cli_restores_it(tmp_path, monkeypatch):
    """``enabled: True`` alone stores no credential, so only the detection branch can
    bring the row back — a regression here would strand a re-enabled CLI off the picker."""
    _install(monkeypatch, "claude_cli")
    store = _store(tmp_path)
    store.update({"provider": "anthropic", "model": "claude-sonnet-4-6", "api_key": "sk-ant"})
    store.update_provider("claude_cli", {"enabled": False})
    assert "claude_cli" not in store.configured_provider_keys()

    store.update_provider("claude_cli", {"enabled": True})
    assert "claude_cli" in store.configured_provider_keys()


def test_detection_consults_the_stored_binary_path(tmp_path, monkeypatch):
    """A custom CLI path must be what gets probed, not the catalog default."""
    calls: list = []
    _install(monkeypatch, "claude_cli", record=calls)
    store = _store(tmp_path)
    store.update({"provider": "anthropic", "model": "claude-sonnet-4-6", "api_key": "sk-ant"})
    store.update_provider("claude_cli", {"base_url": "/opt/bin/claude"})

    keys = store.configured_provider_keys()
    assert keys.count("claude_cli") == 1
    assert ("claude_cli", "/opt/bin/claude") in calls


def test_a_stored_entry_never_enrols_an_uninstalled_cli(tmp_path, monkeypatch):
    """Regression: a CLI row can carry a binary path and a stale ``key_status`` from an
    older validation. Neither proves the binary is still there, so the credentials branch
    must not claim a CLI key — detection alone decides."""
    _install(monkeypatch)  # nothing installed
    store = _store(tmp_path)
    store.update({"provider": "anthropic", "model": "claude-sonnet-4-6", "api_key": "sk-ant"})
    store.update_provider(
        "claude_cli", {"base_url": "/gone/claude", "key_status": "valid", "model": "sonnet"}
    )

    assert "claude_cli" not in store.configured_provider_keys()


def test_no_duplicate_when_a_cli_is_also_the_instance_default(tmp_path, monkeypatch):
    _install(monkeypatch, "claude_cli")
    store = _store(tmp_path)
    store.update({"provider": "claude_cli", "model": "sonnet"})

    keys = store.configured_provider_keys()
    assert keys.count("claude_cli") == 1


def test_detected_clis_reports_all_transports(tmp_path, monkeypatch):
    _install(monkeypatch, "codex_cli")
    store = _store(tmp_path)
    assert store.detected_clis() == {
        "claude_cli": False,
        "codex_cli": True,
        "antigravity_cli": False,
    }


def test_detected_clis_passes_the_stored_path(tmp_path, monkeypatch):
    calls: list = []
    _install(monkeypatch, record=calls)
    store = _store(tmp_path)
    store.update_provider("codex_cli", {"base_url": "/opt/bin/codex"})

    store.detected_clis()
    assert ("codex_cli", "/opt/bin/codex") in calls


# ── The picker: GET /api/models?all=1 ─────────────────────────────────────────


@pytest.fixture
def _api(tmp_path, monkeypatch):
    """A TestClient whose model config lives in the (isolated-per-test-run) DB and whose
    models cache lives under tmp_path."""
    from fastapi.testclient import TestClient

    monkeypatch.setenv("AUTH_DISABLED", "true")
    ModelConfigStore().update({"provider": "anthropic", "model": "claude-sonnet-4-6"})
    monkeypatch.setattr("backend.api.models.MODELS_CACHE_JSON", tmp_path / "models_cache.json")

    from backend.main import app

    return TestClient(app)


def test_installed_cli_models_appear_in_the_union(_api, tmp_path, monkeypatch):
    """The user-visible fix: an installed CLI gets its own labelled group in the picker."""
    _install(monkeypatch, "claude_cli")
    monkeypatch.setattr(
        "backend.api.models._probe_cli",
        lambda transport, path: ("valid", ["sonnet", "opus"], ""),
    )

    body = _api.get("/api/models?all=1").json()
    cli_rows = [m for m in body["models"] if m.get("provider_key") == "claude_cli"]

    assert [m["id"] for m in cli_rows] == ["sonnet", "opus"]
    assert {m["provider_label"] for m in cli_rows} == {"Claude Code CLI"}


def test_a_failed_probe_contributes_no_models(_api, tmp_path, monkeypatch):
    """Regression: a missing/too-old binary used to fall through to the curated static
    floor, so the picker offered seven models that error on the first turn. A CLI has no
    'known set we merely can't reach' — a failed probe means every id is unrunnable."""
    _install(monkeypatch, "claude_cli")
    monkeypatch.setattr(
        "backend.api.models._probe_cli",
        lambda transport, path: ("unreachable", [], "Claude Code CLI 1.0.0 is too old"),
    )

    body = _api.get("/api/models?all=1").json()
    assert [m for m in body["models"] if m.get("provider_key") == "claude_cli"] == []


def test_uninstalled_cli_has_no_group(_api, tmp_path, monkeypatch):
    _install(monkeypatch)
    body = _api.get("/api/models?all=1").json()
    keys = {m.get("provider_key") for m in body["models"]}
    assert "claude_cli" not in keys
    assert "codex_cli" not in keys
    assert "antigravity_cli" not in keys


def test_providers_endpoint_reports_detection(_api, monkeypatch):
    """The Settings row needs detection to say anything true about itself — these
    transports never store credentials to infer it from."""
    _install(monkeypatch, "codex_cli")
    body = _api.get("/api/model-config/providers").json()
    assert body["detected_clis"] == {
        "claude_cli": False,
        "codex_cli": True,
        "antigravity_cli": False,
    }


def test_catalog_carries_the_cli_binary_name(_api):
    """The frontend + the version probe both resolve the binary from the catalog, so it
    must ride along in the response rather than being duplicated per consumer."""
    catalog = {e["key"]: e for e in _api.get("/api/model-config/providers").json()["catalog"]}
    assert catalog["claude_cli"]["cli_binary"] == "claude"
    assert catalog["codex_cli"]["cli_binary"] == "codex"
    assert catalog["antigravity_cli"]["cli_binary"] == "agy"
    assert "cli_binary" not in catalog["openrouter"]
