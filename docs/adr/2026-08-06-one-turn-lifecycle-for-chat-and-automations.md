# One turn lifecycle for chat and Automations

- **Date**: 2026-08-06
- **Status**: Accepted
- **Area**: Turn execution (`backend/ai/turns.py`, `backend/ai/automation_runner_adapter.py`,
  `backend/services/automations/runs.py`, `backend/api/chat.py`)

## Decision

**There is ONE turn lifecycle. `turns._drive` brackets every turn, whoever triggered it.**

It has two entry points, differing only in who owns the task:

- `turns.spawn(...)` — creates a task and returns immediately, so `POST /api/chat` can answer
  `202`.
- `turns.run_inline(...)` — runs the same `_drive` body in the **caller's** task, for an
  Automation run whose task is already owned by `services/automations/runs.py`.

Both register in the same registry, so `turns.executing_turn_id` and `turns.cancel` see every
turn in the process regardless of what started it.

Consequently:

1. **An Automation run can be stopped.** `POST /api/chat/{id}/stop` no longer refuses an
   Automation-owned session; it cancels uniformly, and `runs._task_done` transitions the run to
   the new terminal status `cancelled` and releases the runtime claim.
2. **Mid-turn injection works for Automations through the ordinary path.** The special-case
   branch added to `chat.py` earlier the same day is DELETED.
3. **Bracketing, cost surface and session-meta stamping live in `_drive` only.** The adapter's
   copies are removed.

What stays Automation-specific, deliberately: the trigger, the durable run row, the runtime
claim ("one run per Automation at a time"), and the instructions coming from the Automation
definition rather than a composer.

## Why

An Automation run and a chat turn were always the same thing with different triggers — both drive
`assistant.run()`, stream through `live_sessions`, and hit the same tool loop, approval gate and
injection drain points. They were nonetheless executed by two independent lifecycles: task
creation, task tracking, `live_sessions` bracketing, cost surface and meta stamping each existed
twice, and cancellation existed once.

The cost of that was not theoretical:

- **You could not stop an Automation run anywhere in the product.** `/stop` returned 409 with a
  comment implying the Automations tab owned this; no endpoint, service function or control
  existed there. A scheduled run that looped or burned money could not be interrupted at all.
- **Mid-turn injection silently didn't apply to runs**, because `chat.py` routes on
  `turns.executing_turn_id` and a run was never registered. A message typed mid-run became a
  followup that queued *behind* the run instead of being read *during* it.
- **The fail-open confirm-gate fix had to be written twice.** `set_cost_surface` must be the first
  statement inside a turn's task or `can_render_card(None)` returns False and the gate fails OPEN
  — ~162 mutating tools plus `send_email` executing with no approval card
  (`docs/review/concurrent-conversations-security.md` finding 1). With two lifecycles that fix
  lives in two places, and the next one like it will too.

That last point is the real argument. Duplicated lifecycle means a security fix applied to one
copy leaves the other silently broken.

### Why `run_inline` rather than adopting the task

The cheaper option — `turns.adopt(session_id, task)`, registering the run's existing task without
routing it through `_drive` — would have delivered Stop and injection. It was rejected because it
leaves both lifecycles intact: the adapter would still do its own bracketing and stamping, so the
duplication (and the two-places-to-fix problem) survives. It buys the symptoms and keeps the cause.

### Why not simply call `spawn` from the runner

`services/` must never import `ai/` — a CI-blocking import-linter contract, because the business
core must be usable with no AI at all. `turns.py` is in `ai/`; `runs.py` is in `services/`. That
rule is *why* the two lifecycles drifted apart, and it rules out the obvious fix.

The design works with it: `run_inline` is called from `automation_runner_adapter`, which is
already in `ai/`. `runs.py` keeps owning its task and its run row and never learns `turns` exists.

### Why `cancelled` rather than `failed`

Run statuses were a closed set of four. Recording a user's deliberate Stop as `failed` would be a
lie — nothing failed. Chat already uses `cancelled` for `last_turn_status`, noting it is "the
user's own explicit Stop — nothing went wrong". The UI labels it **"Stopped"**: what the user did,
not what happened to it.

## Consequences

- A new turn initiator must go through `turns.spawn` or `turns.run_inline` — never a bare
  `asyncio.create_task` around `assistant.run`. That is the rule the confirm gate's correctness
  depends on (see CLAUDE.md's AI-harness invariants).
- `turns.cancel` now reaches Automation runs. Anything that assumed "cancel only affects
  interactive chat" is no longer true.
- A stopped run is terminal (`cancelled`) and releases its runtime claim, so the Automation can be
  run again immediately rather than being wedged by a claim nobody releases.
- A run stopped mid-tool-call has the same ambiguity as a stopped chat turn: the tool may have
  committed a write. `core.automation_runner.STOPPED_SUMMARY_NOTE` is appended to the run summary
  so it says the outcome is unknown and must be verified — not that nothing happened.
- `_drive` now stamps `error` when a turn emits an `error` event without raising. That was
  previously only computed in the adapter's copy; folding it into the one lifecycle makes
  interactive chat more accurate too, which is the point of having one.

### Decided while implementing

- **Stop is on the Automations tab as well as in chat.** The detail header's **Run now** becomes
  **Stop** while a run is in flight — the same one-button swap the composer makes, calling the same
  endpoint. A control that exists on only one of the two surfaces is how this went missing.
- **A stopped run does not consume a budgeted run.** `_finish_automation_once(count_run=False)`
  leaves `run_count` alone but still recomputes `next_run_at`, which has no other owner — skipping
  the call entirely would strand a scheduled Automation.
- **A stopped follow-up settles `cancelled` too.** A follow-up turn on an Automation conversation
  runs via `spawn` and its cancellation used to fall through the generic error path, recording
  `failed`. One user action must not read as two statuses depending on which turn it landed on.

## Agent provenance

Decided by a Claude agent (orchestrator, branch `feature/concurrent-conversations`) after the
owner asked "any remaining discrepancies, and the reason why we should have those discrepancies" —
then, on seeing the list, "we shouldn't duplicate anything; the automation is probably only
different from triggers".

That framing is what produced this ADR. The agent's own preceding move had been to add a
special-case injection branch for Automations — a fourth copy of turn-routing logic — and the
owner's instruction correctly rejected it. The branch is deleted by this decision.

Recorded because the tempting fix here is always the local one: bolt cancellation onto the
automation runner, and the duplication survives one more round. The thing that made it visible was
counting how many places `set_cost_surface` had to be correct in for the confirm gate not to fail
open.
