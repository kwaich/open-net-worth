"""The terminal runs as the app's user: it must not be able to read the app's secrets."""

from __future__ import annotations

import asyncio
import ctypes
import os
import sys

import pytest

from backend.core import process_hardening
from backend.core.pty_session import PtySession

linux_only = pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Linux /proc")


def test_forget_env_removes_only_the_named_secrets(monkeypatch):
    monkeypatch.setenv("ONW_GATEWAY_TOKEN", "x" * 40)
    monkeypatch.setenv("KEEP_ME", "1")
    process_hardening.forget_env(["ONW_GATEWAY_TOKEN", "NOT_SET"])
    assert "ONW_GATEWAY_TOKEN" not in os.environ and os.environ["KEEP_ME"] == "1"


@pytest.mark.skipif(sys.platform.startswith("linux"), reason="no-op only off Linux")
def test_restriction_is_a_noop_off_linux():
    assert process_hardening.restrict_process_inspection() is False


async def _shell_output(command: str) -> str:
    session = PtySession.open(
        ["/bin/sh", "-c", command],
        env={"PATH": os.environ["PATH"]},
        cwd=os.getcwd(),
        cols=80,
        rows=24,
    )
    seen = b""
    try:
        while (chunk := await asyncio.wait_for(session.read(), 10)) is not None:
            seen += chunk
    finally:
        await session.close()
    return seen.decode(errors="replace")


@linux_only
def test_a_terminal_cannot_read_the_apps_environment():
    libc = ctypes.CDLL(None)
    os.environ["PROBE_SECRET_FOR_TEST"] = "do-not-leak"
    try:
        assert process_hardening.restrict_process_inspection() is True
        out = asyncio.run(_shell_output(f"cat /proc/{os.getpid()}/environ; echo END"))
        assert "do-not-leak" not in out
        assert "Permission denied" in out
    finally:
        libc.prctl(4, 1, 0, 0, 0)
        os.environ.pop("PROBE_SECRET_FOR_TEST", None)
