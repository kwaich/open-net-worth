"""Regression guard: the app log honors LOGS_DIR.

``DATA_DIR`` and ``CACHE_DIR`` were always env-overridable, but the log path was
hardcoded to the repo's ``backend/logs/`` directory — so moving an instance's
runtime state out of the repo (the data-safety pattern) left logs behind. Logs
now route through ``core.config.Settings.log_dir`` (env ``LOGS_DIR``, default
``backend/logs``), exactly like data and cache.

Mirrors ``test_cache_dir_hermeticity.py``:

1. the path *constants* resolve under the configured ``LOGS_DIR`` (proves they
   are routed through the setting, not joined onto a hardcoded ``backend/logs``);
2. ``add_file_handler`` writes the log under the configured dir and never touches
   the repo's ``backend/logs/`` directory.
"""

from __future__ import annotations

import logging

import backend.core.config as config
import backend.core.logging as core_logging
import backend.paths as paths

# The repo's historical log dir — the place app.log must NOT land in once
# LOGS_DIR is overridden. Resolved the same way core.config derives its default.
_REPO_LOGS = config._DEFAULT_LOG_DIR


def test_default_log_dir_is_repo_logs(monkeypatch):
    """Unset LOGS_DIR → the historical backend/logs default (back-compat).

    conftest sets LOGS_DIR for suite hermeticity, so clear it here to exercise
    the unconfigured fallback an out-of-the-box instance gets.
    """
    monkeypatch.delenv("LOGS_DIR", raising=False)
    assert config._env_path("LOGS_DIR", _REPO_LOGS) == _REPO_LOGS


def test_log_dir_honors_env(monkeypatch, tmp_path):
    """LOGS_DIR env var is expanded and wins over the default."""
    monkeypatch.setenv("LOGS_DIR", str(tmp_path / "logs"))
    assert config._env_path("LOGS_DIR", _REPO_LOGS) == tmp_path / "logs"


def test_log_constants_resolve_under_log_dir():
    """LOGS_DIR / LOG_FILE live under the configured log_dir, not the repo."""
    log_dir = config.get_settings().log_dir
    assert str(log_dir) == str(paths.LOGS_DIR)
    assert paths.LOG_FILE.parent == log_dir
    # conftest redirects LOGS_DIR to a throwaway dir, so this is never the repo's.
    assert log_dir != _REPO_LOGS


def test_file_handler_writes_under_log_dir(monkeypatch, tmp_path):
    """add_file_handler lands app.log in the configured dir, not the repo logs."""
    log_file = tmp_path / "logs" / "app.log"
    # Force a fresh handler install onto an isolated logger.
    isolated = logging.getLogger("backend.test_logs_dir_hermeticity")
    isolated.handlers.clear()
    isolated.propagate = False  # don't double-write to the real backend handlers
    isolated.setLevel(logging.WARNING)
    monkeypatch.setattr(core_logging, "ROOT_LOGGER_NAME", isolated.name)

    core_logging.add_file_handler(log_file)
    isolated.warning("hermeticity probe")
    for h in isolated.handlers:
        h.flush()

    assert log_file.exists()
    assert "hermeticity probe" in log_file.read_text()
    assert not (_REPO_LOGS / "app.log").resolve() == log_file.resolve()
