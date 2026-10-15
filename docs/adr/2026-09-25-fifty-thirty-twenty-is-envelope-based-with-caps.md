---
number: 2026-09-25
title: 50/30/20 rows are envelopes with per-month caps, not a category mapping
status: Accepted
date: 2026-09-25
deciders: orchestrating Claude agent (owner's design) + repo owner
---

# 2026-09-25. 50/30/20 rows are envelopes with per-month caps, not a category mapping

## Context

The 50/30/20 engine (`services/budget_engines/proportional*.py`, id `proportional`, RFC
`2026-08-22-pluggable-budget-engines.md` Decision H) shipped as a pure fold over CATEGORIZED
expense + income: its only owned state was `{targets: {needs, wants}, mapping: {category_id:
"needs"|"wants"|"skip"}}`, and its `ViewModel` rows were whichever categories had spend that
month. It had no per-line ceiling — nothing stopped a category from blowing past 50% of income
with no signal until the month closed — and its rows didn't line up with the Zero-Based table's
own rows (categories vs. envelopes), so a book using both lenses saw two different groupings of
the same spend.

The owner's 2026-09-25 design moves 50/30/20 onto the SAME row identity Zero-Based already uses
— the envelope, from the shared `envelopes` store (`docs/adr/2026-07-05-envelope-id-native.md` /
the envelope-category-full-split RFC) — with an optional per-envelope MONTHLY CAP, and measures
income against the owner's own stated monthly figure (`profile.monthly_net_income`) rather than
only ever looking backward at what a category happened to receive. This is a genuine model
change, not an additive feature: the row universe, the activity fold, and the income figure all
change shape, while the v1 category-mapping state must keep reading correctly forever (existing
configured books get zero migration steps).

Concurrently, the owner is removing the separate "Cash Envelopes" method (engine id `envelope`)
from the product — unrelated to this change except that both proportional's redesign and that
removal happened to land the same day; this ADR does not cover that removal.

## Decision

We will rework `proportional` so that:

1. **Rows are envelopes.** Every live, non-archived envelope EXCEPT a credit-card payment
   envelope or a bot-managed (commitment-synced) one (`envelopes_service.
   bot_managed_envelope_ids()` — the same exclusion `envelope.py`'s own draft already used) is a
   row, in the Zero-Based table's own order (group order, then envelope order within the group).
   An envelope with no categories is still a row (a real, empty budget line — not filtered).

2. **A row's bucket resolves in precedence order**: its own explicit `buckets[envelope_id]`;
   else, when the v1 `mapping` has an opinion about at least one of its member categories, the
   bucket that carried the most spend among those MAPPED categories over the last 3 closed
   months (ties — including an all-zero tie — resolve needs > wants > skip); else `"unsorted"`.
   This is what lets an existing configured book (v1 `mapping` only) keep working with zero
   migration: `resolve_bucket` is a pure read-time fallback, never a write.

3. **A cap is a per-envelope, per-month history** (`caps: {envelope_id: [[month, amount], ...]}`)
   — `cap_at(history, month)` reads the LAST entry at-or-before `month` (0/absent = no cap);
   `splice_cap_history` sets a new value from a month ONWARD by replacing every entry at or after
   it with one new entry, so a cap is forward-looking, never a rewrite of a closed month's own
   figure. Setting a cap for a month before the real current calendar month is refused outright.

4. **Income is measured against the owner's stated usual month, not only actuals.** Current or
   future month → `profile.monthly_net_income` (0 if unset); a CLOSED month → what was actually
   received in the counted income categories if that's positive, else the profile figure, else 0
   — reported with `source` so the UI/Lucius can say which. `income_category_ids: null` counts
   every income-kind category; a `no_envelope`/`uncategorized` unassigned-spend pair is reported
   so the UI can subtract it from savings without silently folding it into a bucket.

5. **`compute_month()` is the ONE computation two surfaces share**: the new REST contract (`GET
   /api/planning/proportional/month`, the screen) and `ProportionalEngine.fold()` (`get_budget_
   view`/`GET /api/planning/view`, Lucius and the generic renderer) both build on it —
   `to_view_model()` is a pure reshape, never a second pass over transactions — so the two can
   never silently disagree. The generic `ViewModel`'s `needs`/`wants` groups carry every
   bucketed row (cap/activity/left as `Measure`s, zero-activity rows included now that the row
   universe is fixed); a `skip`/`unsorted` row rides on `Unmapped.rows` instead (a new, additive
   field), `Row.status` naming which bucket.

6. **Every new verb returns only the top-level key(s) it changed**, and
   `services/budget_engines/service.py::apply_verb` SHALLOW-MERGES that delta onto the engine's
   current stored row (`{**own_state, **delta}`) before persisting — extended from a straight
   "persist whatever the engine returns" now that `proportional`'s state has five independent
   keys (`targets`/`mapping`/`buckets`/`caps`/`income_category_ids`). This is what lets
   `set_bucket` (say) leave `caps`/`targets` exactly as they were, and it is why the LEGACY
   `set_config` verb's own validation (`proportional_config.validate_config`) needed NO code
   change at all — it still returns exactly `{targets, mapping}`, and the merge is what keeps
   the rest of the row alive underneath it.

## Consequences

### Positive
- A book using both lenses now sees the SAME rows in both — no more "two different groupings of
  one book's spend."
- A cap gives 50/30/20 a real, forward-only ceiling per envelope, closing the "no signal until
  the month closes" gap, and `copy_targets_to_caps` lets a book already running Zero-Based seed
  every cap from its existing targets in one call instead of re-entering every number.
- Income no longer silently reads as $0 for a month with categorized income still landing after
  the fact — the profile figure gives an honest number even before the month's activity settles,
  while a closed month still prefers what actually happened.
- `fold()` and the REST endpoint sharing one computation makes "Lucius and the screen agree"
  a structural property, not a tested-and-hoped-for one.

### Negative
- `resolve_bucket`'s v1-mapping fallback is now genuinely stateful in effect (it depends on the
  LAST 3 closed months of spend, which drift over time) even though it's implemented as a pure
  read — a book that never explicitly buckets an envelope can see its "unsorted"-adjacent rows
  quietly change bucket month to month as spend patterns shift. Explicitly bucketing (`configure`
  or `set_bucket`) is the escape hatch, and `configure` seeds one for every envelope in one call.
- `copy_targets_to_caps`'s `applied` count rides inside the persisted `budget_engine_state`
  payload alongside the real `caps` delta (the store's payload is opaque JSON, so this is inert
  clutter rather than a schema violation, but it is a deliberate small wart, not an accident).
- The v1 `draft()`/`draft_budget_engine_config` preview stays CATEGORY-level (unchanged) while
  `configure` seeds buckets at the ENVELOPE level via the same underlying heuristic — two entry
  points into one heuristic, which a future reader has to know to look for both.

### Follow-ups
- `docs/PLANNING_MODEL.md` gains the envelope-based 50/30/20 paragraph (this change).
- `CLAUDE.md`'s Docs map already links `PLANNING_MODEL.md`; no separate CLAUDE.md edit needed
  since it never described 50/30/20 by name.
- The frontend's own Plan screen (built in parallel against this exact REST contract) is out of
  this change's scope — verified against the contract text only, not a running UI.

## Alternatives considered

- **Keep rows as categories, add a cap per category** — rejected: it perpetuates the two-lens
  mismatch (Zero-Based rows are envelopes; a merged envelope's categories would need their OWN
  caps summed back up, reintroducing the "double-count trap" the envelope-category-full-split
  RFC already solved once for Zero-Based).
- **Migrate the v1 `mapping` into `buckets` at read time and drop `mapping` from the schema** —
  rejected: `mapping` is user-authored data (possibly from a saved skill or automation), and
  CLAUDE.md's "no backward-compat shims" rule is about not carrying TWO live write paths for the
  same fact, not about refusing to READ old data; `resolve_bucket`'s fallback is a read, not a
  shim, and an explicit `set_bucket`/`configure` is the one-time, one-directional migration path
  a user or `configure` takes when ready.
- **Make `service.py::apply_verb`'s merge deep (recursively merge `buckets`/`caps` dicts too,
  not just the top-level keys)** — rejected: each verb ALREADY computes the fully-merged value
  for the ONE key it owns (`set_bucket` reads the current `buckets` dict and returns the whole
  updated dict); a deep merge at the dispatcher would double the merge logic across two layers
  for no benefit, and would silently do the wrong thing for a verb that means to REPLACE a whole
  sub-object (there is no such verb here, but the shallow contract is the simpler invariant to
  hold in general).
- **Persist `copy_targets_to_caps`'s `applied` count as an unpersisted sibling return** — would
  need the `BudgetEngine.apply_verb` Protocol (shared by every engine) to grow a second, engine-
  reporting-only return channel for the sake of one verb; rejected as premature generalization
  for a cosmetic count.

## Agent provenance

- **Decided by:** orchestrating Claude agent, implementing the repo owner's explicit design
  (rows are envelopes shared with Zero-Based; caps apply from a month onward; income is the
  profile's usual month with actuals for closed months) relayed via the task brief.
- **Lane / branch:** isolated worktree `agent-a9ab3fe657f8d7e9f`.
- **Alternatives weighed:** category-level caps; a v1-to-v2 state migration instead of a
  read-time fallback; a deep per-key merge in the generic verb dispatcher; a second return
  channel for verb-only reporting (all above).
- **Driving artifacts:** the task brief's REST contract (`GET /api/planning/proportional/month`)
  and verb list; `docs/adr/2026-08-23-cash-envelopes-shares-containers-owns-amounts.md` (the
  "envelopes are the shared container, the paradigm owns the money" precedent this ADR follows
  for a second engine); `docs/adr/2026-07-05-envelope-id-native.md`.
- **Why this way (one line):** rows shared with Zero-Based and a read-time (never migrated) v1
  fallback let every existing book keep working unchanged while a new one gets a real per-
  envelope cap and an income figure that doesn't go quiet before a month closes.

## Amendment (2026-09-25): activity follows Zero-Based's eligibility

A follow-up audit found that although this redesign put 50/30/20's rows on the SAME envelopes
Zero-Based uses, its ACTIVITY fold still disagreed with Zero-Based's for that same envelope:
`proportional_fold._category_activity_pass` counted every non-deleted expense/income
transaction in ANY account, with no on-budget filter and no per-account tracking-start filter,
while Zero-Based's `_iter_category_contributions` (via `plan_leg_ok`, née `_plan_leg_ok`) has
always required a leg's account to be a KNOWN plan-source account (`cashflow.is_plan_source` —
`cash`/`credit` only) dated on/after that account's tracking start. A book with any off-budget
account (investment, retirement, a tracking account) or any account with a tracking-start
cutoff therefore saw two different Activity numbers for the identical envelope depending on
which lens was open — and the frontend's Activity popover, which already lists transactions by
Zero-Based's rule for BOTH lenses, listed a transaction set that didn't sum to 50/30/20's own
displayed figure.

**Fix: reuse, don't re-derive.** `planning._plan_leg_ok` is renamed `planning.plan_leg_ok`
(public — no other change to its body) and imported directly into
`services/budget_engines/proportional_fold.py`, which now threads
`plan_source_ids`/`cutoff_by_id` through every fold that walks expense/income legs:
`_category_activity_pass` (and therefore `activity`, `history`'s four variants, and
`no_envelope`, all pure re-slices of its output), `uncategorized_expense`, and
`income_by_category`. `compute_month` reads both from `ctx`, which
`shared_context.resolve_shared` and `service._shared_context` now populate the same way they
already populate `envelopes`/`groups` — each derives `plan_source_ids`
(`is_plan_source`-filtered account ids) and `cutoff_by_id` (`ledger.cutoffs_by_account`,
non-null entries only) from a fresh `accounts.all()` read, mirroring
`planning._build_context`'s own derivation exactly (duplicated rather than extracted into a
shared helper — deliberately: `_build_context` interleaves this with `cash_ids`/
`card_pay_env_of`, which a paradigm-neutral context has no use for, and the derivation itself
is three lines, too small to be worth the extra indirection of touching a load-bearing,
heavily-tested function for). `resolve_shared` keeps its "a fully-formed `shared` short-
circuits every fallback read" guarantee: the account read only happens when
`plan_source_ids`/`cutoff_by_id` is actually missing.

**Income — the one figure without a direct Zero-Based analogue.** Zero-Based has no
"per-category income total"; its `_activity_map` only ever holds envelope-linked categories
and the blank `""` (Unsorted) key, so an income leg posted to an ordinary income-kind category
(Salary, say) never appears in it at all — that money is walked straight into cash/Ready-to-
Assign. What Zero-Based DOES do uniformly is gate every income leg through `plan_leg_ok`
BEFORE deciding whether it's an envelope refund or RTA inflow. 50/30/20's `income_by_category`
(feeding both `income.actual` for a closed month and each income category's `last_month`
figure) is the closest thing this engine has to "money that reached the plan," so it now
applies that SAME gate to the income leg's `to_account_id` — an off-budget deposit, or a
pre-tracking-start paycheck, no longer counts as received income under either method.

**Deliberately UNCHANGED.** A TRANSFER still never counts toward 50/30/20's activity, and an
investment leg still isn't folded at all — both pre-date this amendment and remain out of
scope: this engine has no Ready-to-Assign/balance identity for a transfer's cash side to
reconcile against (the entire reason Zero-Based's XOR-on-on-budget-leg transfer rule and its
card-payment-envelope auto-move exist), so importing that machinery here would be new surface
area this redesign's own "no Ready to Assign, anywhere" framing (see `envelope_fold.py`'s
retired equivalent, and this engine's own module docstring) doesn't call for. A book whose
envelope-categorized spend runs entirely through categorized transfers or investment buys
will still see a gap between the two lenses on that portion — a known, named limitation, not
an oversight.

**Tests.** `tests/test_budget_engine_proportional.py` gained: two direct-fold tests (an
off-budget account's spend excluded; a leg dated before its account's tracking start
excluded), a `compute_month`-level test of the same two exclusions, an `income_by_category`
eligibility test, and one cross-engine equivalence test — a DB-backed book (one on-budget cash
account, one off-budget investment account) where every 50/30/20 envelope's `activity` equals
`planning.budget_month()`'s `activity` for the same envelope, sign-adjusted.
`docs/PLANNING_MODEL.md`'s 50/30/20 paragraph is updated to match.

## Amendment (2026-09-25): `configure` never seeds Excluded

**Owner-reported.** "Why are these excluded by default?" — envelopes the owner never sorted sat
in Excluded, so a month of their real spend was silently left out of the savings residual
(`income − needs − wants − unsorted − unassigned`). `draft_bucket` returns `skip` only as its
"don't know" (a name no keyword matches and a spend history neither clearly steady nor clearly
erratic, or under two active months — always low-confidence), and `configure` seeded that as
a decision.

**Change.** `configure` seeds needs/wants as before but leaves a `skip` answer UNSET, so the row
resolves `unsorted`: To sort asks the owner, and its spend counts against savings until they
pick. Excluded is only ever a human (or Lucius-on-request) choice via `set_bucket`. The
heuristic itself is unchanged — the category-level `draft()` preview still needs a total
mapping. Already-stored buckets are untouched: a seeded Excluded can't be told from a chosen
one, so an existing book re-sorts by hand. Pinned by
`tests/test_budget_engine_proportional_verbs.py::test_configure_never_seeds_excluded_an_envelope_it_cannot_place_stays_unsorted`.

## Amendment (2026-09-26): the owner's own row order

**Change.** The state gains a sixth key, `order: [envelope_id, ...]`, written by a new verb,
`set_order`. The rows used to follow the Zero-Based order, group by group. A 50/30/20 group
mixes envelopes from several Zero-Based groups, so dragging one row above another there could
only have been saved by moving it on the Zero-Based screen too, and nothing was saved at all.

`compute_month` re-sequences its rows with it (`proportional_state.apply_row_order`). An
envelope in `order` sorts by its place there; one never placed keeps its Zero-Based place after
every placed one, so a new envelope lands at the bottom of its group. `set_order` takes one
group's rows, top to bottom. They move to the end of the stored list, so every other row keeps
its place.

The table saves a drop on a row as that group's new order (plus `set_bucket` first when the row
came from another group). A drop on a group header lands the row at the bottom.

Zero-Based's order is never touched. Pinned by `tests/test_planning_proportional_api.py::test_set_order_reorders_rows_within_their_bucket`
and `frontend/.../PropTable.test.tsx` ("drag to reorder").

## Amendment (2026-09-26, later): the groups are the book's Zero-Based groups

**Why.** The owner keeps many granular envelopes. Sorting each into Needs, Wants or Excluded was
the hard part ("hard to know which one should be needs, wants"), and two buckets were too coarse
("sometimes I want to make it more granular"). Asked whether 50/30/20 should keep groups of its
own or use Zero-Based's, the owner chose "same as Zero-Based".

**Change.** The per-envelope bucket is gone. 50/30/20's groups are the book's Zero-Based groups,
in their order (`proportional_state.groups_out`). An empty group the app keeps (Credit Card
Payments) is left out, and so is an empty income-only one; a group holding envelopes is always
listed. State: `group_targets {group_id: percent}` (each group's share of income),
`excluded_groups` (groups outside the split), `caps`, `income_category_ids`. The legacy
`targets`/`mapping`/`buckets` stay in the row, inert. `buckets` is read once: while no Excluded
choice has been stored, a group whose envelopes were all Excluded starts excluded.

Savings is `income − Σ spend of counted groups − unassigned`, and its target is `100 − Σ shares`
(none until a share is set). Operations: `configure {group_targets?}`, `set_group_targets`,
`set_group_excluded`, the three cap operations, `set_income_categories`. Removed: `set_config`
(and its PUT route), `set_targets`, `set_bucket`, and the same-day `set_order` with its `order`
state. `draft()` now proposes each group's share from its last three months.

Row order, moving an envelope, and adding or renaming a group are Zero-Based's own writes
(`move_category`, `reorder_categories`, `group_create`, `group_rename`), so the two screens
never disagree. That supersedes this ADR's earlier same-day amendment on 50/30/20's own row
order.

## Amendment (2026-09-28): one envelope out of the split; spending no row owned is shown

**Why.** A budgeting gap review with the owner ("do all of them e2e") found three things the
by-group model still got wrong or hid: an envelope could only leave the split with its whole
group (a pass-through you're paid back for, like a proxy loan, needed a group of its own);
spending in a category no envelope holds, or with no category at all, came out of savings
with no row anywhere to show it; and a commitment's envelope was dropped from the rows, so its
category's real spending fell, unseen, into that same "not in an envelope" figure.

**Change.**
- `excluded_envelopes: [envelope_id]` joins the state (default `[]`), set by a new operation
  `set_envelope_excluded {envelope_id, excluded}`. The envelope stays in its Zero-Based group;
  its row carries `excluded: true` and its spend leaves its group's activity, so savings no
  longer counts it. The screen lists such envelopes in an **Excluded** section below the
  groups (drag a row there, or untick "Count in the split" in its editor; drag it back into a
  group to count it again). Lucius's view folds them into `unmapped`, with excluded groups.
- `unassigned.categories` lists each expense category with spending this month that no row
  covers (`{id, label, color, activity}`); with `uncategorized` it renders as a **Not in an
  envelope** section whose Activity opens the transactions. The savings formula is unchanged
  — this spend always counted; now it is seen.
- Only a credit card's payment envelope is hidden from the rows
  (`proportional_config.hidden_envelope_ids`): card purchases already count in their own
  envelopes and paying the card is a transfer. A commitment's envelope is a row in a month it
  has spending, in its (synced, locked) group; a month it has none it is left out rather than
  shown as a S$0 line.
- `configure` accepts `excluded_groups` too, so onboarding can start a group outside the split
  (its "count in the split" tick, the same as Configure's).
