"""The task list an Automation run keeps for itself while it works.

Progress used to be the list of tools that had fired: "Find tools", "List accounts",
"Fetch emails". That is an audit trail of mechanics, not a report — it cannot say anything
about a step until after the step happens, so at the start of a run there is nothing to
show, and at the end it reads like a stack trace rather than "here is what I did".

So the run keeps a real task list instead: it ADDS the tasks it intends to do, each with a
subject in its own words, then marks one started and completed as it goes. Granular
operations rather than re-sending the whole list, because that is how the run actually
thinks — one task begins, one task ends — and because each call then carries only the id
and the new status instead of restating every subject every time.

Ids are small sequential strings ("1", "2", …) scoped to the run, so the model can refer to
a task in one short argument and the reader sees a stable number beside each row.

A ContextVar holding a MUTABLE BOX, and that detail is load-bearing. Tool calls are
dispatched as asyncio Tasks, and a Task runs in a COPY of the context — so a tool that
REBINDS a ContextVar rebinds only its own copy, and the adapter reading from the parent
context sees nothing. Live, that showed as a run whose plan tool fired four times while
the card still listed the tool names. Rebinding once up front and MUTATING the shared box
afterwards is visible everywhere, because every copy points at the same object.
"""

from __future__ import annotations

from contextvars import ContextVar, Token
from typing import Optional

# Cap the list. It is a summary for a human to read at a glance; a forty-row list is a
# transcript by another name and would push the run card's progress section past the
# height of the panel it lives in.
MAX_TASKS = 12
MAX_SUBJECT_CHARS = 120
MAX_DESCRIPTION_CHARS = 400

VALID_STATUS = ("pending", "in_progress", "completed", "failed")

_plan: ContextVar[Optional[dict]] = ContextVar("automation_run_tasks", default=None)


def begin() -> Token:
    """Open a task-list scope for one run. Returns the token to reset it with."""
    return _plan.set({"tasks": [], "seeded": False, "restated": False})


def reset(token: Token) -> None:
    _plan.reset(token)


def _box() -> Optional[dict]:
    return _plan.get()


def get_tasks() -> list[dict]:
    """This run's task list, oldest first. Empty when the run never added any."""
    box = _box()
    return [dict(task) for task in box["tasks"]] if box is not None else []


def add_tasks(entries: list[dict]) -> list[dict]:
    """Append tasks and return the ones that were added, each with its new id.

    Appends rather than replaces: a run that thinks of another step halfway through says so
    without having to restate the steps it already finished.

    A subject already on the list is SKIPPED, not appended again. Append-not-replace is what
    lets a run add a step mid-flight, but it also means a run that re-states its whole plan —
    which happens on a continuation, and whenever the model simply repeats itself — doubles
    the checklist the owner is watching (observed live: 12 tasks that were the same 6 twice).
    Dedupe by subject keeps the intended behaviour and removes the failure mode: genuinely new
    steps still land, restatements are no-ops. Matching is case-insensitive on the trimmed
    subject, so "Fetch emails" and "fetch emails " are one task.

    Exact matching alone was NOT enough on a continuation, because the model does not restate
    verbatim — it paraphrases. Live 2026-08-14 (run_6be9d3e2750d): the resumed run was seeded
    with five tasks and re-declared the same five in fresh words ("Fetch mail from the last
    day across bank/card/wallet/exchange senders" → "Fetch mail from the last 1 day across
    configured senders (banks, cards, wallets, exchanges)"), so nothing matched and the card
    grew to ten. The model then worked ITS five to completion and never touched the seeded
    five, which is how one run reported "2 of 10 unfinished" while its own summary read "All
    10 tasks are now marked complete" — both true, about different lists.

    ``claim_restatement`` is what closes that, structurally rather than by guessing at
    similarity: see its docstring for why the FIRST call on a seeded plan is definitionally
    a restatement.
    """
    box = _box()
    if box is None:
        return []
    if claim_restatement():
        return []
    seen = {str(t.get("label") or "").strip().casefold() for t in box["tasks"]}
    added: list[dict] = []
    for raw in entries:
        if len(box["tasks"]) >= MAX_TASKS:
            break
        subject = str((raw or {}).get("subject") or "").strip()[:MAX_SUBJECT_CHARS]
        if not subject:
            continue
        if subject.casefold() in seen:
            continue
        seen.add(subject.casefold())
        description = str((raw or {}).get("description") or "").strip()[:MAX_DESCRIPTION_CHARS]
        task = {
            "id": str(len(box["tasks"]) + 1),
            "label": subject,
            "description": description or None,
            "status": "pending",
            "done": False,
        }
        box["tasks"].append(task)
        added.append(dict(task))
    return added


def update_task(task_id: str, status: str) -> Optional[dict]:
    """Set one task's status. Returns the updated task, or None if the id is unknown."""
    box = _box()
    if box is None:
        return None
    if status not in VALID_STATUS:
        return None
    for task in box["tasks"]:
        if task["id"] == str(task_id).strip():
            task["status"] = status
            task["done"] = status == "completed"
            return dict(task)
    return None


def claim_restatement() -> bool:
    """True exactly once, for a resumed run's FIRST attempt to declare a plan it already has.

    A continuation is not a new run, but it reads like one to the model. The instruction
    "before your first other tool call, call add_run_tasks with the steps you intend to take"
    lives in the run's OPENING message — deliberately, so the date survives context folding —
    and a continuation replays that same transcript. So the model re-reads a start-of-run
    instruction on resume and complies, every time. The fork is the prompt working as
    written, not the model misbehaving.

    Hence the rule is positional, not textual: on a plan that was SEEDED from a previous
    attempt, the first ``add_tasks`` is the restatement. No similarity threshold to tune, and
    no way for a reworded subject to slip past. Later calls in the same turn append normally,
    which is what preserves the whole point of append-not-replace — a run that thinks of an
    extra step halfway through can still say so.
    """
    box = _box()
    if box is None or not box.get("seeded") or box.get("restated"):
        return False
    box["restated"] = True
    return bool(box["tasks"])


def seed(tasks: list[dict]) -> None:
    """Restore a run's task list — used when a continuation resumes a parked run."""
    box = _box()
    if box is None:
        return
    restored: list[dict] = []
    for raw in tasks[:MAX_TASKS]:
        if not isinstance(raw, dict):
            continue
        subject = str(raw.get("label") or raw.get("subject") or "").strip()
        if not subject:
            continue
        status = str(raw.get("status") or "pending")
        if status not in VALID_STATUS:
            status = "pending"
        restored.append(
            {
                "id": str(raw.get("id") or len(restored) + 1),
                "label": subject[:MAX_SUBJECT_CHARS],
                "description": raw.get("description") or None,
                "status": status,
                "done": status == "completed",
            }
        )
    box["tasks"] = restored
    # Only a non-empty restore marks the run as resumed: seeding nothing leaves a
    # continuation free to declare its plan for the first time, exactly like a fresh run.
    box["seeded"] = bool(restored)
