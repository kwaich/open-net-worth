"""A run finishes when the work it SAID it would do is accounted for.

Observed 2026-08-03 on 'Log transactions from emails' (openrouter:z-ai/glm-5.2): the model
said "Now let me fetch new emails from the…", called list_accounts + list_categories, then
emitted a sentence with no tool_use. assistant.run ends on the first tool-less response, so
the run was recorded green in 4 seconds having fetched no mail and logged nothing.

"It did nothing" is NOT the test — a silent run is a real success ("no new mail, nothing to
log") and looks identical from the outside. The declared task list is what makes the
difference checkable: five tasks added, two resolved, turn over → the run did not finish,
and the card names the task it stopped on.
"""

from __future__ import annotations

import pytest

from backend.ai import assistant, run_plan
from backend.ai.automation_runner_adapter import automation_runner
from backend.core.automation_runner import AutomationRunnerRequest


def _req(session_id: str) -> AutomationRunnerRequest:
    return AutomationRunnerRequest(
        automation_id="task-0fae2fb5",
        run_id="run_early",
        session_id=session_id,
        title="Log transactions from emails",
        instructions="Fetch new mail and log what you find.",
        model="model-a",
    )


@pytest.fixture
def quiet(monkeypatch):
    monkeypatch.setattr(assistant, "upsert_session_meta", lambda *a, **k: None)
    monkeypatch.setattr(assistant, "save_session_durable", lambda *a, **k: None)
    monkeypatch.setattr(assistant, "load_session_durable", lambda *a, **k: [])
    monkeypatch.setattr(
        "backend.ai.automation_runner_adapter._load_plan_tool", lambda *a, **k: None
    )


def _turn(sid: str, *, tasks: list[dict] | None = None, resolve: dict[str, str] | None = None):
    """A fake model turn: declares `tasks`, resolves `resolve`, then talks and stops."""

    async def fake_run(*a, **k):
        yield {"type": "tool_use_start", "id": "t1", "name": "list_accounts"}
        if tasks:
            run_plan.add_tasks(tasks)
        for task_id, status in (resolve or {}).items():
            run_plan.update_task(task_id, status)
        yield {"type": "tool_done", "id": "t1"}
        yield {"type": "text_delta", "text": "Now let me fetch new emails."}
        yield {"type": "turn_done", "usage": {"model": "model-a"}}
        assistant.SESSIONS[sid].append({"role": "assistant", "content": "ok"})

    return fake_run


async def _run(monkeypatch, sid, fake):
    assistant.SESSIONS[sid] = [{"role": "user", "content": "go"}]
    monkeypatch.setattr(assistant, "run", fake)
    return await automation_runner(_req(sid))


THREE = [{"subject": "Fetch new mail"}, {"subject": "Log them"}, {"subject": "Notify"}]


async def test_a_run_that_leaves_tasks_unfinished_is_not_a_success(quiet, monkeypatch):
    sid = "automation-run-early-stop"
    result = await _run(monkeypatch, sid, _turn(sid, tasks=THREE, resolve={"1": "completed"}))

    assert result.status == "failed"


async def test_the_error_names_what_it_stopped_on(quiet, monkeypatch):
    """So the run row answers "where did it die" without opening the transcript."""
    sid = "automation-run-names-it"
    result = await _run(monkeypatch, sid, _turn(sid, tasks=THREE, resolve={"1": "completed"}))

    assert "2 of 3 tasks unfinished" in result.error
    assert "Log them" in result.error


async def test_a_task_left_in_progress_is_recorded_failed_not_quietly_completed(quiet, monkeypatch):
    """Completing it would erase the only evidence of where the run stopped."""
    sid = "automation-run-inflight"
    result = await _run(
        monkeypatch, sid, _turn(sid, tasks=THREE, resolve={"1": "completed", "2": "in_progress"})
    )

    assert [t["status"] for t in result.plan] == ["completed", "failed", "pending"]


async def test_a_run_that_resolved_every_task_completes(quiet, monkeypatch):
    sid = "automation-run-all-done"
    result = await _run(
        monkeypatch,
        sid,
        _turn(sid, tasks=THREE, resolve={"1": "completed", "2": "completed", "3": "completed"}),
    )

    assert result.status == "completed"


async def test_a_task_the_run_deliberately_marked_failed_does_not_block_completion(
    quiet, monkeypatch
):
    """A run that TRIED, failed one step and said so has still accounted for its work — the
    rule is "resolved", not "succeeded"."""
    sid = "automation-run-partial"
    result = await _run(
        monkeypatch,
        sid,
        _turn(sid, tasks=THREE, resolve={"1": "completed", "2": "failed", "3": "completed"}),
    )

    assert result.status == "completed"


async def test_a_run_that_declared_no_tasks_is_unchanged(quiet, monkeypatch):
    """Older Automations, and any run whose model ignores the task tool, must keep working.
    Nothing was declared, so there is nothing to hold it to — and a silent run stays green."""
    sid = "automation-run-noop"
    result = await _run(monkeypatch, sid, _turn(sid))

    assert result.status == "completed"


@pytest.mark.asyncio
async def test_a_run_whose_every_task_failed_is_not_completed(quiet, monkeypatch):
    """Live 2026-08-10: the tool offer refused every call, all five tasks were marked failed,
    and the run still reported `completed`.

    A FAILED task counted as "resolved", and only UNRESOLVED tasks blocked the green path.
    That reaches past the label: `committed=status == "completed"` promotes the staged email
    cursor, so a run that logged nothing could still advance past mail it never processed.
    Three emails survived that run only because its fetch happened to be cursor-neutral.

    One deliberate failure is still a completed run (the test above pins that). The rule
    added here is narrower: nothing succeeding is not a success.
    """
    sid = "automation-run-all-failed"
    result = await _run(
        monkeypatch,
        sid,
        _turn(sid, tasks=THREE, resolve={"1": "failed", "2": "failed", "3": "failed"}),
    )

    assert result.status == "failed"
    assert "3 of 3 tasks failed" in (result.error or "")
