"""ContextVar seam for capturing per-agent-run tool-call steps.

Services layer (agents.py) sets the collection window; ai layer (assistant.py) appends
steps inside the dispatch loop. Both import from here to avoid a services→ai import which
would violate the layering rule (api→ai→services→db→core).
"""

from __future__ import annotations

from contextvars import ContextVar

_run_steps: ContextVar[list | None] = ContextVar("_run_steps", default=None)


def start_run_steps() -> None:
    """Called before a headless runner to begin step collection."""
    _run_steps.set([])


def collect_run_steps() -> list:
    """Called after a headless run to harvest collected steps and reset."""
    steps = _run_steps.get(None) or []
    _run_steps.set(None)
    return steps


def append_run_step(step: dict) -> None:
    """Called inside assistant.py's dispatch loop per tool call/result."""
    steps = _run_steps.get(None)
    if steps is not None:
        steps.append(step)


def settle_in_flight_steps(steps: list, status: str) -> list:
    """Settle whatever was still in flight when a run ended, and hand the list back.

    Lives in ``core`` because two layers must agree on it: ``ai`` settles the steps it
    projects from a finished turn, and ``services`` settles the ones left on the durable
    row when a run's task is cancelled out from under it. When only ``ai`` did this, a
    cancelled run's card kept drawing its interrupted task as still working — forever,
    because nothing was ever going to come back and close it (owner, 2026-08-21).

    Returns the list (rather than only mutating in place) because the reported steps may be
    a fresh projection of the run's declared plan, not the tool-derived list the caller
    holds — the declared plan is a separate projection.
    """
    for step in steps:
        if not isinstance(step, dict):
            continue
        # "running" is a tool step; "in_progress" is a task the run started and never
        # closed. Both mean "still in flight when the run ended".
        if step.get("status") in {"running", "in_progress"}:
            step.update({"status": status, "done": status == "completed"})
    return steps
