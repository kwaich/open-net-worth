"""A Claude Code CLI that exits right after starting says WHY, instead of only "not running"."""

from __future__ import annotations

import asyncio
import sys

import pytest

from backend.ai.models.claude_live_session import LiveSession
from backend.core.errors import ExternalServiceError


def test_an_exit_at_start_reports_the_clis_own_message(tmp_path):
    async def run():
        argv = [
            sys.executable,
            "-c",
            "import sys; sys.stderr.write('Please run claude login\\n'); sys.exit(1)",
        ]
        session = LiveSession(argv, {}, str(tmp_path))
        await session.start()
        await session.proc.wait()
        with pytest.raises(ExternalServiceError, match="Please run claude login"):
            await session.send("hello")

    asyncio.run(run())


def test_a_silent_exit_still_says_it_stopped(tmp_path):
    async def run():
        session = LiveSession([sys.executable, "-c", "pass"], {}, str(tmp_path))
        await session.start()
        await session.proc.wait()
        with pytest.raises(ExternalServiceError, match="stopped right after starting"):
            await session.send("hello")

    asyncio.run(run())
