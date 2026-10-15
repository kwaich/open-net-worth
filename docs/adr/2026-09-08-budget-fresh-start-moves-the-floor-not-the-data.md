---
title: A budget "fresh start" moves the carry floor, never the data — one reversible config field, previewed by running the real RTA fold at an overridden floor
status: Accepted
date: 2026-09-08
deciders: implementing agent (feat/budget-fresh-start lane) + repo owner (design approved before implementation)
---

# 2026-09-08. A budget fresh start moves the floor, not the data

## Context

A book that imported years of history, or that was budgeted badly for a while, drags that past
into every month forever: `_available(m, env) = max(0, available(m−1, env)) + assigned + activity`
walks back to the first month with data. The user's actual intent — *"forget what happened before
September; start from the cash I have now"* — had exactly one existing expression: the AI tool
`set_spending_limit_rollover_start`, which writes `budget_config.rollover_start`, the floor of
that walk (`planning._floor_month` → `ctx["floor"]`, consumed by `_available` and
`_ready_to_assign`). There was no UI for it at all, and the old REST surface for budget config
was deliberately retired in RFC `2026-08-22-pluggable-budget-engines` Phase 4 (zero consumers).

Two forces shaped how to expose it:

1. **It looks destructive and is not.** "Start fresh" reads like "delete my history". Moving the
   floor alters no transaction, no assignment, no envelope — it changes only where a recursion
   stops. But the *effect* on Ready to Assign is large (every positive carry stops carrying and
   lands back in RTA), so the user must be shown the resulting number before committing.
2. **A promised number must be the real number.** Showing an estimate that the next render
   contradicts is worse than showing nothing. The obvious cheap implementation — write
   `rollover_start`, read `budget_month`, write the old value back — is exactly the pattern
   `ledger.resolve_transaction_row` exists to avoid: it makes the book briefly wrong for every
   concurrent reader, and it checkpoints a phantom edit.

## Decision

We will treat a fresh start as **one reversible config write**, and preview it by running the
**real** fold at a hypothetical floor.

- `_floor_month(txns, floor_override=None)` and `_build_context(month, floor_override=None)` take
  a floor as a **call argument**. `planning.fresh_start_preview(month)` builds two contexts — one
  at the stored floor, one overridden to `month` — and reads `ready_to_assign_after` from the same
  `_display_rta` both `budget_month` and the UI read. Nothing is persisted; no store is touched.
  The wire shape is the fields the dialog renders (`month`, `ready_to_assign_after`,
  `cash_at_month_start` — **amended later the same day** to add the rest of that figure's
  arithmetic, `inflows` / `outside_envelopes_spend` / `other_movements`, after the original
  single-sentence copy was found to quote a cash figure the promise didn't follow from: spending
  in categories with no envelope drains RTA directly and nothing named it. An `assigned` field
  was added in that same pass and REMOVED again by the second amendment below, which clears the
  month's assignments — there is nothing assigned left for the figure to be net of);
  `ready_to_assign_now`/`carried_in_dropped`/`assigned_cleared` stay service-internal, where they
  pin the arithmetic in tests without becoming an API contract nothing consumes.
- The write is `planning.set_rollover_start(...)` over `services.budgets.write_budget_config` —
  which stays the **single writer** of that field, shared with `set_spending_limit_rollover_start`.
  The money choke `apply_budget_change` is deliberately NOT involved: no money moves, and routing
  a profile-config write through the envelope/assignment dispatcher would create a second writer
  for a field that already has one.
- **The anchor month is validated in that single writer, not at each caller.** Validating in
  `set_rollover_start` looked equivalent and wasn't: the AI tool writes to `write_budget_config`
  directly, so its own hand-rolled check was the rule for the model — and that check accepted
  `2026-99` and any future month while REST refused both. One writer, one rule; REST 422s and the
  tool wraps the same `ValidationError` into its `{ok: false, error}` shape.
- Three narrowly-scoped routes under `/api/planning/`: `GET`/`PUT /budget-config` (only
  `rollover_start` is settable — `pre_budget_baseline` is a cutover capture, not a user control)
  and `GET /fresh-start-preview?ym=`. `GET /api/planning/month` echoes `rollover_start` read-only.
  A **FUTURE** anchor is refused: the floor is also `_ready_to_assign`'s zero-line, so a floor
  ahead of today blanks Ready to Assign and every envelope in every month the user can actually
  reach. A floor *before* the first month with data stays legal — that is simply "carry
  everything", which is the default.
- Because the write is one field, **Undo is the same PUT with the previous value** — held in
  memory for the undo pill's lifetime. No compensating edits, nothing to replay.
- The floor is ALSO the month-navigation bound (`_nav`: `earliest`/`can_go_back`), so starting
  fresh stops the Budget paging back past that month. We keep that — the bound and the carry are
  one fact, and a browsable month whose envelopes all read 0 would be its own kind of lie — but we
  **disclose it in the confirm dialog** ("Earlier months stop showing in Budget until you resume
  history") rather than let the user discover it afterwards. Nothing is deleted: the transactions
  are still in Accounts and in reports, and the months return on Resume.

## Consequences

### Positive

- The dialog's figure is provably the post-confirm figure: `tests/test_planning_fresh_start.py`
  asserts `fresh_start_preview(m)["ready_to_assign_after"] == budget_month(m)["ready_to_assign"]`
  *after* actually writing the floor, and separately that the preview wrote nothing.
- `carried_in_dropped` (Σ max(0, previous-month available)) is exactly the gap between the two RTA
  figures when no future month is assigned, which is what lets a test prove the arithmetic rather
  than just compare two opaque numbers.
- The floor override is a pure parameter, so any future "what would this month look like if…"
  read can reuse it without a write-and-revert dance.
- Fully reversible, in the UI (Undo / "Resume earlier history") and in the data (one field).

### Negative

- `_build_context` grows a second parameter, and a preview costs **two** context builds (two full
  transaction folds). Acceptable for a once-in-a-while confirm dialog; it would not be for a
  per-row read.
- The stored floor is now surfaced in two places — the kebab label (from `GET /budget-config`) and
  the state line under the hero (from the month payload's echo). They are both server truth, but
  they are two reads that can be momentarily out of step; the write invalidates every
  floor-dependent planning read to close that window.
- **Earlier Budget months become unreachable** while a floor is set (above). Disclosed, reversible,
  and still the sharpest edge of the feature.
- A fresh start is silent in history: nothing records that it happened, or when, beyond the field
  itself. Deliberate for now (see Follow-ups).

### Follow-ups

- Product doc: "Starting fresh" in `docs/product/features/budgeting.md` (done).
- Acceptance: `BUD-FRESH-1..3` in `docs/qa/acceptance/budgeting.md` + the browser-use spec
  `qa/browser-use/specs/budget_fresh_start.yaml` (done).
- Not done, deliberately: no audit trail for the change, and no preview for the *resume*
  direction (the dialog explains it instead of quoting a figure).

## Alternatives considered

- **Write `rollover_start`, read the month, write it back** — rejected: makes the book wrong for
  every concurrent reader for the width of the request, and fires the write hook twice for an edit
  the user has not agreed to.
- **Estimate the after-figure client-side** (`rta_now + Σ max(0, prev available)`) — rejected: the
  identity has terms the client doesn't model (`pre_budget_baseline`, the card-uncovered term, the
  clamped future-assigned deduction), so the estimate would be right until it wasn't.
- **Actually delete/zero pre-floor assignments** — rejected outright: irreversible, and it would
  make the ledger and the budget disagree about months the user can still browse to.
- **Route the write through `apply_budget_change`** — rejected: `budget_config` lives in the
  profile store, not the envelope/assignment tables the choke composes; adding it there would give
  the field two writers (this and `write_budget_config`) for no atomicity gain.
- **Revive the retired `PUT /api/budget-config`** — rejected: that surface was removed for having
  no consumers and a wider write scope; the new routes are `/api/planning/`-scoped and set exactly
  one field.

## Agent provenance

- **Decided by:** implementing agent, senior-engineer lane
- **Lane / branch:** `feat/budget-fresh-start` (worktree `onw-fresh-start`)
- **Alternatives weighed:** write-then-revert preview · client-side estimate · destructive reset ·
  routing through `apply_budget_change` · reviving the retired budget-config REST surface
- **Driving artifacts:** owner-approved design brief for the Plan → Budget "Start fresh" action;
  `backend/services/planning.py` `_floor_month`/`_ready_to_assign`; RFC
  `2026-08-22-pluggable-budget-engines.md` §Phase 4 (why the old REST routes are gone)
- **Why this way (one line):** the number a confirm dialog promises must be the number the app
  will show, and the only way to guarantee that is to run the real fold — so the floor became a
  parameter instead of a temporary write.

---

## Amended 2026-09-08 — a fresh start also clears the month's assignments

**Owner decision, superseding the "never the data" half of the title.** Shipped, verified, and
then used: moving the floor alone leaves September's envelopes still funded from a plan the user
has just declared they want to abandon. "Start fresh" has to mean a blank slate for that month,
not a blank slate for its history only.

**What changed**

- `planning.fresh_start(month)` replaces the bare `set_rollover_start(month)` on the confirm
  path. It snapshots the month's assignments and the current floor, zeroes every assignment
  **through `apply_budget_change`'s `assign` op**, then moves the floor — all inside ONE
  `locked_unit_of_work(budget_assignments, plan_state, profile)`. The before-image (floor +
  the month's assignment map) is stored server-side under a one-shot token in
  `plan_state.payload["budget"]["fresh_start_undo"]`; the write returns `{token, month}` only.
- `planning.undo_fresh_start(token)` loads that stored snapshot, puts both halves back in one
  transaction, and deletes it (a second undo, or a token that isn't the pending one, is a
  `ConflictError`). Undo restores the floor **and** every cleared assignment; **"Resume earlier history" restores the floor only**
  and says so in its own copy.
- `fresh_start_preview` folds with `zero_month_assignments=True` as well as the floor override,
  so the promised figure is still the real post-write number. Its `assigned` term is gone from
  the wire and the "Already assigned" row is gone from the dialog's ledger — there is nothing
  assigned left for the figure to be net of.
- REST: `POST /api/planning/fresh-start` + `POST /api/planning/fresh-start/undo`.
  `PUT /api/planning/budget-config` remains, now exclusively the floor-only "Resume" write.

**What this reverses from the original "Alternatives rejected" list**

- *"Actually delete/zero pre-floor assignments — rejected outright: irreversible"* — the objection
  was **irreversibility**, not the clearing itself. A before-image held by the server and
  replayed by Undo removes it — the client never carries the amounts: the round trip leaves `budget_assignments` byte-identical (pinned by
  `test_undo_restores_the_assignment_table_byte_identically`). Note the scope is still narrower
  than that rejected option: only the TARGET month is cleared, never everything before the floor.
- *"Route the write through `apply_budget_change` — rejected: no atomicity gain"* — that held
  while the only write was one profile field. Now that a money table and the config move together,
  the atomicity is the whole point, and the assignment half must go through the money choke to
  stay checkpoint-covered. `budget_config` still has exactly one writer
  (`budgets.write_budget_config`); it simply accepts a `conn=` now.

**Consequences**

- `apply_budget_change` gains two keyword-only Python parameters: `conn=` (composes the `assign`
  op into a caller's transaction; **raises** if passed with any other op, so a caller can't
  believe its write joined a transaction it didn't) and `allow_negative=` (a snapshot may hold a
  negative assigned amount — `move_money` out of a row legitimately produces one — and a restore
  must reproduce a state the book already had, not author a new one).
- **`allow_negative` being un-nameable from a request body is not, on its own, the safety
  property** — pre-merge review found the first cut of the undo route replayed a CLIENT-SUPPLIED
  assignment map with it set, which reached the same effect without ever naming the kwarg (a
  forged `{env: -5000}` in a future month moved displayed Ready to Assign through `_display_rta`'s
  clamp; the body's defaulted null `rollover_start` also cleared the carry floor). The guarantee
  that actually holds is about the VALUES: `undo_fresh_start` takes a one-shot token, loads the
  before-image the server itself wrote to `plan_state.payload["budget"]["fresh_start_undo"]`, and
  replays only that. `POST /api/planning/fresh-start` returns `{token, month}` and nothing else;
  the undo body is `{token}`; a stale/unknown token or a second undo is a 409 that writes nothing.
  A restart mid-undo-window is survivable because the snapshot is a DB row, not process memory.
- **An assignment edited between the fresh start and Undo is DISCARDED by Undo**, which replays
  the pre-fresh-start amounts wholesale. Accepted: the window is the 7-second undo pill, and a
  partial merge would be less predictable than "put it back exactly as it was".
- Scope held deliberately: the **zero-based lens' assignment table only**. The cash-envelope
  engine's pots and 50/30/20's config are that engine's state; a fresh start in this budget must
  not silently rewrite a lens the user isn't looking at (pinned by
  `test_fresh_start_leaves_the_other_engines_state_alone`).
- Half-applied is the state worth fearing — "Fresh start · since September" over still-funded
  envelopes, or the reverse — so `test_fresh_start_is_atomic_when_the_floor_write_fails` drives a
  failing floor write and asserts the assignments rolled back.

---

## Amended 2026-09-08 (identity change) — the ledger IS the RTA identity, exactly

**Owner decision, same day as the identity change (ADR
`2026-09-08-ready-to-assign-is-unassigned-cash`).** Ready to Assign stopped being a YNAB-style
flow reconstruction and became a straight balance identity: `RTA(m) = cash(m) − baseline −
set_aside(m) − unfunded_card_debt(m)`. The fresh-start preview's own ledger — the second
amendment above ("cash_at_month_start + inflows − outside_envelopes_spend + other_movements")
— was built to reconcile the OLD identity's terms; it does not survive the new one, because two
of its four named terms (`inflows`, `outside_envelopes_spend`) and its residual
(`other_movements`) all existed to patch gaps in a flow-based reconstruction that no longer
exists.

**What changed**

- `planning.fresh_start_preview(month)` now returns `{month, ready_to_assign_after, cash_now,
  set_aside, unfunded_card_debt, baseline}` (+ an optional sixth key, below). `cash_now =
  _cash_as_of(after, month)` — cash as of the END of `month` ITSELF, not the previous month's
  opening frame the old `cash_at_month_start` quoted: the new identity is measured AT the cash,
  this month's own activity included, so there is no separate "starting frame + this month's
  movements" shape left to reconstruct. `set_aside = _set_aside(after, month)`,
  `unfunded_card_debt = _unfunded_card_debt(after, month)` and `baseline = after["baseline"]` are
  read off the SAME hypothetical context (floor overridden to `month`, that month's assignments
  zeroed) the write actually produces, exactly as before.
- The identity closes EXACTLY, by construction, on every book: `ready_to_assign_after == cash_now
  − baseline − set_aside − unfunded_card_debt − assigned_in_future` (the last term only when
  present — below). No residual "other movements" term is needed: `inflows`/
  `outside_envelopes_spend` existed only because the old identity's `reserved` sum excluded
  unbudgeted spend and needed a named term to say where it went; the new identity has no such
  carve-out — a transfer/investment crossing the on-budget boundary, an adjustment, a starting
  balance, and foreign-cash FX drift are all just cash now, with nothing left to enumerate
  separately.
- **`set_aside` was a same-day correction, not part of the original cut of this amendment.** A
  first draft returned only `{cash_now, unfunded_card_debt, baseline}`, reasoning that the write
  clears the month's assignments and the hypothetical floor stops any carry, so a plain spend
  (negative activity) leaves nothing held back. That is true for a plain spend, but NOT for every
  activity: a refund landing in a spending envelope THIS month, or a card swipe THIS month that
  its own envelope's headroom covers (Task 1's coverage cap, `_capped_card_credits`, crediting the
  payment envelope), both leave a POSITIVE row available even with assigned zeroed and no carry —
  and that positive amount is exactly what the RTA popover's own `set_aside` term holds back.
  Omitting it made the three-term sum happen to equal the true `_display_rta` value on every
  fixture the first draft tried (none had a same-month refund or same-month covered swipe) but
  not by construction — a coordinator review caught the gap before it shipped. Fixed by adding
  `set_aside` unconditionally rather than leaving a residual term or special-casing the two
  scenarios. Both are still pinned, under the names the restored identity gave them (ADR
  `2026-09-09-ready-to-assign-follows-ynab-envelopes-are-the-category` renamed the term
  `set_aside` → `held_in_envelopes` and made it SIGNED):
  `tests/test_planning_fresh_start.py::test_a_same_month_refund_drives_held_in_envelopes_positive`
  and `::test_a_same_month_covered_card_swipe_needs_both_card_terms` assert the exact identity
  (not an approximation that "happens" to hold).
- **The one term that does NOT collapse into cash/set-aside**: a LATER month's own assignments
  still clamp this month's displayed figure (`_display_rta`'s owner-decided rule, unchanged by the
  identity work — a dollar promised to a future month can't be promised twice). When that clamp is
  live, the preview adds a sixth key, `assigned_in_future` (the deducted amount), and the dialog
  renders an "Assigned in future months" row for it, hidden when absent. This is needed in
  practice, not hypothetically:
  `tests/test_planning_fresh_start.py::test_assigned_in_future_is_named_when_the_clamp_is_live`
  assigns money in the month AFTER the fresh-start target and asserts the key appears and the
  ledger still closes with it subtracted.
- Frontend: `freshStart.ts`'s `FreshStartPreview` type, `freshStartLedger` (rows "Cash on hand" →
  "Set aside in envelopes" → "Card debt not yet funded" → "Baseline" → optional "Assigned in
  future months", each hidden at zero/absent) and the dialog's closing paragraph ("...This month's
  spending still shows in its envelopes...", replacing "...stays as overspends to cover...", which
  described the same fact from the old vocabulary) all moved together; `cashOnMonthStartLabel`
  (the "Cash on 1 {Mon}" row) is retired — the row no longer names a date, because the figure it
  labels no longer is one.

**Why this is still "moves the floor, not the data," amended**

The write itself (`fresh_start`/`undo_fresh_start`) is UNTOUCHED by this amendment — it still
clears one month's assignments and moves one config field, in one transaction, exactly as the
first amendment above describes. Only the PREVIEW's own arithmetic decomposition changed, because
the identity it was decomposing changed underneath it. The ADR's core claim (a fresh start moves
a floor and clears one month, never touches a transaction) is exactly as true as it was; what
changed is what number the dialog uses to prove the move landed.

**Records**

- ADR `2026-09-08-ready-to-assign-is-unassigned-cash.md` — the identity change this amends.
- `docs/PLANNING_MODEL.md`'s fresh-start paragraph; `docs/qa/acceptance/budgeting.md`
  `BUD-FRESH-1..3`; `qa/browser-use/specs/budget_fresh_start.yaml`;
  `frontend/qa/shoot-budget-fresh-start.mjs`.

## Amendment (2026-09-09) — the ledger's terms follow the restored YNAB identity

ADR `2026-09-09-ready-to-assign-follows-ynab-envelopes-are-the-category` restores the
pre-2026-09-08 hold/reset engine. The mechanics here — one transaction through
`apply_budget_change`, the server-held undo snapshot, `floor_override=`/`zero_month_assignments=`
never writes-then-reverts — are UNCHANGED. Only `fresh_start_preview`'s returned ledger terms
change to match the restored `_ready_to_assign` (`cash_as_of − baseline − Σ available`), built as
an exact, closed sum with no named residual — the same discipline 2026-09-08 introduced, applied
to the restored formula rather than the one it briefly replaced.
