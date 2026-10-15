---
title: Outlook what-if staging hardening — sign convention, accumulation, removal, visibility
status: Accepted
date: 2026-09-03
deciders: orchestrating Claude agent + repo owner
---

# 2026-09-03. Outlook what-if staging hardening — sign convention, accumulation, removal, visibility

## Context

A live conversation ("what if i buy another house at 45?, for 1mil?", session
`eccea467-e2f2-45f3-9bbf-c1615bf9e643`, then a follow-up session `957f2ef1-...`) surfaced a chain
of real defects in `plan.stage_whatif` (the screen command that lets Lucius drive the Outlook
tab's ephemeral what-if simulation — `frontend/src/components/tabs/plan/`):

1. **Sign convention was backwards.** `addEvents[].amount` was `+ = spend, - = windfall` —
   opposite of a ledger amount, of common financial intuition, of the chart's own display (which
   already showed a spend with a leading "−"), and of the sibling backend tool
   `simulate_retirement`'s `one_off_events` (already `positive = windfall, negative = spend`).
   The model inverted a purchase's sign mid-conversation, caught its own mistake, and both the
   model and the user separately found the convention "weird."
2. **Fractional ages leaked in.** `f.currentAge` is deliberately fractional (precise, DOB-derived,
   load-bearing for the sim's month-level math), but every default/bound for "the age this
   happens" inherited it raw, producing values like `33.3` in a UI field where only a whole year
   makes sense.
3. **Events/streams were silently dropped, not accumulated.** Every `plan.stage_whatif` call
   rebuilt `events`/`streams` from the SAVED baseline, not from what was already staged. A
   follow-up call that didn't re-list an earlier addition silently dropped it — no error, no
   signal. Live repro: staging a house purchase, then a car swap, dropped the house.
4. **The accumulate fix had its own race.** Reading `events`/`streams` from the React state
   closure only reflects the last COMMITTED render; two `plan.stage_whatif` calls with no render
   in between (`applyState` runs inside `startTransition`, which defers) would both read the same
   stale base and the second overwrites the first — the same bug moved from "the model forgot to
   resend" to "the harness raced itself."
5. **Once accumulation worked, stale removal didn't.** `removeEvents`/`removeStreams` required an
   EXACT label match. The model recalling "porsche" instead of the actual staged label "Buy
   Porsche (Vietnam)" removed nothing, silently — which read as "there's no way to drop just
   one," so the model reached for `plan.reset` (wipes the ENTIRE staged what-if) and destroyed
   two other scenarios the user still wanted, live.
6. **The model had no ground truth for what was staged.** Nothing surfaced the exact current
   labels of staged events/streams; the old `removeEvents` hint even said "baseline event
   labels," implying it couldn't target the model's own prior additions.
7. **Same-turn result blindness (identified, NOT fixed — see Consequences/Negative).**
   `run_screen_command`'s tool result is a bare `{"status": "dispatched", "command": {...}}` — the
   actual simulation runs client-side, after the backend has already returned its response. The
   model cannot see the resulting probability/pot/chart within the same turn; it only gets an
   accurate picture on the NEXT screen snapshot. `simulate_retirement` (a separate, independent
   server-side sim) is the documented same-turn mitigation, but it requires the model to
   duplicate the same parameters into a second call with nothing enforcing the two stay in sync.

CLAUDE.md's write-choke and dependency-rule conventions don't directly bind this surface (it's
UI-only ephemeral state, `mutates=()` — `run_screen_command` writes nothing durable), but the
"evidence, never assertion" and "root cause before fix" posture applies throughout: each item
above was confirmed by reading the actual code path and, where possible, the live
`session_messages` transcript, not inferred.

## Decision

We will hold four properties for `plan.stage_whatif`/`plan.reset` going forward, enforced at the
harness level wherever possible rather than by prompting the model to behave correctly:

1. **Event/stream `amount` is signed like a ledger amount**: negative = spend, positive =
   windfall/income. Enforced in the simulation engine (`planData.ts`), the chart/sidebar display,
   the presets, the manual modal, and the tool's own description — one convention, no translation
   layer anywhere. A one-time data migration
   (`202609032128_flip_plan_event_amount_sign.py`) negates every already-persisted baseline/
   scenario event amount so existing plans keep their ORIGINAL MEANING under the new sign.
2. **"The age this happens" is always a whole number.** `outlook.tsx` rounds through one
   `currentAgeR` for every event/stream default, bound, and committed value; `stageWhatif.ts`'s
   `clampAge` rounds its own bounds; the backend (`services/plan_state.py`) rejects a non-integer
   `age`/`endAge` outright (mirrors the existing `lifeExp`/`retireAge` check) rather than silently
   truncating.
3. **Events/streams accumulate across calls, and that accumulation is race-proof.** Each
   `plan.stage_whatif` call is based on whatever is CURRENTLY staged (`eventsRef`/`streamsRef` —
   plain refs written synchronously at every mutation point, not the React state closure), not
   the saved baseline. `plan.reset` remains the explicit, full wipe.
4. **Removal degrades gracefully, and the model can see what there is to remove.**
   `removeEvents`/`removeStreams` match a case-insensitive substring, either direction (a token
   under 3 chars is ignored as too unspecific) — an imprecise recall still removes the right
   thing instead of silently removing nothing. `useRegisterScreenFacts` surfaces the exact labels,
   amounts, and ages of everything currently staged (capped at 8 each, with a "+N more" flag)
   every turn, and `plan.reset`'s description now says plainly it clears EVERYTHING, pointing at
   `removeEvents`/`removeStreams` for a single item instead.

We will NOT fix same-turn result blindness (item 7) as part of this pass — see Negative
consequences and Alternatives considered.

## Consequences

### Positive
- A staged what-if's stored sign now matches its displayed sign, `simulate_retirement`'s
  convention, and common intuition — one fewer thing for the model (or a human reading the DB
  directly via `run_sql_query`) to get backwards.
- Accumulation is now correct regardless of render timing, not just "usually correct because a
  render happens to land between calls."
- A stale staged item can be dropped precisely, even from an approximate recall, without
  resorting to a full reset that destroys unrelated staged scenarios.
- The model has ground-truth visibility into staged state every turn, closing the single largest
  source of the failures above (it literally could not see what it had already staged).

### Negative
- **Same-turn result blindness is a known, un-fixed gap.** A reply immediately after staging is
  still an estimate, never a citation of the real resulting number, because the backend cannot
  know what the client-side Monte Carlo worker will compute. This is architectural (the dispatch
  is fire-and-forget by design, ADR `2026-07-11-lucius-drives-plan-outlook`), not a quick patch.
- The staged-facts list is capped at 8 events + 8 streams; a what-if with more than that still has
  a blind spot (flagged via "+N more", not silently truncated, but still incomplete).
- Fuzzy substring removal trades precision for robustness: a short, generic label could in theory
  over-match and remove more than intended. Judged net-positive against the confirmed failure mode
  (exact match removing nothing → the model reaching for `plan.reset` instead).
- No cumulative cap on staged events/streams across a whole session — accumulation is now
  correct, but unbounded; a very long conversation could still pile up an unreadable number of
  staged items with no automatic pruning.
- The ref-based accumulation fix (item 3) has no automated test reproducing the exact same-tick
  race it guards against — `OutlookTab` has no existing mount-test harness (worker mocks,
  `usePlanState`, `useOutlookFire`, etc.), and building one was judged disproportionate to this
  fix's size. Verified by code reading, type-check, and lint only.

### Follow-ups
- Consider a cumulative cap (FIFO or explicit refusal) on staged events/streams per session.
- Consider whether `plan.stage_whatif` should gain a real same-turn result (a synchronous
  round-trip to the client's computed outcome) — a bigger architectural change, out of scope here.
- A mount-test harness for `OutlookTab` (or an extracted, independently-testable hook for the
  ref-synced staging state) would let the race-condition fix be regression-tested directly.

## Alternatives considered

- **Automatic semantic dedup/replace** (e.g. detect "this new car event probably supersedes that
  old car event" and auto-remove) — rejected: this exact conversation needed BOTH additive
  ("buy another house") and replacement ("a 911 instead of the Porsche") semantics for the SAME
  category (vehicles) in one session; a pattern-matching heuristic would get the common case
  wrong specifically where correctness matters most, and contradicts the "no hidden magic"
  posture in `CLAUDE.md`.
- **Cumulative undo-stack ("undo last stage")** — rejected as a fix for targeted removal: the
  live case needed to remove an item staged TWO calls earlier while KEEPING the most recent
  call's addition; "undo last N" doesn't express that.
- **A `replaces` field on `addEvents` entries** (couple an add+remove atomically) — deferred: real
  ergonomic improvement, but still requires the model to correctly name what's superseded: it
  doesn't change the fundamental "does the model know what to remove" problem that visibility
  (the chosen fix) already addresses more directly.
- **Fix same-turn result blindness now** — rejected for this pass: requires the backend to hold a
  tool call open pending a client-side computed result, or a new bidirectional channel; a real
  architecture change, not a hardening fix, and `simulate_retirement` already exists as a
  documented (if imperfect) mitigation.

## Agent provenance

- **Decided by:** orchestrating Claude agent (interactive session, direct implementation — no
  sub-agent dispatch)
- **Lane / branch:** `main`, direct commits (no worktree isolation) — the repo owner drove each
  step interactively and reviewed as it went
- **Alternatives weighed:** see above — semantic auto-dedup, undo-stack, atomic `replaces` field,
  and fixing same-turn blindness immediately were all considered and rejected/deferred
- **Driving artifacts:** live `session_messages` transcripts (`eccea467-e2f2-45f3-9bbf-c1615bf9e643`,
  `74c68247-cdca-4a72-8818-a8d3bd1f8573`, `957f2ef1-0e1f-4854-bb20-8e19af81101a`) read directly
  from `~/.open-net-worth/data/onw.db`, not inferred from screenshots alone; commits `1fd2b4ab6`,
  `c24e6f00f`, `6cc9f4476`, `6bbd6110e`, `729ee1bb6`, `463aaf667`
- **Why this way (one line):** every fix targets a mechanism the model can't get wrong by
  omission (rounding, matching, visibility, synchronous state) rather than asking the model to
  remember to behave correctly, per the owner's explicit "harness perspective, not the model"
  framing — except same-turn result blindness, which is architectural and deliberately deferred.
