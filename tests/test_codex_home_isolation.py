"""The app's isolated CODEX_HOME turns off ChatGPT connected apps.

A shared ChatGPT login otherwise brings the account's connected apps (Gmail, Drive, Calendar…)
into every model turn as `codex_apps` tools — outside every Open Net Worth permission. A home
seeded empty by an older release must be upgraded, not left as it was.
"""

from pathlib import Path

import backend.ai.models.codex_home as codex_home


def test_fresh_home_disables_connected_apps(tmp_path, monkeypatch):
    monkeypatch.setattr(codex_home, "_HOME_DIR", tmp_path / "codex-home")
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "operator"))
    home = Path(codex_home.isolated_home())
    config = (home / "config.toml").read_text(encoding="utf-8")
    assert "[features]\napps = false\n" in config


def test_fresh_home_turns_code_mode_off_so_the_text_tool_protocol_is_the_only_one(
    tmp_path, monkeypatch
):
    """gpt-6 models route tool use through Codex code mode (one native `exec` tool, tools
    looked up in `ALL_TOOLS`) unless `code_mode_host` is off — our manifest's tools are never
    registered there, so the model reports it has none (2026-10-03)."""
    monkeypatch.setattr(codex_home, "_HOME_DIR", tmp_path / "codex-home")
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "operator"))
    home = Path(codex_home.isolated_home())
    config = (home / "config.toml").read_text(encoding="utf-8")
    assert "code_mode_host = false" in config
    assert "shell_tool = false" in config


def test_home_seeded_by_an_older_release_is_upgraded(tmp_path, monkeypatch):
    home = tmp_path / "codex-home"
    home.mkdir()
    (home / "config.toml").write_text("# Managed by Open Net Worth — deliberately empty.\n")
    monkeypatch.setattr(codex_home, "_HOME_DIR", home)
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "operator"))
    codex_home.isolated_home()
    upgraded = (home / "config.toml").read_text(encoding="utf-8")
    assert "apps = false" in upgraded
    assert "code_mode_host = false" in upgraded


def test_native_tools_home_enables_only_the_code_mode_carrier(tmp_path, monkeypatch):
    """Dynamic tools are delivered through code mode, so the native-tools home turns the
    host ON — and nothing else of Codex's own (apps, shell) comes back with it."""
    monkeypatch.setattr(codex_home, "_HOME_DIR", tmp_path / "codex-home")
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "operator"))
    text_home = Path(codex_home.isolated_home())
    native_home = Path(codex_home.isolated_home(native_tools=True))
    assert native_home != text_home
    config = (native_home / "config.toml").read_text(encoding="utf-8")
    assert "code_mode_host = true" in config
    assert "apps = false" in config and "shell_tool = false" in config
    assert "code_mode_host = false" in (text_home / "config.toml").read_text(encoding="utf-8")


def test_native_home_and_thread_config_switch_off_every_codex_builtin(tmp_path, monkeypatch):
    """Code mode lists every tool the thread holds: live on 0.160 that included a working
    `web__run`. The home file and the thread config must both carry the switches."""
    monkeypatch.setattr(codex_home, "_HOME_DIR", tmp_path / "codex-home")
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "operator"))
    config = (Path(codex_home.isolated_home(native_tools=True)) / "config.toml").read_text(
        encoding="utf-8"
    )
    assert 'web_search = "disabled"' in config
    for name in codex_home.NATIVE_BUILTIN_FEATURES:
        assert f"{name} = false" in config
        assert codex_home.NATIVE_THREAD_CONFIG["features"][name] is False
    assert codex_home.NATIVE_THREAD_CONFIG["web_search"] == "disabled"
    workspace = Path(codex_home.native_workspace())
    assert workspace.is_dir() and not any(workspace.iterdir())
    assert not (workspace / "auth.json").exists()
