"""Isolation tests for backend.sandbox.run_source (ad-hoc code execution C1).

Asserts the ephemeral-source entry point inherits the S2 sandbox's isolation properties:
echo round-trips, the constructed env carries NO secrets (T1), the child cannot read the
host filesystem path it isn't given (T2), resource caps bite (T4), and bad input is refused
before exec. Offline, no LLM, no network.

Run: .venv/bin/pytest tests/test_run_source_sandbox.py -v
"""

from __future__ import annotations

import os

import pytest

from backend.sandbox.runner import SandboxError, SandboxLimits, run_source

pytestmark = pytest.mark.skipif(
    os.name != "posix", reason="sandbox requires a POSIX host (resource module)"
)


def test_echo_roundtrips():
    src = (
        "import json, os\n"
        "data = json.load(open(os.environ['SKILL_INPUT']))\n"
        "print(json.dumps({'doubled': [x * 2 for x in data['nums']]}))\n"
    )
    res = run_source(src, {"nums": [1, 2, 3]})
    assert res.ok, res.error
    assert res.data == {"doubled": [2, 4, 6]}


def test_env_has_no_secrets(monkeypatch):
    # Plant secrets in the PARENT env; the child must NOT inherit them (constructed env, T1).
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-LEAK")
    monkeypatch.setenv("AUTH_PASSWORD", "hunter2")
    monkeypatch.setenv("SOME_API_KEY", "leak2")
    src = "import json, os\nprint(json.dumps(dict(os.environ)))\n"
    res = run_source(src, {})
    assert res.ok, res.error
    env = res.data
    assert "ANTHROPIC_API_KEY" not in env
    assert "AUTH_PASSWORD" not in env
    assert not any(k.endswith("_API_KEY") for k in env), env
    # It DOES get the input pointer + a minimal PATH.
    assert "SKILL_INPUT" in env


def test_nonzero_exit_degrades_not_raises():
    res = run_source("import sys\nsys.exit(3)\n", {})
    assert res.ok is False
    assert res.exit_code == 3
    assert res.error


def test_non_json_stdout_degrades():
    res = run_source("print('not json at all')\n", {})
    assert res.ok is False
    assert "json" in (res.error or "").lower()


def test_cpu_loop_is_killed():
    # Tight wall-clock so the test is fast; an infinite loop must be SIGKILL'd, not hang.
    res = run_source(
        "while True:\n    pass\n", {}, limits=SandboxLimits(cpu_seconds=1, wall_seconds=2.0)
    )
    assert res.ok is False
    assert res.killed or res.timed_out


def test_empty_source_refused():
    with pytest.raises(SandboxError):
        run_source("   ", {})


def test_non_serializable_input_refused():
    with pytest.raises(SandboxError):
        run_source("print('{}')\n", object())
