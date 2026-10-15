"""Pin the zero-config data-dir defaults (2026-08-23).

Root problem: DATA_DIR/CACHE_DIR/LOGS_DIR used to default to backend/{data,cache,logs}
— INSIDE the git checkout, so a fresh clone's financial database sat where `git
clean`/`git pull` could destroy it, and backend/data/ was simultaneously git-tracked
seed data AND the live data dir. The fix moves the default outside the checkout, to
~/.open-net-worth/{data,cache,logs} — the same directory core/config.py already
called "the user's production instance directory". See
docs/adr/2026-08-23-zero-config-default-data-dir.md.

Three groups:
  1. The defaults themselves, and that an env var still always wins.
  2. The QA fail-closed guards still correctly treat the (now-default) live tree
     and the repo seed dir as protected.
  3. The legacy-install guard (_assert_not_orphaning_legacy_data_dir) — refuses to
     boot when an old, unmigrated database would otherwise be silently orphaned.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import backend.core.config as config


@pytest.fixture(autouse=True)
def _restore_settings_singleton():
    """Never leak a test's Settings singleton (or monkeypatched _BACKEND) forward."""
    config._settings = None
    yield
    config._settings = None


# ── 1. Defaults + env override precedence ────────────────────────────────────


def test_default_data_dir_is_outside_the_repo():
    assert config._DEFAULT_DATA_DIR == Path.home() / ".open-net-worth" / "data"


def test_default_cache_dir_is_outside_the_repo():
    assert config._DEFAULT_CACHE_DIR == Path.home() / ".open-net-worth" / "cache"


def test_default_log_dir_is_outside_the_repo():
    assert config._DEFAULT_LOG_DIR == Path.home() / ".open-net-worth" / "logs"


def test_unset_data_dir_resolves_to_the_new_default(monkeypatch):
    monkeypatch.delenv("DATA_DIR", raising=False)
    assert config._env_path("DATA_DIR", config._DEFAULT_DATA_DIR) == config._DEFAULT_DATA_DIR


def test_env_var_still_wins_over_the_default(monkeypatch, tmp_path):
    """The owner's real install pins DATA_DIR/CACHE_DIR/LOGS_DIR in .env — this must
    keep winning over the default no matter where the default itself points."""
    override = tmp_path / "somewhere-else"
    monkeypatch.setenv("DATA_DIR", str(override))
    assert config._env_path("DATA_DIR", config._DEFAULT_DATA_DIR) == override


def test_settings_singleton_honors_env_override(monkeypatch, tmp_path):
    data_dir = tmp_path / "data"
    cache_dir = tmp_path / "cache"
    logs_dir = tmp_path / "logs"
    monkeypatch.setenv("DATA_DIR", str(data_dir))
    monkeypatch.setenv("CACHE_DIR", str(cache_dir))
    monkeypatch.setenv("LOGS_DIR", str(logs_dir))
    config._settings = None
    settings = config.get_settings()
    assert settings.data_dir == data_dir
    assert settings.cache_dir == cache_dir
    assert settings.log_dir == logs_dir


# ── 2. QA guard still treats the (now-default) live tree as protected ────────


def test_real_data_dirs_still_include_the_live_instance_and_the_repo_seed():
    live = Path.home() / ".open-net-worth" / "data"
    repo_seed = config._BACKEND / "data"
    assert live in config._REAL_DATA_DIRS
    assert repo_seed in config._REAL_DATA_DIRS


def test_qa_mode_still_refuses_the_new_default_data_dir(monkeypatch):
    """A QA boot that forgets to override DATA_DIR now falls through to the LIVE
    tree (it used to fall through to the harmless repo seed dir) — the QA guard
    must still catch this, or QA mode became MORE dangerous by this change, not
    just differently defaulted."""
    monkeypatch.setenv("ONW_QA_MODE", "1")
    config._settings = None
    with pytest.raises(SystemExit):
        config.assert_qa_data_dir_safe(config._DEFAULT_DATA_DIR)


def test_qa_mode_still_refuses_the_repo_seed_dir(monkeypatch):
    monkeypatch.setenv("ONW_QA_MODE", "1")
    config._settings = None
    with pytest.raises(SystemExit):
        config.assert_qa_data_dir_safe(config._BACKEND / "data")


def test_qa_mode_allows_an_isolated_dir(monkeypatch, tmp_path):
    monkeypatch.setenv("ONW_QA_MODE", "1")
    config._settings = None
    config.assert_qa_data_dir_safe(tmp_path / "qa-data")  # must not raise


# ── 3. Legacy-install guard ───────────────────────────────────────────────────


def _fake_legacy_tree(tmp_path: Path, *, db_bytes: bytes | None = b"x" * 4096) -> Path:
    """Build a fake backend/ dir with (optionally) a non-empty legacy onw.db."""
    fake_backend = tmp_path / "fake-repo" / "backend"
    (fake_backend / "data").mkdir(parents=True)
    if db_bytes is not None:
        (fake_backend / "data" / "onw.db").write_bytes(db_bytes)
    return fake_backend
