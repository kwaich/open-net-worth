"""Catch whatever is blocking the asyncio event loop, with its stack.

Live 2026-08-11: during an Automation run the whole app stopped answering — the owner had
to kill the process. Endpoints logged ``compute=392ms`` with ``db=0ms`` on work that
measures ~15ms idle, which is the signature of a handler waiting to be SCHEDULED rather
than doing anything. Four separate theories about the cause were each disproved by
measurement (run-transcript loads 34ms, the count-everything query 12ms, email parsing 2ms,
dev-server reloads not correlated). Reading more code was not converging.

py-spy would answer it instantly but needs root on macOS, so this samples from inside the
process instead: a task sleeps in a tight loop and measures how late it actually wakes.
Lateness IS loop lag — nothing else can delay it. When lag crosses the threshold, every
other task's stack is dumped, which names the culprit rather than narrowing it.

Diagnostic, not a fix. Cheap enough to leave on (a 250ms timer and a subtraction), and it
only writes to the log when something is already wrong.
"""

from __future__ import annotations

import asyncio
import logging
import sys
import threading
import time
import traceback
from collections import deque

logger = logging.getLogger(__name__)

# How often to wake. Short enough to catch a stall inside one request, long enough to be
# free. The task does nothing but sleep, so this is not a busy loop.
_TICK_SECONDS = 0.25

# Report when a wake is this much later than it should have been. 500ms is well past normal
# jitter and well under the multi-second stalls being chased.
_LAG_THRESHOLD_SECONDS = 0.5

# One report per stall, not one per tick, so a 30-second block is one log entry.
_QUIET_AFTER_REPORT_SECONDS = 5.0

# Stacks are for a human reading a log line; the top frames carry the answer.
_STACK_DEPTH = 12

# The out-of-loop sampler: how often it looks, and how many identical looks mean "stuck".
_SAMPLE_SECONDS = 0.1
_SAMPLES_TO_REPORT = 5

# An IDLE loop parks in one of these, waiting for IO. Anything else on the loop thread is
# code that is RUNNING, which is what the sampler is looking for.
_IDLE_FRAME_MARKERS = ("/asyncio/", "selectors.py", "uvicorn/server.py")

# Every non-idle sample, kept just long enough to describe the window a stall happened in.
# 200 samples at 100ms is 20s of history — far more than any lag report needs, and bounded
# so a long-running process cannot grow it.
_recent_samples: deque[tuple[float, str]] = deque(maxlen=200)


def _is_idle_frame(filename: str) -> bool:
    """True when the loop thread is parked waiting for IO rather than running something.

    Split out of the sampler and pinned by a test because the first version got it wrong in
    a way that inverted the whole diagnostic: it scanned only the last two frames and wrote
    ``A or B and C``, which parses as ``A or (B and C)``. An idle stack that bottoms out in
    native code has no selectors frame at all, so it sailed through and named uvicorn's own
    bootstrap as "the blocker" (false positive, live 2026-08-11 17:10). A watchdog that
    cries wolf while idle is worse than no watchdog, so this stays covered.
    """
    return any(marker in filename for marker in _IDLE_FRAME_MARKERS)


def summarise_window(lag: float, now: float) -> str:
    """What the out-of-loop sampler saw DURING the stall, as "N× file:line" lines.

    This exists because a stall does not have to have a single culprit. ``_SAMPLES_TO_REPORT``
    only fires on 5 consecutive samples in the SAME frame, so it names one long block and says
    nothing at all about a stall assembled from many short ones. Live 2026-08-11 produced two
    reports (642ms and 659ms) with no accompanying BLOCKED line and every task parked at an
    await — a genuinely ambiguous picture that this turns into evidence.

    Three outcomes, and the difference between them is the whole point:
      · several DIFFERENT frames  → death by a thousand cuts, no single fix
      · one frame just under the threshold → a near-miss the BLOCKED path narrowly skipped
      · nothing at all → the loop thread was never caught running Python, so it was not
        blocked, it was STARVED — GIL contention from the request threadpool, or a C
        extension that never dropped the GIL. A different problem with a different fix.
    """
    seen = [(t, w) for t, w in _recent_samples if t >= now - lag]
    if not seen:
        return (
            "  sampler saw the loop thread idle throughout — it was STARVED, not blocked "
            "(GIL contention from the request threadpool, or a C extension holding the GIL). "
            "Offloading more work will NOT help; look at concurrent CPU instead."
        )
    counts: dict[str, int] = {}
    for _t, where in seen:
        counts[where] = counts.get(where, 0) + 1
    ranked = sorted(counts.items(), key=lambda kv: kv[1], reverse=True)
    verdict = (
        "one frame dominated — a near-miss on the BLOCKED threshold"
        if len(ranked) == 1
        else f"{len(ranked)} distinct frames — the stall is many short blocks, not one"
    )
    lines = "\n".join(f"    {n}× {where}" for where, n in ranked[:8])
    return f"  loop thread was running Python during the stall ({verdict}):\n{lines}"


def _dump_running_tasks(lag: float) -> None:
    """Log every other task's innermost frames, plus what the sampler saw during the stall.

    The task stacks alone are famously unhelpful here — a coroutine sampler is only scheduled
    once the loop is free AGAIN, so every task has already parked at an await by the time it
    looks. ``summarise_window`` is what makes the report actionable.
    """
    lines: list[str] = []
    current = asyncio.current_task()
    for task in asyncio.all_tasks():
        if task is current or task.done():
            continue
        frames = task.get_stack(limit=_STACK_DEPTH)
        if not frames:
            continue
        where = "".join(traceback.format_list(traceback.extract_stack(frames[-1]))[-3:])
        lines.append(f"  · {task.get_name()}\n{where.rstrip()}")
    logger.warning(
        "event loop stalled %.0fms — %d task(s) alive:\n%s\n%s",
        lag * 1000,
        len(lines),
        summarise_window(lag, time.perf_counter()),
        "\n".join(lines) or "  (no task stacks)",
    )


def _sample_blocked_thread(main_thread_id: int) -> None:
    """Watch the LOOP THREAD from outside, and name what is running while it is stuck.

    The task-based dump below cannot do this: a coroutine sampler only gets scheduled once
    the loop is free again, so by the time it looks, every task is parked at an await and
    the culprit has already yielded — which is exactly what the first live capture showed
    (18 tasks, all awaiting, loop 682ms late). Sampling from a separate OS thread runs
    DURING the stall, so the stack it grabs is the code actually holding the thread.

    A C extension that never drops the GIL would starve this too, but nearly all blocking
    Python releases it periodically, so in practice this catches the sync call.
    """
    consecutive = 0
    previous_top = ""
    while True:
        time.sleep(_SAMPLE_SECONDS)
        frame = sys._current_frames().get(main_thread_id)
        if frame is None:
            continue
        stack = traceback.extract_stack(frame)
        # An IDLE loop is the common case and must never be reported — see _is_idle_frame.
        if _is_idle_frame(stack[-1].filename):
            consecutive = 0
            continue
        top = f"{stack[-1].filename}:{stack[-1].lineno}"
        # Recorded for EVERY non-idle sample, not just ones that reach the consecutive
        # threshold — summarise_window needs the near-misses and the short blocks too.
        _recent_samples.append((time.perf_counter(), top))
        consecutive = consecutive + 1 if top == previous_top else 0
        previous_top = top
        if consecutive == _SAMPLES_TO_REPORT:
            logger.warning(
                "loop thread BLOCKED >%.0fms in the same frame — this is the blocker:\n%s",
                _SAMPLE_SECONDS * _SAMPLES_TO_REPORT * 1000,
                "".join(traceback.format_list(stack[-_STACK_DEPTH:])).rstrip(),
            )


def start_blocking_sampler() -> None:
    """Start the out-of-loop sampler. Daemon: it must never hold shutdown open."""
    threading.Thread(
        target=_sample_blocked_thread,
        args=(threading.get_ident(),),
        name="loop_watch_sampler",
        daemon=True,
    ).start()


async def watch_event_loop() -> None:
    """Sample loop lag forever. Cancelled with the app."""
    start_blocking_sampler()
    logger.info("loop_watch: sampling every %.0fms", _TICK_SECONDS * 1000)
    last_report = 0.0
    while True:
        before = time.perf_counter()
        await asyncio.sleep(_TICK_SECONDS)
        lag = (time.perf_counter() - before) - _TICK_SECONDS
        if lag < _LAG_THRESHOLD_SECONDS:
            continue
        now = time.perf_counter()
        if now - last_report < _QUIET_AFTER_REPORT_SECONDS:
            continue
        last_report = now
        try:
            _dump_running_tasks(lag)
        except Exception:  # noqa: BLE001 — a diagnostic must never take the app down
            logger.warning("loop_watch: stalled %.0fms (stack dump failed)", lag * 1000)
