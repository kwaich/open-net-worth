# Acceptance criteria — Budgeting (YNAB planning correctness)

The money-math trust contract for the **② Budgeting** flow (the `② Budgeting` lane in
[`docs/launch/BOARD.md`](../../launch/BOARD.md)). Computational criteria — checkable at the
service layer (`backend/services/planning.py`) with constructed inputs — complementing the
UI-level Plan-tab criteria in [`plan.md`](plan.md). The QA agent must exercise every
criterion against the service / running app and report PASS/FAIL per ID.

Base currency SGD unless stated. "RTA" = Ready-to-Assign.

Legend: ✅ = verified by the 2026-06-28 reality audit · 🧪 = lands with the
`lane/B1-B3-budget-money` fixes (verify on merge).

---

### BUD-RTA-INFLOW ✅
- GIVEN a month with income categorized as inflow (uncategorized / ready_to_assign)
- WHEN Ready-to-Assign is computed
- THEN RTA includes that income, summed in BASE currency (via `txn_base_amount`).

### BUD-RTA-ASSIGN ✅
- GIVEN RTA of X
- WHEN Y is assigned across categories
- THEN RTA == X − Y. Over-assignment past RTA is allowed → negative RTA, which is corrected by a future inflow.

### BUD-AVAILABLE ✅
- GIVEN a category with prior-month available A, assigned B this month, activity C
- WHEN available is computed
- THEN available == `max(0, A) + B + C` — the CARRY (A) clamps at 0 going into the new month (ADR `2026-09-09-ready-to-assign-follows-ynab-envelopes-are-the-category` restores the clamp `2026-09-08` had removed); B and C are SIGNED and uncapped, so an in-month overspend (B+C net negative) still reads negative all month — it is only reset by the NEXT month's clamped carry, not immediately (see BUD-RTA-2). Computed every read, never trusted from storage.

### BUD-ROLLOVER-POSITIVE ✅
- GIVEN a category with positive leftover available at month end
- WHEN the next month is computed
- THEN that positive available carries into next month's `prev` term unchanged — `max(0, prev)` only clamps a NEGATIVE carry to 0, so a positive one is untouched either way.

### BUD-MOVE-CONSERVE ✅
- GIVEN `move_money` of Z from category A to B
- WHEN applied
- THEN A's assigned drops by Z, B's rises by Z, and the month's total assigned is unchanged (no money created/destroyed).

### BUD-MOVE-CAP-AVAILABLE ✅ (supersedes BUD-MOVE-NO-NEGATIVE/B2, FEB-02 fix 2026-07-04)
- GIVEN a category row with AVAILABLE = A this month (its own assigned + activity + any positive carry from last month)
- WHEN `move_money` attempts to move more than A out of it (envelope→envelope, or envelope→`__rta` release)
- THEN it is REFUSED (a typed validation error naming the row and its available figure).
- GIVEN a category with THIS MONTH assigned = 0 but AVAILABLE = 500 (money carried from a prior month)
- WHEN `move_money` moves 500 out of it
- THEN it SUCCEEDS: the row's assigned for this month goes NEGATIVE (−500) — `BudgetAssignmentsStore.set_assigned` stores a negative amount as-is (only an EXACT zero pops the entry) — and the availability identity `prev + assigned + activity` still yields the correct available (0) for this month and the correct rollover into next month. RTA is unchanged (the move nets to zero in-month). Owner decision 2026-07-04: the true cap on moving money OUT of an envelope is its AVAILABLE, not this-month assigned — YNAB-correct, since carried-over money is movable.

### BUD-TARGET ✅
- GIVEN a category target (monthly / weekly / yearly / by_date, incl. snooze)
- WHEN the budget month is computed
- THEN `needed_this_month` reflects the target's **flavor** (`needs_whole_amount`), measured as:
  - **Set aside** (`needs_whole_amount=True`, recurring) → against **assigned this month**; a funded target stays funded even after you spend it.
  - **Fill up to** (`needs_whole_amount=False`, recurring) → against what you **funded this month** (rollover + assigned = `available − activity`); spending it down does NOT re-open the target mid-month — it refills next month.
  - **Set aside by [date]** (`by_date`, `needs_whole_amount=True`) → against **cumulative contributions** (`assigned_to_date`), so spending never sets the dated goal back.
  - **Have a balance of** (`by_date`, `needs_whole_amount=False`, the by_date default) → against the running **available** balance (a savings pot you don't spend down; spending sets you back).
  - `needs_whole_amount` default: `True` for every type; `by_date` persists `True` explicitly (its default read-back is "have a balance of"). ADR `docs/adr/2026-07-10-set-aside-by-date-target-flavor.md`.

### BUD-TARGET-COMMITMENT-CHIP ✅
- GIVEN a Commitments-synced budget row (its `commitment.envelope_id` FK points at the row's envelope)
- WHEN the ↗ chip on the row is clicked
- THEN it deep-links to THAT commitment's detail via `/commitments?open=<id>` (refresh-safe); the chip never shows on a Card-Payment row.

### BUD-GROUP-OTHERS ✅
- GIVEN the default "Others" group
- WHEN a delete is attempted
- THEN it is REFUSED; deleting a normal group re-homes its categories to "Others" (never orphaned); category order = the group's `categories` list order.

### BUD-ACTIVITY-BASE ✅
- GIVEN spending in a non-base currency within a category
- WHEN activity is computed
- THEN activity is the BASE-currency amount (via `txn_base_amount`), never the raw native leg.

### BUD-SPLIT-EXPENSE ✅
- GIVEN a split expense with per-category lines
- WHEN activity is computed
- THEN each split line lands in its own category's activity (Σ split lines == the leg), ignoring the top-level "split" sentinel.

### BUD-SPLIT-INCOME-RTA 🧪 (B3)
- GIVEN a split income with a spending-category line + an RTA-category line (e.g. 200 → food, 1000 → uncategorized)
- WHEN RTA and activity are computed
- THEN the spending line is a refund to that envelope (+200 food activity) AND the RTA-category line reaches RTA (+1000) — no money dropped.

### BUD-AUTO-ASSIGN ✅
- GIVEN auto-assign modes (underfunded / assigned_last_month / spent_last_month / averages / cover_overspending / reset_assigned)
- WHEN previewed and applied
- THEN each produces the documented assignment. BOTH `underfunded` and `cover_overspending` cap their total at the DISPLAYED Ready-to-Assign (rule B4, restored by ADR `2026-09-09-ready-to-assign-follows-ynab-envelopes-are-the-category`): under the restored identity an envelope HOLDS its own overspend, so covering one genuinely costs Ready to Assign dollar for dollar up to the hole's size, and auto-assign must never propose funding money that does not exist. A red row is topped up to exactly 0, in row order, until Ready to Assign runs out; a payment envelope is never proposed at all (it is funded by coverage, not by an assignment). Pinned by `tests/test_planning_service.py::test_auto_assign_preview_cover_overspending_is_capped_at_rta` and `tests/test_credit_card_payment_envelope.py::test_cover_overspending_skips_payment_envelopes_and_costs_exactly_the_hole`.

## Ready to Assign follows YNAB — an envelope holds its own overspend until month-end (ADR `2026-09-09-ready-to-assign-follows-ynab-envelopes-are-the-category`, restoring ADR 2026-07-02 + 2026-07-09, superseding 2026-09-08)

**The on-budget OVERRIDE this section originally covered (`BUD-ONBUDGET-*` below) was
RETIRED by ADR `2026-09-07-seven-account-types-one-axis`, not merely re-skinned:
on-budget is now TYPE-ONLY (`cash` + `credit`, no exceptions) with no per-account
override at all — the account modal has no "On budget" control, tri-state or
otherwise, and the API has no `on_budget` field to PUT. Left below, marked RETIRED,
so a future reader doesn't rediscover the same dead end (`account-modal-redesign.md`
AM-4/AM-6 and `account-taxonomy.md` AT-1..AT-5 are the current criteria for this
area).**

### BUD-RTA-1 🧪
- GIVEN cash of $1,000.00, baseline $0, no assignments this month
- WHEN a $60.00 grocery run posts with NO category at all (Uncategorized — no envelope, no bucket to hold it)
- THEN Ready to Assign drops from $1,000.00 to $940.00 the instant that spend posts — there is no envelope to hold it, so it drains immediately (the one fix carried over from the pre-2026-09-08 engine, which had wrongly held Uncategorized spend like a mini-envelope). Categorizing or linking that spend to an envelope AFTERWARD changes only WHICH envelope's `available` absorbs the activity going forward — Ready to Assign does not move again for this spend.
- AND GIVEN the same $60.00 instead posts to a real, labeled category that is simply never linked to any envelope, THEN it drains identically — one rule for "no bucket," whether the category is blank or just unlinked.
- AND GIVEN the same $60.00 instead posts on a CREDIT CARD with no linked envelope, THEN Ready to Assign is UNCHANGED — it does not move, this month or ever. A card never had cash to lose, so its debt is never Ready to Assign's business: with no envelope row there is no drain to cancel, so `_capped_card_credits` yields no `_CARD_UNCOVERED_KEY` term at all and nothing in the identity reflects the swipe. This is exactly the treatment a card spend on a LINKED-but-unfunded envelope gets (BUD-RTA-3) — reached by absence rather than by cancellation — so there is no linked-vs-unlinked distinction on the card side. The $60.00 surfaces only via that card's own `underfunded` figure, and touches Ready to Assign solely by being FUNDED (assigning into the card's payment envelope). "No bucket drains immediately" is a CASH-only rule: it applies to the two bullets above because real cash left the account, and to nothing on a card. (Supersedes the 2026-09-09 "drains once, then resets" wording, which described a bare subtraction that silently un-subtracted itself at the next month's roll — commit `5a7cc3984`, found by the 2026-09-09 security review; pinned by `tests/test_credit_card_payment_envelope.py::test_uncategorized_card_spend_has_no_pool_and_is_rta_neutral`.)

### BUD-RTA-2 🧪
- GIVEN an envelope Travel, assigned $0, cash $1,000.00 before any spend
- WHEN a $580.00 flight posts to Travel, taking its `available` to −$580.00
- THEN Ready to Assign does NOT move because of that spend relative to the same spend held by a funded envelope — cash drops to $420.00 via `cash_as_of`, and Travel's now-negative `available` (−$580.00) is subtracted right back out of the identity, so RTA still reads $1,000.00 (an overspent envelope holds its own hole; it does not charge RTA in-month).
- WHEN month m+1 is computed with Travel STILL at −$580.00 (no assignment, no cover before the roll)
- THEN Travel's `available` resets to $0.00 in m+1 (the clamped carry, `max(0, prev)`) and m+1's Ready to Assign is reduced by exactly $580.00 at the roll — the hole is charged to NEXT month, it does not carry forward as debt forever.
- GIVEN instead that $580.00 is assigned to Travel in the SAME month m, covering it before the roll
- THEN Ready to Assign falls by $580.00 the moment that assignment posts (from $1,000.00 to $420.00) — covering an overspent envelope costs Ready to Assign, dollar for dollar, up to the hole's size; `_auto_cover_overspending` is capped at the displayed RTA so it can never propose more than that.

### BUD-RTA-3 🧪
- GIVEN a credit-card account with a $200.00 spend in an envelope funded for $120.00 (covered $120.00, uncovered $80.00)
- WHEN Ready to Assign is computed
- THEN the covered $120.00 is RTA-neutral — the set-aside relocates from the spending envelope into the card's payment envelope, fully funding that portion of the debt — AND the uncovered $80.00 is ALSO RTA-neutral: `_CARD_UNCOVERED_KEY`/`card_spend_not_covered` cancels the spending envelope's own drain for that $80.00, so nothing is charged now and nothing is re-charged at rollover (real debt that hasn't cost cash yet, never manufacturing assignable cash out of it).
- AND GIVEN that card instead carries $500.00 of PRE-EXISTING/standing debt with no funding at all, THEN Ready to Assign is unaffected by it — the card's payment envelope starts at $0.00, and the standing debt sits there, untouched, until the user assigns toward the payment envelope over time (the "pay a card off over time" capability this restore exists to support).
- AND the payment row carries `owed` (the card's real debt), `available_for_payment` (raw signed) and `underfunded = max(0, owed − max(available_for_payment, 0))`, all three `null` on every other row.

### BUD-RTA-4 🧪
- GIVEN any month at/after the budget floor
- WHEN `GET /api/planning/month` returns
- THEN `ready_to_assign == cash_as_of − pre_budget_baseline − held_in_envelopes − card_spend_not_covered` exactly, where `held_in_envelopes` is the SIGNED Σ of every envelope's `available` (NOT clamped per-envelope — an overspent envelope contributes a negative term) and `card_spend_not_covered` is this month's uncovered card-spend activity, never a standing debt total. `RTA ≤ cash_as_of − pre_budget_baseline` is NOT an invariant any more — e.g. cash $420.00 after a $580.00 overspend Travel is holding yields RTA $1,000.00 (BUD-RTA-2), above cash on hand. In its place, the identity closes EXACTLY on every generated book: `ready_to_assign + held_in_envelopes + card_spend_not_covered + pre_budget_baseline == cash_as_of` to the cent. A month BEFORE the budget floor reports `ready_to_assign`, `held_in_envelopes` and `card_spend_not_covered` all 0 — the identity is gated at the floor. The RTA popover renders these terms as its rows (BUD-RTA-BREAKDOWN-LINES-EXACT). (Lock: `tests/test_rta_balance_identity.py`, `tests/test_rta_identity_property.py` — the retired `RTA <= cash` property test is replaced by the closed-identity assertion on 200 generated books.)

### BUD-RTA-COACHING 🧪 (the copy the restored identity forces)
- GIVEN Ready to Assign is negative (`< -0.005`) AND `held_in_envelopes > 0.005` — money IS sitting in envelopes, so moving some back is a real, available remedy
- THEN the insight ribbon (`budget.BudgetInsight`) reads "Over-assigned by $X — move money back or unassign to get Ready to Assign to zero."
- GIVEN instead Ready to Assign is negative AND `held_in_envelopes <= 0.005` — nothing is there to move back, so the shortfall is a CASH gap, most often last month's overspend clamped to 0 at the roll and charged here
- THEN the ribbon reads "Ready to Assign is short by $X — nothing is held in your envelopes to move back, so this is likely a rolled-over overspend charged this month. It clears as income arrives, or you can leave it." It must NOT say "over-assigned" or "unassign": under the restored identity a negative Ready to Assign has two causes and only one of them is over-assignment, and naming an action with no target is worse than naming none (Lock: `budget.insight.test.tsx`)
- GIVEN an overspent envelope and the one-click "Cover $X" action (the row button, or the target panel's callout)
- WHEN it fires — immediately, with NO confirm dialog, by design: it is undoable, and a dialog on every cover would cost more than a sentence does
- THEN its cost in Ready to Assign is stated plainly WITHOUT a hover: the target panel's callout reads "Cover $X to get back to green — it comes out of Ready to Assign", and the toast leads with the price rather than the reassurance — "−$X from Ready to Assign — {envelope} is covered", never a bare "Covered {envelope} • $X". Covering costs Ready to Assign dollar for dollar under ADR `2026-09-09-ready-to-assign-follows-ynab-envelopes-are-the-category` (BUD-RTA-2), the exact reverse of the one-day rule where it was free (Lock: `useBudgetModel.cover.test.tsx`)

### BUD-ONBUDGET-DEFAULT-BY-TYPE ⛔ RETIRED (2026-09-07 — the field itself is gone)
- Described a two-way "On budget"/"Off budget" toggle, preselected per type, on the account
  modal. ADR `2026-09-07-seven-account-types-one-axis` removed `on_budget` entirely — the
  modal has no such control at all, and on-budget membership is 100% derived from `type`
  (`cash`/`credit` on-budget, everything else off), with no default to preselect and nothing
  to override. See AM-4/AM-6 in `account-modal-redesign.md`.

### BUD-ONBUDGET-DEFAULT-FOLLOWS-TYPE-PICK ⛔ RETIRED (2026-09-07)
- Same reason as above — there is no toggle left to re-preselect when the type changes.

### BUD-ONBUDGET-OVERRIDE-EXPLICIT ⛔ RETIRED (2026-09-07)
- Same reason as above — there is no toggle to flip and no `on_budget` field to PUT.

### BUD-ONBUDGET-EDIT-PRESERVES-DERIVE-WHEN-UNTOUCHED ⛔ RETIRED (2026-09-07)
- Same reason as above — there is no derive-mode/override distinction left to preserve.

### BUD-ONBUDGET-TRISTATE-API-UNCHANGED ⛔ RETIRED (2026-09-07)
- Same reason as above — the API has no `on_budget` field, tri-state or otherwise, to send
  `true`/`false`/`null` to.

### BUD-ENV-1TO1-NOCHANGE 🧪 (P4)
- GIVEN a book where every envelope holds exactly 1 category (default post-migration)
- WHEN the Plan → Budget table renders
- THEN every row is pixel-identical to today: no badge, same kebab items/order, Assigned/Activity/Available match the pre-flip numbers exactly.

### BUD-ENV-BADGE 🧪 (P4)
- GIVEN an envelope holding 3 categories
- WHEN its row renders
- THEN it shows the envelope's name + a "3 categories" badge (`.pl-gcount`) whose tooltip lists the member labels.

### BUD-ENV-MERGE 🧪 (P4)
- GIVEN two 1:1 envelopes A (assigned S$50) and B (assigned S$30) in the same month
- WHEN A's kebab → "Merge into…" → B → "Merge envelopes"
- THEN B holds both categories, B's assigned becomes S$80 that month (and every month with nonzero A), A is archived (not deleted) and disappears from the table.

### BUD-ENV-MERGE-EXCLUDES-SELF 🧪 (P4)
- GIVEN any envelope row's merge picker
- THEN it never offers the row's own envelope nor any envelope in the locked/Commitments-synced group.

### BUD-ENV-MERGE-NO-TARGETS 🧪 (P4)
- GIVEN a book with exactly one budgetable envelope
- THEN "Merge into…" is omitted entirely (not disabled).

### BUD-ENV-SPLIT 🧪 (handoff — unlink semantics flip, supersedes the P4 "split into a new envelope" behavior)
- GIVEN an envelope {Dining out, Coffee, Takeout} with assigned S$400
- WHEN "Remove from envelope" → "Coffee"
- THEN Coffee becomes envelope-less (surfaces in the Outside-envelopes → Unbudgeted section, relinkable any time via "Add to envelope…"); NO new envelope is minted; the source keeps {Dining out, Takeout} + the FULL S$400 (money never moves); no transaction or historical assigned amount changes.

### BUD-ENV-SPLIT-TO-1TO1 🧪 (P4)
- GIVEN an envelope holding exactly 2 categories
- WHEN one is unlinked
- THEN the remaining envelope renders as a plain 1:1 row (no badge).

### BUD-ENV-UNLINK-1TO1 🧪 (handoff)
- GIVEN a 1:1 envelope (its only linked category)
- WHEN "Remove from envelope" is used on it
- THEN this is now LEGAL (the old sole-member refusal is dropped — a 1:1 row gets the affordance too): the category becomes envelope-less/Unbudgeted, the envelope survives zero-category — keeping its pool, still visible in the table (dashed zero-category hint) — and is never archived by this action.

### BUD-ENV-DELETE 🧪 (handoff)
- GIVEN a non-synced envelope (1:1, merged, or empty) with assigned money and/or linked members
- WHEN "Delete envelope…" is confirmed
- THEN every member unlinks (→ Unbudgeted, never deleted), and the envelope's `available` — across EVERY month it existed — leaves the identity's signed sum exactly as covering it any other way would: a POSITIVE `available` releases to Ready to Assign (RTA rises by that amount); a NEGATIVE `available` (an unresolved overspend) COSTS Ready to Assign up to its size, same as covering it any other way — removing a negative term from `held_in_envelopes`' signed sum is arithmetically identical to assigning money to zero it out (ADR `2026-09-09-ready-to-assign-follows-ynab-envelopes-are-the-category`, restoring `2026-07-02`/`2026-07-09`) — its target/note clear, and it archives with `archive_reason="deleted"` + `archived_at` — restorable, never destroys a category. Refused for an already-archived (422) or Commitments-synced (422) envelope.

### BUD-ENV-RESTORE 🧪 (handoff)
- GIVEN an archived envelope (deleted or merged-away) listed in the Archived-envelopes footer
- WHEN "Restore" is confirmed
- THEN it un-archives EMPTY (its stored group if that group still exists, else Others, placed last) — it NEVER re-funds the assigned money a delete zeroed — and re-links each former member category that is STILL unlinked (one already re-homed to a different envelope in the meantime is left alone). A merge-archived source's former members are typically already linked to the merge target, so its restore comes back with zero relinks (by design).

### BUD-MOVE-RTA 🧪 (handoff)
- GIVEN the Move/Cover picker with "Ready to Assign" as the first option (group "Budget")
- WHEN money moves TO Ready to Assign from an envelope
- THEN the source's assigned decreases, CAPPED at the row's AVAILABLE this month — not at what's assigned there (ADR `2026-07-04-move-money-caps-at-available-not-assigned`: carried-over money is movable, so assigned may go negative); refused past that, a typed validation error. Ready-to-Assign rises by the released amount, minus whatever the release re-exposes: pulling out of a card's PAYMENT envelope lowers `funded`, so up to the same amount of the card's debt reads as unfunded again and RTA rises only by the net (0 for a fully-covering release).
- WHEN money moves FROM Ready to Assign INTO an envelope
- THEN the destination's assigned increases with NO cap (Ready-to-Assign may go negative — exactly like a direct assign — the over-assigned coaching copy already covers this case). Both ends being "Ready to Assign" simultaneously is refused (nothing to move).

### BUD-TARGET-DUE 🧪 (handoff)
- GIVEN a weekly target with `due_weekday` set (e.g. Saturday=5)
- WHEN "needed this month" is computed
- THEN it uses the COUNT of that weekday in the month (not always Monday) × the amount; an UNSET `due_weekday` reproduces the original Monday-only count exactly (byte-identical regression — no drift for existing books).
- GIVEN a monthly target with `due_day` (1-28, or `-1` = last day of the month) or a yearly target with an optional `date`
- THEN the field is stored + echoed on the row for display ("By the 15th" / "By last day of month" / "By 15 Mar") with NO effect on the underlying math; a field paired with the wrong target type (e.g. `due_day` on a weekly target, `due_weekday` on monthly, `date` on monthly/weekly) is refused with a validation error.

### BUD-CATMGR-LIST 🧪 (P4)
- GIVEN the "Manage categories" modal opened from the Budget toolbar
- WHEN it loads
- THEN every non-deleted category lists color/label/kind/taxable/hidden + envelope membership ("Not budgeted" when unmapped); a rename here shows in the Budget table next load.

## Ready-to-Assign breakdown popover (ADR `2026-09-08-ready-to-assign-is-unassigned-cash`)

The popover renders the identity's own terms and nothing else. Four criteria of
`.claude/rfcs/rta-breakdown-popover-spec.md` v2 are **⛔ RETIRED** with the flow model they
described — their subjects no longer exist anywhere in the UI:
`BUD-RTA-BREAKDOWN-PREV-LINE` (there is no previous-month line — nothing carries in, the
terms are all as-of-this-month), `BUD-RTA-BREAKDOWN-RECONCILER` (there is no residual to
reconcile — see NO-RESIDUAL below, which replaces it with the opposite assertion),
`BUD-RTA-BREAKDOWN-OVERSPEND-COACHING` (an overspent envelope no longer charges Ready to
Assign, so there is nothing to coach about) and `BUD-RTA-BREAKDOWN-BANNER-MATCH` (the
`.pl-sum-formula` / `RtaFormula` line is gone — the operator-prefixed rows ARE the formula).
`BUD-RTA-BREAKDOWN-FUTURE-INFO` is FOLDED into LINES-EXACT rather than retired: "Assigned in
future months" is a real subtracted row now, not a faint footnote below the total.

### BUD-RTA-BREAKDOWN-OPEN 🧪
- GIVEN the Plan → Budget sub-tab has loaded a month with `on_budget_cash != null`
- WHEN the RTA hero figure (a `<button>`) is clicked, or Enter/Space is pressed while it's focused
- THEN a popover opens anchored to the hero, titled "Ready to Assign • {month}", and the trigger's `aria-expanded` flips to `true`
- AND GIVEN `on_budget_cash` is null (the month hasn't loaded), the hero renders as a plain, non-interactive figure with no trigger at all — a half-loaded month never reads as a broken identity.

### BUD-RTA-BREAKDOWN-LINES-EXACT 🧪
- GIVEN the popover is open
- WHEN its rows render
- THEN they are ONE flat list in this order, each with an operator glyph and a penny-exact amount (`fBaseCents`): `+ Cash on hand` == `on_budget_cash`, `− Held in envelopes` == `held_in_envelopes`, `− Card spending not covered this month` == `card_spend_not_covered`, `− Baseline` == `pre_budget_baseline`, `− Assigned in future months` == `assigned_in_future_months` — cash and held ALWAYS render (they are the identity's backbone; at 0, "Held in envelopes" IS the answer, not noise), the last three each ONLY when `|value| >= 0.005` — then a divider and a bold `= Ready to Assign` total == `ready_to_assign`
- AND the glyph follows the term's ACTUAL sign, not its structural one (`rtaLines.glyph`): `held_in_envelopes` is SIGNED under ADR `2026-09-09-ready-to-assign-follows-ynab-envelopes-are-the-category`, so a month whose envelopes are net overspent renders `+ Held in envelopes` — held overspending genuinely lends room back until the roll — while `−` remains the structural default at exactly 0
- AND when that `+` is showing (`held_in_envelopes < -0.005`) ONE inline line renders DIRECTLY under that row (`.pl-rtapop-note`, `budgetRta.HELD_CREDIT_NOTE`): "Envelopes are net overspent this month — that lends Ready to Assign room back until the roll charges the hole to next month." — absent otherwise. It is the only term whose glyph can contradict its own label, so it is the only one carrying a reason; INLINE, never a hover tooltip, because the sign is on screen for everyone and so its explanation must be. It never mentions card spending: an unfunded swipe is RTA-neutral whether or not an envelope is linked to it, so it can never produce this credit
- AND the sign is spelled ONCE, in the glyph column: every amount renders UNSIGNED (`fBaseCents(|value|)`), so no row prints two minuses for one fact
- AND colour marks STATUS, never arithmetic: a plain subtraction keeps the default ink (money properly held in an envelope is the healthy state, not a problem), `− Card spending not covered this month` takes the warn tone, and only the `= Ready to Assign` total is tinted by sign (`--good` / `--bad`)
- AND the fresh-start dialog's ledger (BUD-FRESH-1) renders the SAME rows from the SAME builder (`rtaLines.buildRtaLines`) — same order, same labels, same hideability, same glyph column, same colours
- AND the rendered rows sum EXACTLY to that total (this is BUD-RTA-4's payload identity, shown).

### BUD-RTA-BREAKDOWN-NO-RESIDUAL 🧪 (replaces BUD-RTA-BREAKDOWN-RECONCILER)
- GIVEN any month at or after the budget floor, on any book — including one with overspent envelopes, unlinked-category spending, uncategorized spending, off-budget transfers and card debt (a month strictly before the floor reports Ready to Assign 0 with `held_in_envelopes` and `card_spend_not_covered` zeroed alongside it, and is unreachable from the Budget tab)
- WHEN the popover renders
- THEN there is NO "Unexplained — refresh" row and no "Investing & other movements" row: the identity closes by construction, so a residual can only mean a defect. An `|residual| > 0.01` renders an "Unexplained — refresh" row in the warn tone AND logs a console warning carrying the residual ALONE — never the balances behind it, so privacy mode and a screen-share are not bypassed via devtools. That row's appearance is a FAILURE of this criterion, never an explanation, and its label has to read as an anomaly rather than as one more term of the identity (the old "Other" read exactly like "Baseline" and gave the user nothing to act on).
- AND the warning does not fire on a SINGLE render either, on load or on month nav — "it self-heals before paint" is not a pass. All four terms come from ONE state update by construction: `on_budget_cash`, `card_spend_not_covered`, `pre_budget_baseline` and `assigned_in_future_months` are read straight off the payload, `held_in_envelopes` is the payload's own plus `heldOffset` (what our un-refetched writes have moved), and `ready_to_assign` is DERIVED as the payload's less that same offset — so the offset cancels out of the subtraction whatever its value and no render can hold a mixed pair. Kept honest by `budget.rtaFreshPayload.test.tsx`, which drives the REAL `useBudgetModel` (mocking the hook hands the component an already-consistent figure and cannot see this).

### BUD-RTA-BREAKDOWN-QUICK-ACTION 🧪
- GIVEN Ready to Assign is positive (> 0.005) and at least one row has status `underfunded`
- WHEN the popover renders
- THEN a primary "Fund {N} underfunded target(s)" button appears below the total and opens the SAME auto-assign preview as the toolbar's "Fund all targets" (closing the popover first); it is absent when RTA is zero/negative or nothing is underfunded
- AND GIVEN `has_foreign_cash`, an informational footer "Ready to Assign can shift as exchange rates move" renders — a note, never an amount.

### BUD-RTA-BREAKDOWN-DISMISS-AND-MASK 🧪
- GIVEN the popover is open
- WHEN the user clicks outside, presses Escape, resizes the window, or scrolls outside the popover
- THEN it closes and focus returns to the trigger button; a click INSIDE the popover never closes it.
- GIVEN privacy mode is ON (`body.hide-amounts`)
- WHEN the popover renders
- THEN every amount (including its sign) is masked via `.mask` — the hero, every term row and the total; a negative total shows a literal "−" plus the negative tint.

## RTA popover flow framing (ADR `2026-09-13-rta-popover-flow-framing`)

### BUD-RTA-BREAKDOWN-FLOW-LINES-EXACT 🧪
- GIVEN a month strictly after the carry floor (`flow` is non-null on the wire)
- WHEN the popover renders
- THEN its rows are "Ready to Assign Left Over from {PrevMonth}", "Inflow: Ready to Assign Transactions in {Month}", "Assigned in {Month}", and — only when non-zero — "Cash Overspending in {PrevMonth}" and "Assigned in future months"
- AND every visible row's value sums exactly (penny-exact) to `ready_to_assign`, with no "Unexplained — refresh" row.

### BUD-RTA-BREAKDOWN-PREV-CARRY-AND-OVERSPEND 🧪
- GIVEN the previous month has unresolved cash overspending (`flow.cash_overspending_prev > 0.005`)
- WHEN the popover renders
- THEN "Ready to Assign Left Over from {PrevMonth}" AND "Cash Overspending in {PrevMonth}" both render as SEPARATE lines (never folded into one) in their own sections (Added / Deducted), and the pointer note appears beside the "Left Over" line
- AND GIVEN `cash_overspending_prev == 0`, THEN the "Cash Overspending" line and its note are both absent.

### BUD-RTA-BREAKDOWN-CARD-NEVER-CHARGED 🧪
- GIVEN the previous month had ONLY unfunded CARD spending (no cash overspend on any row)
- WHEN this month's popover renders
- THEN `flow.cash_overspending_prev == 0` and no "Cash Overspending" line renders — a card's own debt is never charged to Ready to Assign at the roll (the `cash_overspending ≥ 0` theorem, `docs/INVARIANTS.md`).

### BUD-RTA-BREAKDOWN-FIRST-MONTH 🧪
- GIVEN the requested month is at or below the carry floor (`flow` is null on the wire)
- WHEN the popover renders
- THEN it falls back to the STOCK rows (`buildRtaLines`, unchanged) with a "First month of your plan — nothing carried in." subline, and shows no Added/Deducted section headers.

### BUD-RTA-BREAKDOWN-HELD-NOW-NOTE 🧪
- GIVEN this month has open cash overspending (`cash_overspending > 0.005`)
- WHEN the popover renders
- THEN a footnote reads "{amount} is held until month-end — it's charged to {NextMonth} if it's still uncovered."
- AND, driving next month's popover, its "Cash Overspending in {ThisMonth}" line equals THIS footnote's amount exactly — the promise and its receipt.
- AND GIVEN privacy mode is ON, THEN this footnote's amount masks the same way every other row's amount does (security review finding, 2026-09-13 — the figure was originally bare text, not wrapped in `.money.mask`).

### BUD-RTA-BREAKDOWN-HIDE-AT-ZERO 🧪
- GIVEN `flow.cash_overspending_prev == 0` and `assigned_in_future_months == 0`
- WHEN the popover renders
- THEN both "Cash Overspending in {PrevMonth}" and "Assigned in future months" are absent — hide-at-zero, matching the stock builder's existing convention.

## Spending outside envelopes (ADR `2026-09-07-budget-available-is-the-display-total`)

### BUD-OUTSIDE-1 🧪
- GIVEN a month with BOTH uncategorized spending (the derived `__unsorted__` row) and at least one labeled category carrying spending but no linked envelope (the derived `__unbudgeted__` group, server status `unlinked`)
- WHEN the Budget sub-tab renders
- THEN the banner above the table (`.pl-uncat`) headlines the COMBINED figure — "{|unsorted| + Σ unlinked spending} of spending isn't in any envelope — it already came out of Ready to Assign." — followed by a compact `.pl-uncat-list` breakdown ("{amt} has no category · {amt} in {Category}, a category with no envelope"), naming at most the three largest unlinked categories and appending "+N more" beyond that; each named category gets an inline "Add {Category} to an envelope →" button that OPENS (never writes) the SAME `AddToEnvelopeModal` → `linkCategoryToEnvelope` flow the Unbudgeted rows use — never a second picker — and "Review with Lucius" is unchanged. Every amount in the banner uses `fSmart` (cents when fractional), so a small category never renders as "{sym}0".

### BUD-OUTSIDE-2 🧪
- GIVEN a month with unlinked-category spending but NO uncategorized spending
- WHEN the banner renders
- THEN it reads "{amt} of spending is in categories that have no envelope: {names}. It already came out of Ready to Assign." with no `.pl-uncat-list` breakdown; and GIVEN the inverse (uncategorized spending only) it reads "{amt} of spending has no category — it already came out of Ready to Assign; give it a category any time to sort it in reports." with no link buttons; and GIVEN neither (and no uncategorized income) the banner is absent entirely. EVERY expense-side branch closes the Ready-to-Assign loop the income branch always did: the old "isn't counted in any envelope yet" read as PENDING, inviting a user to categorize a spend expecting Ready to Assign to move back — it won't, the cash already left. The trigger is per source — `|unsorted| > 0.005` OR any single unlinked category with `activity < -0.005`. Only SPENDING counts: an unlinked category with positive activity (a refund) is neither listed nor netted against one that spent, so the headline can never come out smaller than the lines beneath it.

### BUD-OUTSIDE-3 🧪
- GIVEN a month with spending that belongs to no envelope (Uncategorized and/or Unbudgeted)
- WHEN the Summary's "Total available" stat, the Summary mini "… available" line, the table's Total row and the Lucius "Total available" screen fact render
- THEN ALL FOUR show the PLAIN Σ of every envelope's Available, negatives included — outside spending is NOT folded in (ADR `2026-09-08-ready-to-assign-is-unassigned-cash` supersedes `2026-09-07-budget-available-is-the-display-total`); it is computed live from the model so an optimistic assign moves it immediately
- AND neither the stat nor the mini line carries a tooltip: the number means one thing and needs no footnote
- AND the spending outside every envelope is named ONLY by the `.pl-uncat` banner, which is informational — it never claims to change Ready to Assign (the cash it spent already did).

## Starting fresh (ADR `2026-09-08-budget-fresh-start-moves-the-floor-not-the-data`)

### BUD-FRESH-1 🧪
- GIVEN the Plan → Budget sub-tab with no carry floor set (`budget_config.rollover_start` is null)
- WHEN the "…" menu on the Budget tab row (desktop: `.pl-mast-tools`, beside the lens switcher; on a phone: the page header's overflow menu) is opened
- THEN it offers "Start fresh from {Month YYYY}…" naming the CURRENT calendar month, formatted exactly as the month nav does ("September 2026") — never the browsed month
- AND WHEN that item is selected
- THEN a confirm dialog titled "Start fresh from {Month YYYY}?" opens, showing a skeleton in place of the amount until `GET /api/planning/fresh-start-preview?ym=` returns and keeping "Start fresh" DISABLED until then (an inline error state plus a still-disabled "Start fresh" if that read fails)
- AND THEN the dialog reads "Ready to Assign becomes {amount}:", with the amount masked (`.mask`) and rendered to the CENT (`fBaseCents`, the same precision as the hero) so it equals `GET /api/planning/month?ym=` → `ready_to_assign` after confirming exactly — the preview is the real fold at the hypothetical floor WITH the month's assignments treated as zero (both halves of the write), not an estimate
- AND under it a ledger (`.pl-fresh-ledger`, holding the popover's OWN `.pl-rtapop-row` rows — tabular figures, every amount masked) shows that figure's OWN arithmetic and ADDS UP to it to the cent, EXACTLY, on every book. It is not merely the same identity as the RTA popover (ADR `2026-09-09-ready-to-assign-follows-ynab-envelopes-are-the-category`) but literally the same rows, from the same builder (`rtaLines.buildRtaLines`) — same order, labels, hideability, glyph column and colours (BUD-RTA-BREAKDOWN-LINES-EXACT): "Cash on hand" (`cash_now`, on-budget cash as of the END of the month itself) and "Held in envelopes" (`held_in_envelopes`) ALWAYS render — the write clears the month's assignments, so each row reduces to its OWN month's activity, which makes this term NEGATIVE on any book with a budgeted spend in the cleared month (the envelope holds its hole until the roll), POSITIVE for a same-month refund or a same-month covered card swipe, and 0 only when nothing touched an envelope at all — then, each hidden when zero, "Card spending not covered this month" (`card_spend_not_covered` — THIS month's uncovered card spend, never a standing debt: a card balance carried in from an earlier month contributes nothing), "Baseline" (`baseline`) and, ONLY when a later month's own assignments clamp this month's figure, "Assigned in future months" (`assigned_in_future`). Every row's glyph follows the term's actual sign and its amount renders UNSIGNED; there is no residual term — the identity closes by construction, not because it happens to on the books tried
- AND there is NO "Already assigned" row at any value — a fresh start CLEARS the month's assignments, so nothing assigned FOR THIS MONTH is left for the promised figure to be net of
- AND one closing paragraph discloses every consequence: "Envelopes start over from {Month} — this month's assignments are cleared. This month's spending still shows in its envelopes. Earlier months hide until you resume history. No transactions change — undo any time."

### BUD-FRESH-2 🧪
- GIVEN the dialog is confirmed
- WHEN the Budget sub-tab re-renders
- THEN a muted one-liner "Fresh start · since {Mon YYYY}" appears under the Ready-to-Assign hero (`.pl-sum-fresh`), naming the STORED floor month abbreviated; it is absent entirely while `rollover_start` is null
- AND every envelope's carry-in from earlier months is 0 AND every Assigned cell in that month reads 0 (`POST /api/planning/fresh-start` clears them through `apply_budget_change`, in the SAME transaction that moves the floor — half-applied is impossible), while that month's own spending still shows in its envelopes, now overspent since their set-aside was cleared; no transaction and no envelope was changed
- AND month navigation is bounded at the fresh-start month: `nav.earliest` equals it and `nav.can_go_back` is false, so the Previous-month button is disabled (the deliberate, disclosed side effect — the floor IS the nav bound; nothing is deleted and it returns on resume)
- AND the menu item now reads "Started fresh from {Month YYYY}" (past tense, no ellipsis) and its dialog offers "Resume earlier history", which sets `rollover_start` back to null (`PUT /api/planning/budget-config`) and restores both the full-history Ready to Assign and `nav.can_go_back` — the FLOOR ONLY. Its body says so verbatim: "…Assignments cleared by the fresh start are not restored." Undo, not Resume, is what brings them back.

### BUD-FRESH-3 🧪
- GIVEN a fresh start was just confirmed
- WHEN the undo pill ("Budget starts fresh from {Month YYYY} · Undo" — one line, since `.pl-undo` spans the page gutters at ≤640px rather than being capped at half the viewport) is clicked within its 7-second window
- THEN `POST /api/planning/fresh-start/undo` fires with `{token}` — the one-shot token the fresh-start response returned, and NOTHING else; the before-image stays server-side (`plan_state.payload["budget"]["fresh_start_undo"]`) and the client derives no inverse of its own — restoring the PREVIOUS `rollover_start` (null, or the earlier month it was already set to — never a blanket clear) AND every cleared assignment, in one transaction. The pill is consumed, and Ready to Assign, every Assigned cell and the state line return exactly to their pre-confirm values (the `budget_assignments` table is byte-identical)
- AND that route is NOT a bulk-assign back door: it is the only path that relaxes `_op_assign`'s negative-amount guard, so a body carrying its own `assignments`/`rollover_start` is ignored and the stored before-image wins; a stale/unknown token, or a second undo, is a **409 that writes nothing**
- AND a double-click sends ONE write, not two
- AND a `rollover_start` in the FUTURE is refused — at `services.budgets.write_budget_config`, the single writer, so the REST route 4xxs AND the `set_spending_limit_rollover_start` AI tool returns `{"ok": false}` (a floor ahead of today reads every visible month as zero); a month before the first transaction is accepted (that is just "carry everything").

---

**Flow is "rock-solid / lockable" when:** every ID is PASS, the regression suite
(`tests/test_budgeting_acceptance.py`, B5) is green in `make check`, and the owner has signed
off on the real budget numbers in the running app.
