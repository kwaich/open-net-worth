"""The live-session fan-out: a chat panel follows a turn it did not start.

An Automation run is driven by the scheduler, so nothing connects the browser to it. This
broker is that wire. The properties that matter are about NOT harming the run: a watcher
must never block it, and no watcher at all must cost nothing.

``begin``/``end`` are keyed by ``(session_id, turn_id)`` (security review, 2026-08-06): Phase
2 (concurrent-conversations RFC) added a second caller on the same session key space — an
interactive POST can now overlap an Automation run already mid-turn on the SAME session — so
the bracket must tell two overlapping turns apart instead of a bare per-session counter that
a mismatched begin/end pair could silently corrupt.

Async tests run under pytest-asyncio (``asyncio_mode=auto``) rather than ``asyncio.run``:
that helper CLOSES the loop it made and leaves the thread with no current loop, and the
next module to import ``backend.ai.tools`` builds an ``asyncio.Lock()`` at import time —
so a stray ``asyncio.run`` here fails unrelated test files with "no current event loop".
"""

from __future__ import annotations

import asyncio

import pytest

from backend.ai import live_sessions


@pytest.fixture(autouse=True)
def _clean():
    live_sessions._reset_for_tests()
    yield
    live_sessions._reset_for_tests()


async def _collect(session_id: str, out: list[dict]) -> None:
    async for event in live_sessions.watch(session_id):
        out.append(event)


async def test_a_watcher_receives_the_events_published_after_it_attaches():
    live_sessions.begin("s1", "t1")
    out: list[dict] = []
    task = asyncio.ensure_future(_collect("s1", out))
    await asyncio.sleep(0)  # let the watcher register
    live_sessions.publish("s1", {"type": "text_delta", "text": "hi"})
    live_sessions.end("s1", "t1")
    await asyncio.wait_for(task, timeout=1)

    assert out == [{"type": "text_delta", "text": "hi"}]


async def test_a_watcher_that_joins_mid_turn_gets_the_turn_so_far():
    """Opening the panel halfway through a run must not start mid-sentence."""
    live_sessions.begin("s1", "t1")
    live_sessions.publish("s1", {"type": "text_delta", "text": "already "})
    out: list[dict] = []
    task = asyncio.ensure_future(_collect("s1", out))
    await asyncio.sleep(0)
    live_sessions.publish("s1", {"type": "text_delta", "text": "said"})
    live_sessions.end("s1", "t1")
    await asyncio.wait_for(task, timeout=1)

    assert [e["text"] for e in out] == ["already ", "said"]


async def test_the_replay_is_not_delivered_twice():
    live_sessions.begin("s1", "t1")
    live_sessions.publish("s1", {"type": "text_delta", "text": "a"})
    out: list[dict] = []
    task = asyncio.ensure_future(_collect("s1", out))
    await asyncio.sleep(0)
    live_sessions.end("s1", "t1")
    await asyncio.wait_for(task, timeout=1)

    assert len(out) == 1


async def test_watching_a_session_that_is_not_live_returns_immediately():
    """A client that arrives after the run finished must fall through to the transcript,
    not hang on an open connection forever."""
    out: list[dict] = []
    await asyncio.wait_for(_collect("nope", out), timeout=1)

    assert out == []


def test_publishing_with_no_watcher_is_a_no_op():
    """The 3am scheduled run: nobody is looking, and it must cost nothing."""
    live_sessions.begin("s1", "t1")
    live_sessions.publish("s1", {"type": "text_delta", "text": "x"})

    assert live_sessions.watcher_count("s1") == 0


async def test_a_full_watcher_queue_never_blocks_the_run():
    """A frozen tab must not stall a turn that is mid-write to the ledger. Its events are
    dropped; the transcript stays the durable record it reconciles against."""
    live_sessions.begin("s1", "t1")
    queue: asyncio.Queue = asyncio.Queue(maxsize=1)
    live_sessions._subscribers["s1"] = {queue}
    for i in range(50):
        live_sessions.publish("s1", {"type": "text_delta", "text": str(i)})

    assert queue.qsize() == 1


async def test_end_always_releases_a_watcher_with_a_full_queue():
    live_sessions.begin("s1", "t1")
    task = asyncio.ensure_future(_collect("s1", []))
    await asyncio.sleep(0)  # register the watcher and let it block on queue.get()

    for i in range(live_sessions._QUEUE_MAX):
        live_sessions.publish("s1", {"type": "text_delta", "text": str(i)})

    live_sessions.end("s1", "t1")

    await asyncio.wait_for(task, timeout=1)


async def test_end_releases_every_watcher():
    live_sessions.begin("s1", "t1")
    done = [False, False]

    async def watch(idx: int):
        async for _ in live_sessions.watch("s1"):
            pass
        done[idx] = True

    tasks = [asyncio.ensure_future(watch(0)), asyncio.ensure_future(watch(1))]
    await asyncio.sleep(0)
    live_sessions.end("s1", "t1")
    await asyncio.wait_for(asyncio.gather(*tasks), timeout=1)

    assert done == [True, True]


def test_a_finished_session_is_no_longer_reported_live():
    live_sessions.begin("s1", "t1")
    assert live_sessions.live_session_ids() == ["s1"]

    live_sessions.end("s1", "t1")
    assert live_sessions.live_session_ids() == []
    assert not live_sessions.is_live("s1")


def test_the_replay_buffer_is_bounded():
    live_sessions.begin("s1", "t1")
    for i in range(live_sessions._REPLAY_MAX + 100):
        live_sessions.publish("s1", {"type": "text_delta", "text": str(i)})

    assert len(live_sessions._replay["s1"]) == live_sessions._REPLAY_MAX


async def test_a_disconnected_watcher_is_forgotten():
    """Otherwise a long-lived server accumulates dead queues for every panel ever opened."""
    live_sessions.begin("s1", "t1")
    task = asyncio.ensure_future(_collect("s1", []))
    await asyncio.sleep(0)
    assert live_sessions.watcher_count("s1") == 1

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert live_sessions.watcher_count("s1") == 0


# ── Turn-scoped begin/end (security review, 2026-08-06) ─────────────────────────────────


def test_ending_one_of_two_overlapping_turns_does_not_end_the_session():
    """An Automation run (t1) is mid-turn on session S; the user posts a followup into
    the SAME conversation, opening a second, overlapping bracket (t2). t1 finishing first
    must not end the session out from under t2, which is still writing."""
    live_sessions.begin("s1", "t1")
    live_sessions.begin("s1", "t2")

    live_sessions.end("s1", "t1")

    assert live_sessions.is_live("s1"), "the session ended while a sibling turn was still open"
    assert live_sessions.live_session_ids() == ["s1"]

    live_sessions.end("s1", "t2")

    assert not live_sessions.is_live("s1")


def test_a_second_begin_does_not_wipe_the_replay_of_the_first_turn():
    """The naive (pre-fix) begin() unconditionally reset the replay buffer — an overlapping
    turn's begin() would erase events a watcher already joined on for the FIRST turn."""
    live_sessions.begin("s1", "t1")
    live_sessions.publish("s1", {"type": "text_delta", "text": "from t1"})

    live_sessions.begin("s1", "t2")

    assert live_sessions._replay["s1"] == [{"type": "text_delta", "text": "from t1"}]


def test_end_is_a_no_op_for_a_turn_id_that_never_began():
    """A stale/duplicate/mismatched end() must not corrupt a still-open sibling bracket —
    keyed accounting (a set) rather than a bare counter makes this structurally safe."""
    live_sessions.begin("s1", "t1")

    live_sessions.end("s1", "unrelated-turn-id")

    assert live_sessions.is_live("s1"), "an end() for a turn_id that never began ended the session"

    live_sessions.end("s1", "t1")

    assert not live_sessions.is_live("s1")


async def test_a_watcher_survives_a_sibling_turns_end():
    """A watcher attached while TWO turns are open must keep receiving events from the
    still-running one after the OTHER turn ends."""
    live_sessions.begin("s1", "t1")
    live_sessions.begin("s1", "t2")
    out: list[dict] = []
    task = asyncio.ensure_future(_collect("s1", out))
    await asyncio.sleep(0)

    live_sessions.end("s1", "t1")  # t1 finishes first — must not release the watcher
    live_sessions.publish("s1", {"type": "text_delta", "text": "still going"})
    live_sessions.end("s1", "t2")  # only the LAST bracket actually ends the session
    await asyncio.wait_for(task, timeout=1)

    assert out == [{"type": "text_delta", "text": "still going"}]
