"""connectors.turn_registry — the /stop cancellation registry.

Proves the core mechanism behind connector /stop: a stop cancels the ACTIVE turn task (and any
pending debounce flush) for a channel, which is what raises CancelledError into assistant.run →
the LLM stream tears down. The higher-level wiring (handle_inbound / Telegram handle_stop) is
covered in test_connector_stop.py; this file pins the registry's own contract.
"""

from __future__ import annotations

import asyncio

import pytest

from backend.connectors import turn_registry


@pytest.fixture(autouse=True)
def _clean_registry():
    turn_registry.reset()
    yield
    turn_registry.reset()


@pytest.mark.asyncio
async def test_stop_cancels_the_active_turn_task():
    started = asyncio.Event()

    async def _turn():
        started.set()
        await asyncio.sleep(60)  # a "long generation" — only a cancel ends it

    task = asyncio.create_task(_turn())
    await started.wait()
    turn_registry.register_active("telegram", "42", task)
    assert turn_registry.is_running("telegram", "42") is True

    stopped = turn_registry.stop("telegram", "42")
    assert stopped is True
    with pytest.raises(asyncio.CancelledError):
        await task
    assert task.cancelled() is True


@pytest.mark.asyncio
async def test_stop_cancels_a_pending_debounce_flush():
    """A stop during the debounce window cancels the not-yet-launched turn (OpenClaw #51046)."""
    launched = False

    async def _flush():
        nonlocal launched
        await asyncio.sleep(0.5)
        launched = True

    task = asyncio.create_task(_flush())
    turn_registry.register_debounce("telegram", "42", task)

    stopped = turn_registry.stop("telegram", "42")
    assert stopped is True
    with pytest.raises(asyncio.CancelledError):
        await task
    assert launched is False  # never fired


def test_stop_with_nothing_running_returns_false():
    assert turn_registry.stop("telegram", "999") is False


@pytest.mark.asyncio
async def test_clear_active_never_clobbers_a_newer_turn():
    """A slow turn A finishing must not evict a newer turn B that already registered."""

    async def _noop():
        await asyncio.sleep(60)

    a = asyncio.create_task(_noop())
    b = asyncio.create_task(_noop())
    try:
        turn_registry.register_active("telegram", "42", a)
        turn_registry.register_active("telegram", "42", b)  # B supersedes A
        # A's finally runs LATE and tries to clear — must be a no-op (B is active now).
        turn_registry.clear_active("telegram", "42", a)
        assert turn_registry.is_running("telegram", "42") is True
        # stop() still cancels B (the real active turn), not A.
        assert turn_registry.stop("telegram", "42") is True
    finally:
        a.cancel()
        b.cancel()
        for t in (a, b):
            with pytest.raises(asyncio.CancelledError):
                await t


@pytest.mark.asyncio
async def test_channel_isolation_and_connector_case_insensitive():
    async def _noop():
        await asyncio.sleep(60)

    ta = asyncio.create_task(_noop())
    tb = asyncio.create_task(_noop())
    try:
        turn_registry.register_active("Telegram", "1", ta)  # mixed case
        turn_registry.register_active("telegram", "2", tb)
        # Stopping chat 1 (case-insensitive match) leaves chat 2 running.
        assert turn_registry.stop("telegram", "1") is True
        assert turn_registry.is_running("telegram", "2") is True
        assert turn_registry.stop("telegram", "2") is True
    finally:
        for t in (ta, tb):
            t.cancel()
            with pytest.raises(asyncio.CancelledError):
                await t


@pytest.mark.asyncio
async def test_clear_active_removes_only_a_done_matching_task():
    async def _quick():
        return None

    task = asyncio.create_task(_quick())
    await task
    turn_registry.register_active("telegram", "42", task)
    turn_registry.clear_active("telegram", "42", task)
    assert turn_registry.is_running("telegram", "42") is False
    assert turn_registry.stop("telegram", "42") is False
