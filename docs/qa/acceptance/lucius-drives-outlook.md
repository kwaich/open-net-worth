# Acceptance — Lucius drives Plan/Outlook (two-way screen contract)

Spec: `docs/superpowers/specs/2026-07-11-lucius-drives-plan-outlook-design.md`.
Harness: `make qa-e2e` (browser-use) against an isolated QA server (8090–8099, `ONW_QA_MODE=1`).
The chat criteria need a real model key; without one, verify the SSE/bus layer via the unit
suites and the chip/park criteria via a scripted `run_screen_command` result.

## LDO-1 — on-screen what-if moves the levers
GIVEN the Plan → Outlook sub-tab is visible with a saved FIRE plan
WHEN the user asks the dock "what if I retire at 45?"
THEN `run_screen_command("plan.stage_whatif", {retireAge: 45})` executes with status `dispatched`
AND the retire-age lever, chart, and headline odds update WITHOUT a page reload
AND the reply's numbers are phrased as approximate.

## LDO-2 — ephemerality is visible
GIVEN LDO-1 just staged a what-if
WHEN the user reloads the page
THEN the Outlook shows the SAVED plan (the what-if is gone) — nothing persisted.

## LDO-3 — off-screen what-if parks + chip navigates applied
GIVEN the user is on the Cash tab
WHEN they ask the dock "what if I add a $90k baby event at 36?"
THEN the reply carries a "View on Outlook →" chip (tool status `parked`)
AND clicking it navigates to Plan → Outlook with the event staged on the chart.

## LDO-4 — chip survives reload
GIVEN LDO-3's reply exists
WHEN the user reloads and clicks the chip in the restored transcript
THEN navigation + staging still work (block re-derived from the persisted tool pair).

## LDO-5 — persisted event add refreshes the screen
GIVEN the Outlook is visible
WHEN the user says "add a $90k baby event at 36 and update my plan" and Lucius calls
`set_plan_baseline`
THEN a `data_changed {domains:["plan"]}` rides the turn
AND the Outlook refetches: the event appears as a saved-baseline event (survives reload)
AND a "Plan updated from chat" toast shows.

## LDO-6 — salary update re-seeds the levers (the fixed staleness bug)
GIVEN the Outlook is visible with monthly saving derived from the profile
WHEN the user says "my salary is now $9k/month" and Lucius calls `update_profile`
THEN `data_changed {domains:["profile"]}` rides the turn
AND the Outlook re-fetches `/api/fire` and the saving lever re-seeds WITHOUT reload.

## LDO-7 — reset command
GIVEN a staged what-if on the visible Outlook
WHEN the user says "reset the what-if"
THEN `plan.reset` restores the saved plan and a toast confirms.

## LDO-8 — headless/scope safety
GIVEN a scheduled (headless) agent turn
WHEN it attempts `run_screen_command`
THEN the call is refused by the headless allowlist (tool absent from
`HEADLESS_ALLOWED_TOOLS`) — pinned by
`tests/test_screen_commands.py::test_run_screen_command_not_headless`.

## LDO-9 — legacy refresh parity
GIVEN any tool from the retired frontend MUTATING_TOOLS set runs in a chat turn
(e.g. `add_transaction`)
THEN the portfolio/Cash views still refresh on turn end (now via
`data_changed` domains) — pinned by
`tests/test_tool_domains.py::test_old_frontend_mutating_set_still_covered`.
