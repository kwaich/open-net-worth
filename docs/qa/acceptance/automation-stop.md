# Acceptance — Stopping an Automation run

Design authority: `.claude/rfcs/one-turn-lifecycle.md` +
`docs/adr/2026-08-06-one-turn-lifecycle-for-chat-and-automations.md`. Verify on the running app via
`make qa-e2e` / `make qa-cu` against an isolated QA server (8090–8099, `ONW_QA_MODE=1`) — never
`:5174`/`:8080` directly.

Until 2026-08-06 an Automation run could not be stopped from anywhere in the product: chat's Stop
returned 409 for an Automation-owned conversation on the reasoning that the Automations tab owned
the control, and the Automations tab had no such control. A scheduled run that looped, misbehaved
or burned money could not be interrupted at all. These criteria cover the control on both surfaces
and the state it must leave behind.

## AS1 — Stop is offered wherever the run is visible

- **AS1.1** GIVEN an Automation run is in flight WHEN its conversation is open in the chat panel
  THEN the composer's send button reads as **Stop**, exactly as it does for a turn this browser
  started. `following` (this browser did not open the turn) no longer withholds it.
- **AS1.2** GIVEN an Automation with a run in flight WHEN its detail is open on the Automations tab
  THEN the header's **Run now** button is replaced by **Stop**; no second button appears beside it.
  Regression: `AutomationDetail.test.tsx`.
- **AS1.3** GIVEN no run is in flight THEN the header reads **Run now** and no Stop control is
  present. A `waiting` (parked for approval) run is NOT in flight — there is no task to cancel, so
  it offers Run now, not Stop.
- **AS1.4** GIVEN either surface WHEN Stop is tapped THEN exactly one
  `POST /api/chat/{session_id}/stop` is issued. There is no Automations-specific stop endpoint —
  a run is a turn, and one cancel path serves both.

## AS2 — The server actually stops it

- **AS2.1** GIVEN a run mid-tool-call WHEN Stop is tapped THEN the run's task is cancelled within
  one poll interval, the streaming text in the panel halts, and the working indicator clears. No
  spinner outlives the turn.
- **AS2.2** GIVEN a stopped run THEN its run row settles to **`cancelled`**, rendered as
  **"Stopped"** in run history with a neutral dot — not "Failed" (nothing failed) and not left at
  "Running" forever. Regression: `test_one_turn_lifecycle.py`.
- **AS2.3** GIVEN a stopped run THEN the Automation's runtime claim is released, so **Run now** is
  immediately usable again. A claim nobody releases would wedge the Automation permanently.
- **AS2.4** GIVEN a stopped run THEN its summary carries the interrupted note: a tool call already
  in flight may have committed a write, so what it touched needs checking. The summary must not
  imply nothing happened — that is not known.
- **AS2.5** GIVEN a stopped run of an Automation with `max_runs` set THEN `run_count` is unchanged
  — you stopped it, so it does not consume a budgeted run — while `next_run_at` IS recomputed, so a
  scheduled Automation is not stranded.
- **AS2.6** GIVEN a stopped run THEN its conversation remains reopenable: a follow-up message
  re-enters `running` on the same row, exactly as it does after a completed or failed run.

## AS3 — Stopping is bounded to what the user can see

- **AS3.1** GIVEN two conversations with turns in flight (one chat, one Automation run) WHEN Stop
  is tapped on one THEN only that turn is cancelled; the other keeps streaming.
- **AS3.2** GIVEN a run that has already finished WHEN Stop is tapped THEN the call is a truthful
  no-op (`stopped: false`, HTTP 200) — never a 404/409, and never a fabricated session row.
- **AS3.3** GIVEN a cross-origin page WHEN it issues a bare `POST` to the stop endpoint THEN it is
  refused (403, missing `X-Requested-With`). Removing the Automation 409 must not have widened what
  an untrusted origin can cancel.

## AS4 — Parity with chat, mid-run

- **AS4.1** GIVEN an Automation run in flight WHEN the user types a message into its conversation
  THEN it is spliced into the RUNNING run (queued and drained at the run's own injection points),
  not held as a followup behind it. This works through the ordinary `executing_turn_id` route —
  the Automation-specific branch that once existed in `api/chat.py` is deleted.
- **AS4.2** GIVEN an Automation run that writes data WHEN it is stopped THEN its turn checkpoint is
  still finalized and the writes it made up to that point remain rewindable, exactly as for a
  stopped chat turn.
