# Scheduled runs — reliability backlog

**What this is:** every known reliability defect in Automation runs, with the evidence that
found it. Written 2026-08-16 after a census of all 34 failed runs on this instance
(2026-08-08 → 2026-08-16). Nothing here is speculative — each item names the run, log line,
or code path it came from.

**Read this with:** [`docs/launch/BOARD.md`](launch/BOARD.md) (the live board — these items
are indexed there), [`backend/ai/automation_runner_adapter.py`](../backend/ai/automation_runner_adapter.py)
(the run lifecycle), [`backend/ai/turns.py`](../backend/ai/turns.py) (the turn lifecycle).

> **Status honesty.** Two items below (A1, A2) are **running in production right now and
> were never approved**. They shipped by accident: the dev server runs
> `uvicorn --reload --reload-dir backend` against the LIVE `~/.open-net-worth/` tree, so
> saving a backend file deploys it. That is itself item P2.

---

## The census

34 failed runs, four root causes. Counted by cross-referencing each run's `finished_at`
against `scheduler: stopped` lines in the logs, and each session's stored transcript.

| Cause | Runs | Share |
|---|---|---|
| Server restart killed the worker mid-run | 10 | 29% |
| Model stopped early / narrated work it never did | 13 | 38% |
| Provider or infra error | 9 | 26% |
| Turn time limit / a round that produced nothing | 2 | 6% |

Failure rate by model, same work, since 2026-08-10:

```
haiku            7 completed / 10 failed    59% fail
gpt-5.6-luna     1 completed /  3 failed    75% fail
sonnet          13 completed /  2 failed    13% fail
```

**323 server restarts in two days** (73/63/80/107 across the four log files) — roughly one
every nine minutes, against runs that take 2–8 minutes.

---

## The root cause underneath A1, A2, A3, B1, B2, B3

Owner, 2026-08-16: *"it looks like we haven't been able to properly implement loop
engineering / graph engineering in this case."* That is the correct framing, and it changes
what this backlog is — those six are not six defects, they are one missing abstraction
observed six times.

**The run is an implicit loop with exactly one edge.**

```python
while True:
    response = model(...)
    if response.has_tool_calls: run_tools(); continue   # the only edge
    else: end_turn()                                    # everything else falls here
```

The single decision the harness makes per round is "did the model emit a tool call?" There
is no verify edge, no continue edge, no retry edge, and no terminal state other than "the
model stopped talking." Every fix attempted so far has been another `if` bolted onto that one
`else` branch.

**And the run's state is split across three stores that can disagree.**

| State | Lives in | Survived a crash (before C4) |
|---|---|---|
| goals + progress (task list) | `run_plan` ContextVar → run **row** | yes |
| what actually happened | session **transcript** | **no** |
| verdict | run row `status` / `error` | yes |

That split is itself several items: B4 is the card and the model reading different stores;
C4 was one store being wiped while the other survived (`run_2c606ddf3dc7`: 24 tool calls and
5 of 6 tasks on the row, one message in the transcript).

**How each item falls out of it**

| Item | What it really is |
|---|---|
| A2 | the missing **outer** loop — there isn't one; the run *is* the turn |
| A1 | a hand-rolled **inner** loop; overlaps A2 because no boundary between "the model's turn" and "the run's job" is defined anywhere |
| B3 | a continuation replays the **entry node** instead of resuming into a state, so the model re-reads "declare your plan" and complies |
| B2 | attended-ness is a property of the run, but is inferred from whichever function opened the ContextVar; in a graph it is a construction parameter |
| B1 | messages carry two roles because that is the API's shape; the harness never modeled its own message **kinds**, so a correction injected by an edge is indistinguishable from something the owner typed |
| A3 | a graph with a real verify node tolerates a weaker model; part of "Haiku is bad" is having no control flow to compensate with |

**The shape that is missing**

```
plan → act → observe → VERIFY → { goals met       → finish
                                  unmet + progress → continue
                                  unmet + stalled  → fail
                                  cancelled        → stop (fail-closed) }
```

The verify node **already exists** — `automation_runner_adapter.py:390` computes `unfinished`
on every run. It runs *after* the loop has exited, where the only action available to it is
writing `failed`. Promoting it to a node with typed outgoing edges is most of A2, and it
**subsumes A1, B2 and B3** rather than sitting beside them.

This does not need a framework. It needs a run object that owns goals, progress, attempts,
budget and attended-ness, with `assistant.run` demoted to the act/observe node.

**Consequence for this backlog:** A1, B2 and B3 should not be fixed independently. Doing so
adds three more conditionals to the one `else` branch — the pattern that produced this list.

---

## A — Decisions blocked on the owner

### A1 — The completion nudge is live and unapproved
**Status:** running in production, uncommitted, superseded but not removed.

When a model round ends with no tool call, `assistant.py` hands the model a corrective
message and loops instead of ending the turn. Two rules: the text contains a tool call
written as prose, or the run's declared task list still has open items. Bounded to 2.

It works — it rescued the 12:23 run on 2026-08-12, which then logged 5 real transactions,
all carrying `external_refs` so they are dedup-protected. But the owner rejected the
approach twice as symptom-fixing in favour of **A2**, and leaving both in means two
mechanisms doing overlapping work.

**Decision:** remove it in favour of A2, or keep it as the in-turn layer beneath A2.

### A2 — The continue-supervisor (designed, never built)
**Status:** designed in full with the owner; one open parameter.

A run is not one turn — it is *work that continues until its declared goals are resolved or
its budget is spent*. The machinery already exists: `_stream_continuation` resumes a run with
its session, transcript and half-ticked task list. It has exactly one trigger today (an
approval was tapped). Give it a second.

| Run ends | Today | Proposed |
|---|---|---|
| approval answered | continue | continue |
| declared tasks unresolved | **failed** | **continue** |
| worker died mid-run (`running` at boot) | **failed** | **continue** |
| timed out with progress made | **failed** | **continue** |
| all tasks resolved | completed | completed |

The verification signal already exists — `automation_runner_adapter.py:390` computes
`unfinished` on every run. It just has no consumer except writing `failed`.

**Continue, never restart.** Nothing is re-executed, so nothing double-writes.

**Fail-closed allowlist — never continue:**
- `cancelled` — the owner pressed Stop. Absolute, no progress check, no exception.
- `waiting` **with live pending approvals** — parked on a confirmation card by design.
  Auto-continuing would resolve a write nobody approved: a confirm-gate bypass, the worst
  possible bug here.
- `completed`, or an automation that has been disabled or deleted.

A queued continuation must re-check status under the runtime claim immediately before
launch, so a Stop between scheduling and firing wins.

**Open parameter — the bound.** Recommended: continue while each attempt *advances* something
(a task status changed, a domain write landed — writes are already attributed via
`automation_run_id` on the audit scope), with a hard cap of 3. A stuck model then costs one
extra round to detect rather than three. "At least one tool call" is too weak a test: the
2026-08-12 11:02 fabrication called two tools and achieved nothing.

No separate hang watchdog is needed — the 1200s turn cap already is one, and an idle watchdog
was considered and rejected in `core/config.py` for a good reason (a legitimate long tool call
goes quiet for minutes).

### A3 — Model choice for automations
`haiku` fails 59% of runs against `sonnet`'s 13% on identical work, and its failures are the
dangerous kind — confident summaries of work it never did. Config change, not code.

### A4 — `news_screen` on Haiku, and `_DAILY_SCREEN_CAP`
35 `error_max_structured_output_retries` failures in one day (median 26s, max 198s, ~18
minutes of wall clock producing nothing), each holding one of only two CLI concurrency slots.
Separately, `_DAILY_SCREEN_CAP = 1_000_000` was set "for testing" on 2026-07-19 and nothing
bounds that spend.

---

## B — Open defects

### B1 — The nudge reads as a message the owner typed
It is a bare `role: "user"` message with no provenance marker, so it renders in the
conversation as the owner's own words and the model cannot tell harness from human.

A true `system` role is not portable: Anthropic's `system` is a top-level request parameter,
not a message role, and both CLI transports flatten to a text transcript with no system
concept. The portable fix is `role: "user"` plus an explicit harness label — a
`«system_note»` fence in the model-visible text and a distinct render in the UI. Same
mechanism as the existing `«user_msg:nonce»` fence, opposite purpose.

This applies to A2's continuations too: a resumed run has to be told *why* it was resumed,
and that message has the same provenance problem.

### B2 — Chatting into a failed run's conversation can trigger the nudge
`stream_automation_followup` → `_stream_continuation` → `run_plan.begin()` +
`_seed_plan_from(baseline)`. So typing a question into a **failed** run's conversation puts
that run's unfinished tasks in scope, and the nudge's task-list rule can fire on the owner's
question — sending the model off doing automation work mid-conversation.

Fix: mark the plan scope attended/unattended at `begin()`. The three callers differ —
fresh run and `stream_automation_resolution` are unattended (nudge on),
`stream_automation_followup` is attended (nudge off).

### B3 — The prompt still tells every continuation to re-declare its plan
The instruction *"Before your first other tool call, call `add_run_tasks` with the steps you
intend to take"* lives in the run's **opening message** — deliberately, so the date survives
context folding — and a continuation replays that transcript. The model re-reads a
start-of-run instruction on resume and complies.

Mitigated at the choke (`run_plan.claim_restatement()` — the first `add_run_tasks` on a seeded
plan returns the existing list instead of appending a parallel one), so the plan no longer
forks. But the prompt is still wrong and the choke is absorbing it.

### B4 — A failed goal check has no conversation-side record
18 of the 41 failed runs carry `last_turn_status: ok` — the *turn* succeeded and the *run*
failed its goal check ("ended with 5 of 6 tasks unfinished"). Those conversations are not
empty, so the error backfill correctly skipped them, and stamping a turn error on a turn that
did not error would invent a fact.

**Undecided:** should a run-level verdict appear in the conversation at all, and as what?

### B5 — 12 runs predate `last_turn_status`
No stored status, so there is no evidence their session's last turn was that run's. Left
alone deliberately; recorded here so nobody "fixes" it by guessing.

### B6 — `claude_cli` sends an empty text block
`API Error: 400 messages: text content blocks must be non-empty`, on 2026-08-15 and
2026-08-16. Reported fixed by another agent 2026-08-16; **not verified here.**

---

## C — Shipped 2026-08-16, committed 2026-08-17 (`009fe531`), not yet owner-verified

| ID | Change | Evidence |
|---|---|---|
| C1 | A failed turn's error is persisted (`last_turn_error` on session meta) and rebuilt by `rehydrate_messages`, so it renders in the dock, side panel and Lucius tab through the existing `MessageBubble` path | `tests/test_last_turn_error_rehydrates.py` (11) |
| C2 | Backfill for runs that failed before C1 existed — **11 of 41** written; 18 skipped (`turn_ok`), 12 skipped (`no_status`) | `tests/test_backfill_last_turn_error.py` (8), `backend/scripts/backfill_last_turn_error.py` |
| C3 | A continuation no longer forks its task list (B3's mitigation) | `tests/test_run_plan.py` (+4) |
| C4 | A crashed turn keeps its transcript — the rollback that deleted it is gone | `tests/test_p0_5_concurrency.py` (updated), `tests/test_last_turn_error_rehydrates.py` |
| C5 | A conversation hydrated while empty is no longer sealed empty forever | `frontend/src/hooks/useChat.emptyHydratedSlice.test.ts` (3) |

**C4 detail, because it changed a documented invariant.** `del history[rollback_history_len:]`
deleted a crashed turn's entire transcript. Measured on `run_2c606ddf3dc7`: the run row
recorded **24 tool calls and 5 of 6 tasks completed**, while the session kept **one** message.
Everything it did — ledger writes included — left no readable account. Progress survived only
because the task list is projected onto the run *row*.

CONC-H3 ("a concurrent turn must not be wiped by another turn's failure") is not weakened —
with no rollback, nothing can wipe one, so it holds by construction. The test's other
assertion (serialization via the session lock) is untouched. `_fix_dangling_tool_uses`, not
the rollback, is what prevents the 400 on retry, and it still runs.

**C5 detail.** `useChat`'s hydration effect skipped on `slice.hydrated` alone. Opening an
automation's conversation once while empty stamped `{messages: [], hydrated: true}`
permanently; `reconcileWithRemote` could not rescue it because `shouldAdoptRemote` returns
false on an empty remote. A live run always looked fine (live-attach populates the slice
without hydrating), which is why the bug read as random.

---

## P — Process debt

- **P1 — RESOLVED: C1–C5 are now committed** (`009fe531`, 2026-08-17, "wip: remove
  context_budget preflight gate; add last-turn-error backfill + automations reliability
  doc" — committed as-is per explicit owner instruction, the commit's own message says,
  "not reviewed or tested in this session"). That commit also DELETED
  `backend/ai/context_budget.py` in the same breath as C1–C5 — the very entanglement this
  item warned about landed anyway. **This makes CLAUDE.md's extensive `ai/context_budget.py::
  run_preflight` documentation (the "gates EVERY provider round" paragraph under "AI
  harness") describe a module that no longer exists** — `backend/ai/context_budget.py` is
  gone from the working tree; that section of CLAUDE.md needs the same audit-against-code
  treatment this file got, and is out of this doc's own ownership to fix.
- **P2 — `--reload` is on**, against the live data tree. It kills in-flight runs *and*
  deploys whatever anyone saves. Root cause of both the 29% restart bucket and A1 shipping
  unapproved.
- **P3 — no `/security-review`** on any of C1–C5, which touch the tool-authorization surface
  and a money-writing automation.
- **P4 — no UI Quality Gate** on C5 (`useChat`/`ChatPanel`). Unit tests only; no acceptance
  file, no `make qa-e2e`, no `make ui-gate`, no screenshots.
- **P5 — concurrent-agent hazard.** Two agents are editing the same backend files. `git stash`
  was used on `assistant.py` during that window to isolate a test failure, which could have
  clobbered the other lane's work; it did not, but by luck rather than care. Don't do that.

## Known-failing tests, none from this work

`test_context_budget` (the other lane deleted that module), `test_instruments_service` and
`test_search_market` (network-dependent), and a flaky symlink case in
`test_migrate_automations_v3`.
