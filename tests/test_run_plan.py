"""An Automation run keeps a task list; the run card shows that, not a tool log.

Progress used to be the list of tools that had fired — "Find tools", "List accounts" —
which says nothing until after each step happens and reads like a stack trace at the end.
The run now ADDS the tasks it intends to do and marks them started and finished as it
goes, so the card shows intent up front and position throughout.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from backend.ai import run_plan
from backend.ai.tools.automations import add_run_tasks, update_run_task


@pytest.fixture(autouse=True)
def _scope():
    token = run_plan.begin()
    yield
    run_plan.reset(token)


async def _add(*subjects):
    raw, _ = await add_run_tasks({"tasks": [{"subject": s} for s in subjects]})
    return json.loads(raw)


async def _update(task_id, status):
    raw, _ = await update_run_task({"task_id": task_id, "status": status})
    return json.loads(raw)


# ── Adding ────────────────────────────────────────────────────────────────────


async def test_added_tasks_get_sequential_ids_and_start_pending():
    result = await _add("Read today's mail", "Send the summary")

    assert [t["id"] for t in result["added"]] == ["1", "2"]
    assert [t["status"] for t in run_plan.get_tasks()] == ["pending", "pending"]


async def test_a_later_call_APPENDS_rather_than_replacing():
    """A run that thinks of another step halfway through must not restate the finished ones."""
    await _add("Read today's mail")
    await _update("1", "completed")
    await _add("Chase the one that failed")

    tasks = run_plan.get_tasks()
    assert [t["label"] for t in tasks] == ["Read today's mail", "Chase the one that failed"]
    assert [t["status"] for t in tasks] == ["completed", "pending"]


async def test_a_description_is_kept_when_the_subject_leaves_something_open():
    raw, _ = await add_run_tasks(
        {"tasks": [{"subject": "Gather mail", "description": "Today 00:00 onward, all boxes"}]}
    )
    assert json.loads(raw)["ok"] is True
    assert run_plan.get_tasks()[0]["description"] == "Today 00:00 onward, all boxes"


async def test_a_blank_subject_is_dropped():
    await _add("Real", "   ")

    assert len(run_plan.get_tasks()) == 1


async def test_a_call_with_only_blanks_is_refused_rather_than_reporting_success():
    result = await add_run_tasks({"tasks": [{"subject": "  "}]})

    assert json.loads(result[0])["ok"] is False


async def test_an_empty_list_is_refused():
    assert json.loads((await add_run_tasks({"tasks": []}))[0])["ok"] is False


async def test_the_list_is_capped():
    await _add(*[f"Task {i}" for i in range(run_plan.MAX_TASKS + 5)])

    assert len(run_plan.get_tasks()) == run_plan.MAX_TASKS


async def test_a_long_subject_is_trimmed_not_rejected():
    await _add("x" * 500)

    assert len(run_plan.get_tasks()[0]["label"]) == run_plan.MAX_SUBJECT_CHARS


# ── Updating ──────────────────────────────────────────────────────────────────


async def test_a_task_can_be_started_then_completed():
    await _add("Read today's mail")
    await _update("1", "in_progress")
    assert run_plan.get_tasks()[0]["status"] == "in_progress"

    await _update("1", "completed")
    assert run_plan.get_tasks()[0] == {
        "id": "1",
        "label": "Read today's mail",
        "description": None,
        "status": "completed",
        "done": True,
    }


async def test_only_the_named_task_moves():
    await _add("One", "Two")
    await _update("2", "completed")

    assert [t["done"] for t in run_plan.get_tasks()] == [False, True]


async def test_an_unknown_id_is_refused_AND_names_the_ids_that_do_exist():
    """The model has to be able to fix its own mistake without guessing."""
    await _add("One")
    result = await _update("7", "completed")

    assert result["ok"] is False
    assert "'1'" in result["error"]


async def test_an_unusable_status_is_refused_and_lists_the_valid_ones():
    await _add("One")
    result = await _update("1", "nearly")

    assert result["ok"] is False
    assert "completed" in result["error"]
    assert run_plan.get_tasks()[0]["status"] == "pending"


# ── Scope ─────────────────────────────────────────────────────────────────────


async def test_a_task_added_inside_a_Task_is_visible_to_the_run():
    """The scar. Tool calls dispatch as asyncio Tasks, and a Task runs in a COPY of the
    context — a tool that REBINDS a ContextVar rebinds only its own copy. Live, that showed
    as a run whose plan tool fired four times while the card still listed the tool names."""

    async def as_a_tool():
        await _add("Read today's mail")

    await asyncio.ensure_future(as_a_tool())

    assert [t["label"] for t in run_plan.get_tasks()] == ["Read today's mail"]


async def test_adding_with_no_run_scope_open_does_not_leak_into_the_next_run():
    token = run_plan._plan.set(None)
    try:
        assert (
            json.loads((await add_run_tasks({"tasks": [{"subject": "Stray"}]}))[0])["ok"] is False
        )
        assert run_plan.get_tasks() == []
    finally:
        run_plan._plan.reset(token)


# ── The adapter's projection ──────────────────────────────────────────────────


def test_the_task_list_is_reported_separately_from_the_tool_trail():
    """``plan`` and ``steps`` answer different questions and both are kept: the task list is
    what the run set out to do, the trail is which tools actually fired."""
    from backend.ai.automation_runner_adapter import _declared_plan

    token = run_plan.begin()
    try:
        run_plan.add_tasks([{"subject": "Read today's mail"}])
        assert [t["label"] for t in _declared_plan()] == ["Read today's mail"]
    finally:
        run_plan.reset(token)


def test_a_run_that_added_no_tasks_reports_an_empty_list():
    from backend.ai.automation_runner_adapter import _declared_plan

    token = run_plan.begin()
    try:
        assert _declared_plan() == []
    finally:
        run_plan.reset(token)


def test_a_continuation_picks_the_same_task_list_back_up():
    """Resuming after an approval card must not erase the run's history: the list lives in
    a ContextVar that died with the original turn, so it is re-seeded from the run row."""
    from backend.ai.automation_runner_adapter import _declared_plan, _seed_plan_from

    token = run_plan.begin()
    try:
        _seed_plan_from(
            {
                "plan": [
                    {"id": "1", "label": "Read today's mail", "status": "completed"},
                    {"id": "2", "label": "Send the summary", "status": "in_progress"},
                ]
            }
        )
        tasks = _declared_plan()
        assert [t["label"] for t in tasks] == ["Read today's mail", "Send the summary"]
        assert [t["done"] for t in tasks] == [True, False]
    finally:
        run_plan.reset(token)


def test_a_seeded_list_keeps_its_ids_so_the_continuation_can_still_address_it():
    from backend.ai.automation_runner_adapter import _seed_plan_from

    token = run_plan.begin()
    try:
        _seed_plan_from({"plan": [{"id": "1", "label": "One"}, {"id": "2", "label": "Two"}]})
        assert run_plan.update_task("2", "completed")["label"] == "Two"
    finally:
        run_plan.reset(token)


def test_a_run_row_with_no_task_list_seeds_nothing():
    from backend.ai.automation_runner_adapter import _declared_plan, _seed_plan_from

    token = run_plan.begin()
    try:
        _seed_plan_from({"steps": [{"label": "List accounts", "status": "completed"}]})
        assert _declared_plan() == []
    finally:
        run_plan.reset(token)


def test_a_task_left_in_progress_is_settled_when_the_run_ends():
    from backend.ai.automation_runner_adapter import _mark_running_steps

    settled = _mark_running_steps([{"label": "One", "status": "in_progress"}], "failed")

    assert settled[0]["status"] == "failed"


def test_the_runner_snapshots_the_task_list_before_closing_its_scope():
    """The scar. Both collectors are ContextVar-scoped and every `return` in
    ``automation_runner`` sits AFTER the try/finally that resets them — reading them there
    got a closed scope, so a run whose plan tool fired twice still persisted `plan: []`.
    Pinned by source, because the ordering is the whole bug and a unit call cannot see it."""
    import inspect

    from backend.ai import automation_runner_adapter as adapter

    src = inspect.getsource(adapter.automation_runner)
    snapshot = src.index("declared_plan = _declared_plan()")
    release = src.index("run_plan.reset(plan_token)")
    first_return = src.index("return AutomationRunnerResult(")

    assert snapshot < release < first_return


def test_restating_the_plan_does_not_duplicate_the_checklist():
    """A run that re-states its plan must not double the list the owner is watching.

    add_tasks appends rather than replaces on purpose — a run that thinks of another step
    halfway through says so without restating what it already finished. But that also meant a
    continuation, or a model simply repeating itself, appended the whole plan a second time.
    Observed live: 12 tasks that were the same 6 twice.
    """
    token = run_plan.begin()
    try:
        first = run_plan.add_tasks(
            [{"subject": "Fetch emails"}, {"subject": "Book trades"}, {"subject": "Report"}]
        )
        assert len(first) == 3

        again = run_plan.add_tasks(
            [{"subject": "Fetch emails"}, {"subject": "Book trades"}, {"subject": "Report"}]
        )
        assert again == [], "restating the plan appended it a second time"
        assert len(run_plan.get_tasks()) == 3
    finally:
        run_plan.reset(token)


def test_a_genuinely_new_step_still_appends_mid_run():
    """The behaviour dedupe must NOT break: adding a step discovered halfway through."""
    token = run_plan.begin()
    try:
        run_plan.add_tasks([{"subject": "Fetch emails"}])
        added = run_plan.add_tasks([{"subject": "Fetch emails"}, {"subject": "Reconcile totals"}])
        assert [t["label"] for t in added] == ["Reconcile totals"]
        assert [t["label"] for t in run_plan.get_tasks()] == ["Fetch emails", "Reconcile totals"]
    finally:
        run_plan.reset(token)


def test_dedupe_ignores_case_and_surrounding_space():
    token = run_plan.begin()
    try:
        run_plan.add_tasks([{"subject": "Fetch emails"}])
        assert run_plan.add_tasks([{"subject": "  fetch EMAILS "}]) == []
        assert len(run_plan.get_tasks()) == 1
    finally:
        run_plan.reset(token)


# ── a continuation must not fork the plan ─────────────────────────────────────────


def test_a_resumed_run_re_declaring_its_plan_adds_nothing():
    """Live 2026-08-14 (run_6be9d3e2750d). The run was seeded with five tasks, re-declared
    the same five in DIFFERENT WORDS, and the card grew to ten — exact-subject dedupe cannot
    see a paraphrase. The model then completed its own five and left the seeded five open, so
    the run reported "2 of 10 unfinished" while its summary read "All 10 tasks are now marked
    complete". Both were true, about different lists."""
    token = run_plan.begin()
    try:
        run_plan.seed(
            [
                {"id": "1", "label": "Fetch mail from the last day across bank/card senders"},
                {"id": "2", "label": "Read current account list and category list"},
            ]
        )
        added = run_plan.add_tasks(
            [
                {"subject": "Fetch mail from the last 1 day across configured senders"},
                {"subject": "Read current account list and category list fresh"},
            ]
        )
        assert added == []
        assert len(run_plan.get_tasks()) == 2, "the seeded list must stay the only list"
    finally:
        run_plan.reset(token)


def test_a_resumed_run_can_still_add_a_genuinely_new_step():
    """Append-not-replace exists so a run that thinks of another step mid-flight can say so.
    Only the FIRST call is treated as the restatement; later ones append as always."""
    token = run_plan.begin()
    try:
        run_plan.seed([{"id": "1", "label": "Fetch mail"}])
        run_plan.add_tasks([{"subject": "Fetch mail restated"}])  # absorbed
        added = run_plan.add_tasks([{"subject": "Reconcile the duplicate charges"}])
        assert [t["label"] for t in added] == ["Reconcile the duplicate charges"]
        assert len(run_plan.get_tasks()) == 2
    finally:
        run_plan.reset(token)


def test_a_fresh_run_declares_its_plan_normally():
    """Nothing seeded means nothing to restate — the guard must never touch a first run."""
    token = run_plan.begin()
    try:
        added = run_plan.add_tasks([{"subject": "Fetch mail"}, {"subject": "Log it"}])
        assert len(added) == 2
    finally:
        run_plan.reset(token)


def test_seeding_an_empty_plan_leaves_the_run_free_to_declare():
    """A continuation of a run that never declared anything is, for this purpose, a fresh
    run — seeding nothing must not swallow its first real plan."""
    token = run_plan.begin()
    try:
        run_plan.seed([])
        assert len(run_plan.add_tasks([{"subject": "Fetch mail"}])) == 1
    finally:
        run_plan.reset(token)
