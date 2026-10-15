# A turn outlives the request that asked for it

- **Date**: 2026-08-06
- **Status**: Accepted
- **Area**: Server-owned turn lifetime (`backend/ai/turns.py`, `backend/ai/live_sessions.py`,
  `backend/api/chat.py`, `backend/ai/assistant.py`, `frontend/src/hooks/useChat.ts`)

## Decision

`POST /api/chat` and `POST /api/chat/{id}/resolve` stop driving turns inside the HTTP request.
Each validates, then calls `turns.spawn(...)` — a **synchronous** function with no `await`
anywhere in it — which opens the `live_sessions` episode, registers a supervised background
`asyncio.Task`, and returns a handle immediately. The route answers `202 {session_id, turn_id,
accepted}`. The client attaches to `GET /api/sessions/{id}/live` — the same endpoint an
Automation run has always used. Interactive, resolve-continuation, and Automation-followup turns
become one lane (`backend/ai/turns.py`) with three different initiators.

`spawn()` being synchronous is the entire race-freedom argument: the live episode is open and the
task is registered **before** the 202 response is even constructed, so a client that calls
`GET /live` the instant it reads the 202 can never find the session not-live. No grace period,
no retry loop — removed by construction. (A 30s post-*end* replay-retention grace window in
`live_sessions` is a separate, smaller thing: it protects a client that attaches moments *after*
a short turn already finished, not the spawn race.)

## Why

The owner's ask (`.claude/rfcs/concurrent-conversations.md` Phase 3) was that a turn survive tab
close, mobile backgrounding, and navigation. Phase 2 already gets most of the way there —
`live_sessions` fans out every interactive turn's events, so a second watcher can already attach
mid-turn — but the turn itself was still driven by `event_stream()`, an async generator living
inside the request coroutine (`api/chat.py`). Closing the tab cancels that coroutine, and the turn
dies wherever it was.

## The blocking defect this decision had to close first

`event_stream()` called `set_cost_surface("web")` **inside itself**, at the top of the generator.
`contextvars` are copied at `asyncio.create_task` time — so a task spawned from the route
handler's own context starts with `cost_surface` still `None`, and
`ai.tools._confirm_gate.should_gate` **fails open** on an unrecognised surface by design
(`_confirm_gate.py`: *"a gate that cannot determine its context must never silently stop a
working write path"*). With `GATE_ALL_WRITES = True` that would mean every mutating tool (~162),
plus `send_email` (the only irreversible effect in the codebase), plus `restore_checkpoint`,
executing with **no approval card** the moment this RFC shipped — and it would look fine in
tests, because a cost row with no surface falls back to a legacy `"chat"` attribution rather than
erroring.

Fix: `backend/ai/turns._drive` calls `set_cost_surface(cost_surface)` as its literal first
statement — before the first `await` — so nothing the task does can observe an unset surface.
`clear_cost_surface()` runs in the task's `finally`. `tests/test_turn_supervisor.py`'s
`test_a_gated_write_still_stages_a_card_through_the_detached_path` is the regression test, and it
was run against the bug to confirm it fails without the fix (temporarily commenting out the
`set_cost_surface` call reproduces the failure: the gated tool executes with no
`PendingPlanStore` entry). The test asserts on the gate — a `proposed_change` event and a pending
entry — never on the cost log, which is exactly how this class of bug hides.

**Considered and rejected: making `can_render_card` fail closed on an unrecognised surface.**
That is a broader, cross-cutting policy change affecting every legitimately headless caller
(`services/self_harness`, `ai/capture.py`, inbound MCP) that runs with no surface at all today and
is gated by the *separate* unattended-hardline path instead — auditing every one of those call
sites was out of scope for closing this specific, verified bug. Closing it does not need that:
every path that reaches `_drive` goes through `spawn()`, whose `cost_surface` parameter is a
plain `str` with a safe default (`"web"`) enforced by the type checker — there is no way to reach
a detached turn with an unset surface short of a future caller explicitly passing
`cost_surface=None`, which mypy already refuses. Defense in depth stops at "the supervisor is the
sole writer of cost_surface for a detached turn," not at "redesign the fail-open policy."

## What else this decision carries

- **`live_sessions.begin/end` becomes episode-refcounted with a post-end replay grace** (already
  partially built in Phase 2 as a bare turn-id set; this change adds the 30s retention window).
  `is_live()`/`live_session_ids()` are keyed off the *open-episode* set, never off replay-buffer
  presence — a grace-retained buffer must never make a finished turn LOOK live, or the sidebar
  lies and a POST during the window would wrongly route into mid-turn injection instead of
  starting a fresh turn.
- **`assistant.run()` / `resolve_pending_tool_use()` accept an optional `turn_id`.** Phase 2 had
  already moved turn-id minting and stamping into `assistant.run` itself. `spawn()` mints the id
  *before* the task exists (needed for the race-freedom argument above), so the id must be
  threaded down rather than let a second, disagreeing id get minted deeper — two id namespaces for
  one turn would silently break replay/watch matching. Every in-process caller that omits the
  parameter (connectors, capture, headless_turn, the Automation runner) is unaffected.
- **The dangling-tool-use stub text now depends on WHY the turn ended.** A cancellation
  (`asyncio.CancelledError` — a user Stop, or the new wall-clock timeout) can land at an `await`
  *after* a tool's own synchronous store write already committed, so `_run_turn` now distinguishes
  it from a genuine crash: `"This tool call was interrupted before its result was recorded... "`
  rather than the old, false-in-this-case `"Tool call did not complete."` Money-correctness, not
  copy — see `_fix_dangling_tool_uses`'s `reason` parameter and
  `tests/test_concurrent_turn_ownership.py`.
- **No idle/no-event watchdog.** A wall-clock deadline (`ONW_TURN_TIMEOUT_S`, default 1200s)
  bounds a *stuck* turn; an idle-silence timer was rejected because a legitimately long tool call
  (a big statement import, a slow `fetch_page`) emits nothing for minutes and would be killed by
  one. There is still no spend cap (owner decision, carried over from the parent RFC) — the
  deadline bounds time, not cost.
- **Boot recovery, modelled on `recover_automation_runs`.** `last_turn_status == "running"` in a
  fresh process is definitionally stale (the registry is empty at boot; only a crash leaves that
  status behind, since a clean `shutdown()` rewrites every in-flight turn to `"cancelled"`
  first) — rewritten to `"interrupted"`. No new store: the existing `last_turn_status` field
  (already shipped in Phase 2) gains three more values (`running`, `interrupted`, `timeout`)
  rather than being replaced by a new nested structure, so nothing that already reads it
  (`frontend/src/hooks/useConversations.ts`'s narrow `'ok' | 'error'` cast) needs to change —
  a value it doesn't recognise is already, correctly, treated as absent.

## Alternatives considered

**Keep `POST /api/chat` streaming; make client disconnect non-fatal.** Drive the turn in a task
but keep the request attached as the first watcher, still returning `text/event-stream`. Rejected:
the response would be a *second* fan-out path with different lifetime rules than
`GET /{id}/live` — the exact two-lane split the parent RFC exists to delete. Every
re-attach-after-drop still needs the live endpoint regardless, so the client needs the attach path
either way. This also gives up something worth naming: today a slow client back-pressures the turn
through TCP; after this change it cannot (a slow watcher's events are dropped, per
`live_sessions.publish`'s existing contract) and the transcript reconcile is the compensating
mechanism. Deliberate trade: turn durability beats guaranteed delivery to one socket.

**A `core/`-seam task supervisor**, mirroring `core.automation_runner`. Rejected — those seams
exist so a *lower* layer can trigger an *upper* one. The caller here (`api/chat.py`) is already
*above* `ai`, so a direct import is legal under the dependency rule (`api -> ai -> services -> db
-> core`) and a seam would be ceremony with an indirection cost.

**A transitional dual-shape client** (accept both the old SSE stream and the new 202, deleted at
the end) was specified in the design doc to make a multi-step, independently-deployed rollout
safe. Not built: this change lands backend and the two `useChat.ts` call sites together in one
unit, so there is no intermediate deploy boundary to protect, and the scaffolding would be pure
dead weight, written only to be deleted in the same change.

## Consequences

- `backend/ai/turns.py` is the one place a turn's background lifetime is managed. A future turn
  initiator (a new endpoint, a connector wanting durability) calls `spawn()`, not
  `asyncio.create_task` directly — bypassing it silently loses the cost-surface guarantee above.
- `POST /api/chat` / `POST /api/chat/{id}/resolve` no longer stream. Any external caller not in
  this repo (a script, a bookmarklet) that parsed SSE off the POST body breaks; a repo-wide search
  found none, and the API is not externally documented.
- `frontend/src/hooks/useChat.ts`'s attach effect is now a pure function of slice state
  (`streaming || liveSessions.includes(id)`), not of who posted — one attach per slice regardless
  of whether the turn was started locally or elsewhere.
- `live_sessions.register_task` / `unregister_task` / `cancel` (Phase 2's own cancellation
  mechanism, keyed off the request-handling task) are removed as dead code — `turns.py` now holds
  the task and owns cancellation via `turns.cancel(session_id, turn_id=None)`.
- **`ConvSlice.abort` (the per-conversation `AbortController` Phase 1 introduced) is retired in
  practice, though the field itself is left declared** (harmless, always `null` now — removing it
  is a `chatSessions.ts` type change out of this lane's remit). Two UI-lane call sites that
  merged in AFTER this lane's own STEP-0 turned out to depend on it directly —
  `luciusChat.tsx`'s `stopConversation` (sidebar-row quick-Stop) and `ChatPanel.tsx`'s
  `handleDeleteConversation` (acceptance B6) both called `.abort?.abort()` on it. Both were
  fixed, in this same change, to `POST /api/chat/{id}/stop` — found only because the integration
  branch was re-merged a second time before this lane closed; a `grep -rn "\.abort\b"` across
  `frontend/src` is the check to repeat if this ADR is read after a further UI-lane merge.

## Agent provenance

Decided by a Claude agent implementing `.claude/rfcs/concurrent-conversations-phase3.md`
end to end. The design doc's own uncertainty #2 ("two id namespaces for one turn would be a
genuine bug") turned out to be live, not hypothetical: Phase 2, merged ahead of this change, had
already moved turn-id minting into `assistant.run()` itself — the design doc's `spawn()` sketch
(`make_events: Callable[[], AsyncIterable[dict]]`, no argument) would have minted a second,
disagreeing id. Fixed by threading the supervisor's id down (`make_events: Callable[[str], ...]`),
confirmed with a regression test (`test_events_from_the_endpoint_keep_their_session_id_and_turn_id`)
that fails if `assistant.run` doesn't receive the passed-in `turn_id`.

The `can_render_card` fail-open question was the one place this agent deliberately did *not* take
the design doc's "consider" as an instruction to build something — the doc explicitly left it as a
judgement call ("say what you chose and why"), and the call made was to fix the verified bug at
its narrowest correct point rather than take on an unaudited, cross-cutting policy change.
