"""Private desktop entry points import before settings, dotenv, or third-party packages."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.mark.parametrize("module", ["backend.cli.desktop", "backend.cli.desktop_file"])
def test_private_entry_imports_without_site_packages_or_configuration(module):
    environment = {
        key: value for key, value in os.environ.items() if key in {"PATH", "HOME", "SYSTEMROOT"}
    }
    script = (
        "import importlib, sys; "
        f"importlib.import_module({module!r}); "
        # desktop_file registers backend.core as an empty namespace (no __file__) so only its two
        # stdlib-only modules load; what must never be present is the real package.
        "assert getattr(sys.modules.get('backend.core'), '__file__', None) is None; "
        "assert 'backend.core.config' not in sys.modules; "
        "assert 'dotenv' not in sys.modules; "
        "print('PRIVATE_BOOTSTRAP_IMPORT_OK')"
    )
    result = subprocess.run(
        [sys.executable, "-S", "-c", script],
        cwd=Path(__file__).resolve().parents[1],
        env=environment,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == "PRIVATE_BOOTSTRAP_IMPORT_OK\n"
