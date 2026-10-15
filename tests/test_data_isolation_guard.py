"""Regression guard for the test-suite data-isolation backstop (conftest.py).

A test once leaked into the operator's live ``~/.open-net-worth/data`` (it renamed
the instance to "My Money"). The conftest now redirects DATA_DIR/CACHE_DIR to a
throwaway dir BEFORE any backend import. This test fails loudly if that backstop
ever regresses — i.e. if the suite is somehow pointed at a real instance dir.
"""

from __future__ import annotations

import backend.core.config as config
import backend.paths as paths


def test_data_dir_is_a_throwaway_not_the_live_instance():
    data_dir = str(paths.DATA_DIR)
    # Never the operator's real instance data.
    assert "/.open-net-worth/" not in data_dir, data_dir
    # It is the conftest-provisioned throwaway.
    assert "onw-pytest-data-" in data_dir, data_dir


def test_settings_data_dir_matches_paths():
    # The env redirect took effect at Settings resolution, not just in paths.
    assert str(config.get_settings().data_dir) == str(paths.DATA_DIR)
