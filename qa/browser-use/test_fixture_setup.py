"""Regression tests for the shared browser-QA fixture execution boundary."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_PATH = Path(__file__).with_name("fixture_setup.py")
_SPEC = importlib.util.spec_from_file_location("fixture_setup", _PATH)
assert _SPEC and _SPEC.loader
fixture_setup = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = fixture_setup
_SPEC.loader.exec_module(fixture_setup)


def _attested(_backend_url: str, _nonce: str):
    return fixture_setup.FixtureResult(True, "ok")


def _script(path: Path, body: str) -> None:
    path.write_text(body)


def test_rejects_path_traversal_before_execution(tmp_path):
    outside = tmp_path.parent / "outside_fixture.py"
    _script(outside, "raise SystemExit('must not execute')\n")

    result = fixture_setup.run_fixture_script(
        "../outside_fixture.py",
        fixtures_dir=tmp_path,
        backend_url="http://localhost:8091",
        qa_nonce="nonce",
        attest=_attested,
    )

    assert result.ok is False
    assert "invalid setup_script" in result.detail


def test_forwards_backend_url_and_nonce_without_a_shell(tmp_path):
    _script(
        tmp_path / "ok.py",
        "import argparse\n"
        "p=argparse.ArgumentParser()\n"
        "p.add_argument('--backend-url')\n"
        "p.add_argument('--qa-nonce')\n"
        "a=p.parse_args()\n"
        "print(a.backend_url, a.qa_nonce)\n",
    )

    result = fixture_setup.run_fixture_script(
        "ok.py",
        fixtures_dir=tmp_path,
        backend_url="http://localhost:8091",
        qa_nonce="one-run",
        attest=_attested,
    )

    assert result.ok is True
    assert result.output == "http://localhost:8091 one-run"


def test_nonzero_exit_is_an_error(tmp_path):
    _script(tmp_path / "fail.py", "raise SystemExit('fixture failed safely')\n")

    result = fixture_setup.run_fixture_script(
        "fail.py",
        fixtures_dir=tmp_path,
        backend_url="http://localhost:8091",
        qa_nonce="nonce",
        attest=_attested,
    )

    assert result.ok is False
    assert "fixture failed safely" in result.detail


def test_timeout_is_an_error(tmp_path):
    _script(tmp_path / "slow.py", "import time\ntime.sleep(1)\n")

    result = fixture_setup.run_fixture_script(
        "slow.py",
        fixtures_dir=tmp_path,
        backend_url="http://localhost:8091",
        qa_nonce="nonce",
        timeout_s=0.01,
        attest=_attested,
    )

    assert result.ok is False
    assert "timed out" in result.detail
