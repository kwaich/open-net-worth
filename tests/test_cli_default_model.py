"""A CLI's own default model is read from the CLI's config, never from a hint of ours.

Scar: first run seeded Codex with ``gpt-5.6-terra`` (the catalogue's first hint) and the live
list, which also contained terra, kept it — while the owner's ``config.toml`` named another
model and the CLI's own cache led with it. All inputs here are synthetic temp dirs.
"""

from __future__ import annotations

import json

import pytest

from backend.ai.models import cli_default_model as mod
from backend.ai.models.codex_cli_catalog import FALLBACK_MODELS


@pytest.fixture
def homes(tmp_path, monkeypatch):
    codex = tmp_path / "codex"
    claude = tmp_path / "claude"
    codex.mkdir()
    claude.mkdir()
    monkeypatch.setenv("CODEX_HOME", str(codex))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(claude))
    return codex, claude


def _cache(codex, *slugs):
    rows = [
        {"slug": s, "visibility": "list", "priority": i + 1, "supported_reasoning_levels": []}
        for i, s in enumerate(slugs)
    ]
    (codex / "models_cache.json").write_text(json.dumps({"models": rows}))


def test_codex_config_model_wins_over_cache(homes):
    codex, _ = homes
    (codex / "config.toml").write_text('model = "synthetic-sol"\n')
    _cache(codex, "synthetic-lead", "synthetic-sol")
    assert mod.cli_default_model("codex_cli") == "synthetic-sol"


def test_codex_selected_profile_model_wins(homes):
    codex, _ = homes
    (codex / "config.toml").write_text(
        'model = "top"\nprofile = "work"\n[profiles.work]\nmodel = "profile-model"\n'
    )
    assert mod.cli_default_model("codex_cli") == "profile-model"


def test_codex_unselected_profile_is_ignored(homes):
    codex, _ = homes
    (codex / "config.toml").write_text('model = "top"\n[profiles.work]\nmodel = "other"\n')
    assert mod.cli_default_model("codex_cli") == "top"


def test_codex_without_config_leads_with_cache_priority_order(homes):
    codex, _ = homes
    _cache(codex, "synthetic-lead", "synthetic-second")
    assert mod.cli_default_model("codex_cli") == "synthetic-lead"


def test_codex_malformed_config_falls_to_cache(homes):
    codex, _ = homes
    (codex / "config.toml").write_text("model = [unterminated")
    _cache(codex, "synthetic-lead")
    assert mod.cli_default_model("codex_cli") == "synthetic-lead"


def test_codex_non_string_model_is_ignored(homes):
    codex, _ = homes
    (codex / "config.toml").write_text("model = 5\n")
    _cache(codex, "synthetic-lead")
    assert mod.cli_default_model("codex_cli") == "synthetic-lead"


def test_codex_nothing_readable_uses_the_floor(homes):
    assert mod.cli_default_model("codex_cli") == FALLBACK_MODELS[0]


def test_claude_reads_settings_json_model(homes):
    _, claude = homes
    (claude / "settings.json").write_text(json.dumps({"model": "opus"}))
    assert mod.cli_default_model("claude_cli") == "opus"


@pytest.mark.parametrize("content", ["{not json", "[]", json.dumps({"model": 3}), ""])
def test_claude_unusable_settings_use_the_alias_floor(homes, content):
    _, claude = homes
    (claude / "settings.json").write_text(content)
    assert mod.cli_default_model("claude_cli") == "sonnet"


def test_claude_missing_settings_use_the_alias_floor(homes):
    assert mod.cli_default_model("claude_cli") == "sonnet"


def test_claude_config_dir_defaults_to_home(tmp_path, monkeypatch):
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    monkeypatch.setattr(mod.Path, "home", classmethod(lambda cls: tmp_path))
    (tmp_path / ".claude").mkdir()
    (tmp_path / ".claude" / "settings.json").write_text(json.dumps({"model": "haiku"}))
    assert mod.cli_default_model("claude_cli") == "haiku"


def test_unknown_cli_has_no_default(homes):
    assert mod.cli_default_model("antigravity_cli") == ""
    assert set(mod.cli_default_models()) == {"claude_cli", "codex_cli"}
