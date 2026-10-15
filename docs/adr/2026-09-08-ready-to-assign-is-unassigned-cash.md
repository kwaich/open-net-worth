---
title: Ready to Assign is unassigned cash — every outflow reduces it when it leaves cash; envelopes hold back positive balances only
status: Superseded
date: 2026-09-08
deciders: orchestrating Claude agent + repo owner (design settled in-session 2026-09-08, "go, e2e")
---

# 2026-09-08. Ready to Assign is unassigned cash

> **Superseded 2026-09-09** by ADR [`2026-09-09-ready-to-assign-follows-ynab-envelopes-are-the-category`](2026-09-09-ready-to-assign-follows-ynab-envelopes-are-the-category.md) — one day after shipping, comparing against YNAB/Actual's own documentation showed this identity matched neither, and the owner's requirement to support paying a card off over time has no honest home in an identity that nets all card debt continuously. The pre-2026-09-08 engine (ADR 2026-07-02 + 2026-07-09) is restored, with the one real defect this ADR correctly diagnosed (uncategorized spend held instead of drained) now fixed narrowly instead of the whole identity being replaced.

## Context

ADR 2026-07-02 made Ready to Assign balance-based but kept YNAB's overspend rule: an overspent
envelope is *held* in-month (its negative available cancels the cash drop, so RTA stays put) and
charged to next month's RTA at rollover. That rule is coherent in YNAB because every category is a
budget bucket and every transfer to a tracking account must be categorized. This app split
envelopes from categories on purpose and runs three budgeting lenses, so the rule grew
exceptions: uncategorized spend is held (`unsorted` in `reserved`), spend in a category with no
envelope is *drained* ("drains RTA directly"), transfers to off-budget accounts are drained, and a
`_CARD_UNCOVERED_KEY` term patches card spending. Three treatments of one event.

On the owner's book this produced RTA S$44,115 against S$37,665 of cash, a header "available"
that disagreed with the identity by the unbudgeted spend, and a fresh-start dialog whose promised
figure sat S$207.68 below "cash on 1 Sep" with no visible reason. The owner's standard for this
app: one word, one number; numbers you would bet real money on.

## Decision

We will define, for the zero-based lens only:

```
RTA(m) = cash(m) − baseline − Σ_env max(available(m, env), 0) − Σ_cards max(card_debt − funded, 0)
available(m, env) = available(m−1, env) + assigned(m, env) + activity(m, env)     # no reset
```

- Every outflow reduces RTA the moment it leaves on-budget cash — categorized or not, envelope
  or not, transfer or not. The `unsorted`, `unbudgeted` and `_CARD_UNCOVERED_KEY` terms are
  deleted from the identity.
- Envelopes hold back positive balances only. An overspent envelope is a fact about the past;
  assigning into a hole never moves RTA.
- Negatives carry into the next month; there is no month-end reset and no
  `prev_month_overspend` charge.
- Card debt is netted continuously: the unfunded part of every card's balance is subtracted at
  all times. The payment-envelope engine stays as the funding mechanism.
- Invariant: **RTA ≤ cash − baseline**, always.

Zero-based budgeting is unchanged as a goal — drive RTA to zero — and stricter as a fact: RTA = 0
now means every dollar of cash is in an envelope, reserved for a card, or already spent.

## Consequences

### Positive
- One rule; the header total, the RTA popover, the fresh-start ledger and the banner agree by
  construction. RTA can never exceed cash.
- Categorization is a labelling act: it decides which allotment absorbed a spend, never how much
  is left to assign.
- The cash-vs-credit rollover split, the card-uncovered term, the "previous month overspend"
  charge and the display-total tooltip all disappear.

### Negative
- YNAB's "cover every overspend before month end" stops gating the headline; a red envelope is
  information. The disciplined form lives in the cash-envelope lens (an empty pot is a hard stop).
- The headline drops on upgrade (owner: ≈ 44,115 → 31,625) and October shows carried negatives
  for envelopes overspent in September. Release-noted.
- `tests/test_rta_balance_identity.py` and its neighbours are re-pinned wholesale.

### Follow-ups
- Spec `docs/superpowers/specs/2026-09-08-rta-is-unassigned-cash-design.md`.
- Supersedes ADR 2026-07-02 (identity + held-overspend rule; the on-budget account set stands via
  ADR 2026-09-07-seven-account-types-one-axis) and ADR
  `2026-09-07-budget-available-is-the-display-total` (its "RTA reserve" vs. "display total"
  distinction dissolves — there is only one identity now, so the header collapses back to the
  plain envelope sum and its display-total tooltip goes). Amends ADR 2026-07-09 (payment
  envelopes kept; RTA effect changes) and ADR 2026-09-08-budget-fresh-start (its ledger becomes this
  identity on the cleared context: cash − set aside − unfunded card debt − baseline, with set
  aside normally 0 after the clear and shown only when nonzero).
- Docs: `PLANNING_MODEL.md`, `INVARIANTS.md`, `CLAUDE.md`, product budgeting page, acceptance.

## Amendment (2026-09-09) — "covering never changes RTA" is a PER-MONTH claim, and coverage funds a payment envelope

The decision above retired `_auto_cover_overspending`'s RTA cap on the strength of "assigning into
a hole reserves nothing, so covering moves Ready to Assign by exactly 0." The pre-merge security
review reproduced two books where that sentence, as written, was false. Both are now closed in the
engine, and the claim is qualified everywhere it is stated.

1. **A card's payment envelope is never a cover step.** `_capped_card_credits` credits it the
   moment the SPENDING row a swipe drew on is funded, so a step sized against the payment row's
   pre-coverage Available lands on top of a credit that did not exist when the step was computed.
   Reproduced: cash S$1000, a S$900 swipe on a linked category paid off in-month by a S$900
   transfer — covering proposed 900 to the spending row AND 900 to the payment row, and RTA fell
   100 → −800 for a card that owed nothing. `_auto_cover_overspending` now excludes
   `_payment_env_accounts(ctx)`, matching the Budget tab, which has always forced `status='none'`
   on a payment row. The engine and the tab therefore propose the same work.
2. **The neutrality is per-MONTH, not global.** `auto_assign`'s `month` is caller-chosen. Coverage
   run on a LATER month writes assignments into it, and those reduce the CURRENT month's DISPLAYED
   figure through `_display_rta`'s clamp (the 2026-07-03 owner decision), exactly like any other
   future assignment. That rule is unchanged and deliberately so; the CLAIM is what was wrong.
   Every statement of it — `CLAUDE.md`, `INVARIANTS.md`, the two `auto_assign`/`get_budget_month`
   tool descriptions, the zero-based engine help text, and the product Help corpus — now says
   "never changes Ready to Assign **for that month**".
3. **A COUPLED mode's apply re-derives each step from the live book.** `_op_auto_assign` computed
   every step against ONE pre-write context and then wrote them sequentially, so any mode whose
   per-row figure depends on another row's write had no preview/apply guarantee at all. It now
   re-runs the preview after each write, takes that row's live figure, clamps it to the approved
   amount and skips a row whose hole has already closed. Deliberately NOT one transaction: the
   re-derivation must READ what the previous step WROTE, and `_build_context` reads through its
   own connection — composing the steps would re-derive them all against the same pre-write
   snapshot, which is the bug being closed.

   The rebuild costs ~50 ms/step on the 3k-transaction demo book (an 18-step apply went 44 ms →
   911 ms), so it is paid only by the modes in `_COUPLED_AUTO_ASSIGN_MODES` — and that set is a
   READ/WRITE-SET proof about `month`, not an intuition about which modes look risky. An apply
   writes exactly `assigned(month, env)`. `cover_overspending` reads `_available(month, ·)`,
   which a card-linked row resolves through `_capped_card_credits` off ANOTHER row's
   `assigned(month, ·)`; `underfunded` reads the same, plus a running cap seeded from
   `_ready_to_assign(month)` that every write moves — both COUPLED. `reset_assigned` writes a
   constant and reads nothing; `assigned_last_month` / `spent_last_month` / `average_*` read only
   months strictly BEFORE `month`, which the apply never writes — so their previewed steps ARE
   their end state, and one frozen context is exact rather than merely close enough (the 18-step
   `reset_assigned` is back to 48 ms). A new mode that reads anything at `month` belongs in the
   set until the same argument says otherwise. Pinned by counting `_build_context` calls per
   apply, both directions.

Also closed here, from the same review round: `fresh_start_preview` applies
`budgets._validate_anchor_month` exactly like the write (a future month no longer renders a dialog
the confirm would refuse), and `budget_month` gates `on_budget_cash` and `pre_budget_baseline` at
the carry floor alongside `set_aside_base`/`unfunded_card_debt_base`, so a pre-floor month's
payload closes the identity on zeroes instead of publishing a live bank balance beside a hard 0.

## Alternatives considered

- **Match YNAB fully (hold all three no-envelope cases)** — rejected: requires every category to
  be a bucket and every off-budget transfer to be categorized, undoing envelope ⊥ category; and a
  user with no envelopes who spends everything would see a phantom RTA equal to their starting cash.
- **Keep the current mix, fix labels/tooltips** — rejected: explains a wrong definition instead of
  fixing it; the owner refused the tooltip route for cash on hand and it applies here too.
- **Drain the three no-envelope cases but keep held envelope overspends** — rejected as a halfway
  house: RTA would still exceed cash and still need the rollover charge.
- **Reset negatives at month end (YNAB category behaviour)** — rejected for the default: under
  this identity a carried negative costs nothing in RTA and tells the truth about the pot;
  "Start fresh" is the clean-slate action. A per-envelope reset can be added if asked.
- **Actual Budget's per-category "rollover overspending" toggle** — deferred (YAGNI).

## Agent provenance

- **Decided by:** orchestrating Claude agent, in dialogue with the repo owner.
- **Lane / branch:** `feat/rta-unassigned-cash`.
- **Alternatives weighed:** YNAB-full; label-only; drain-only-no-envelope; reset-at-rollover;
  Actual's per-category toggle.
- **Driving artifacts:** owner questions 2026-09-08 (RTA > cash; S$213 header gap; S$207.68
  fresh-start gap; S$5 uncategorized); YNAB rollover doc
  (support.ynab.com "When the month rolls over"); Actual Budget "To Budget" model; spec above.
- **Why this way (one line):** categories classify and envelopes budget, so the only honest
  meaning of "ready to assign" here is cash nobody has given a job yet — measured at the cash,
  not reconstructed from the plan.

## Amendment (2026-09-09) — one row builder, and colour means status

Review of the shipped surfaces found the identity rendered THREE different ways depending on
where you opened it: the RTA popover always showed "Set aside in envelopes" while the
fresh-start ledger hid it at 0 (sanctioned by the "shown only when nonzero" note under
*Follow-ups* above — now superseded); the popover signed each term twice (a glyph column AND a
minus on the amount) while the ledger signed it once, via `fBaseCents`; and the popover tinted
every subtracted row `--bad` while the ledger used no colour at all. Painting money properly set
aside the same alarm red as card debt nobody has funded overloaded the tab's own status
vocabulary — it read as two problems where there was one.

Resolved by making it structurally impossible to drift again:

- **ONE builder**, `frontend/src/components/tabs/plan/rtaLines.ts` (`buildRtaLines`), and ONE row
  component (`budgetRta.RtaRow`). Both the popover and the fresh-start dialog's ledger render its
  output; the ledger's bespoke `<dl>` grid is gone.
- **Cash on hand and Set aside in envelopes ALWAYS render**, in both places — they are the
  identity's backbone, and at $0 "Set aside in envelopes" is the answer, not noise. Card debt,
  baseline and assigned-in-future stay hidden at 0.
- **The sign is spelled once**, in the glyph column; amounts render `fBaseCents(|value|)`.
- **Colour marks STATUS, not arithmetic**: plain subtractions keep the default ink, "Card debt
  not yet funded" takes `--warn`, and only the `= Ready to Assign` result is tinted by sign.
- The defect row is labelled **"Unexplained — refresh"** (not "Other", which read like one more
  term of the identity and gave the user nothing to act on) and its `console.warn` logs the
  residual ALONE — never the balances behind it, so privacy mode and a screen-share are not
  bypassed via devtools.
- The Budget insight ribbon gains an explicit **`rta < -0.005` branch ahead of** the
  overspent/underfunded checks. Under this identity RTA can go negative with zero overspent
  envelopes and zero underfunded targets (a baseline capture, or unfunded card debt, drags it
  under); the fallthrough was asserting "Every target's funded" in exactly that state.
- Every expense-side "outside an envelope" banner branch now says the money **already came out of
  Ready to Assign**, as the income branch always did. "isn't counted in any envelope yet" read as
  pending, inviting the user to categorize a spend expecting RTA to move back.

Separately, QA found `[budgetRta] Ready to Assign does not match its own terms` firing on every
ordinary load of a healthy book: four terms were read off the month payload while set-aside was
re-summed from the LIVE model rows, which lag by a render on any refetch and cannot see a retired
payment envelope. Set-aside is now sourced exactly like the card-debt term — `set_aside_base` plus
`setAsideOffset`, the offset `applyOptimisticTerms` accumulates and every payload sync zeroes — so
a residual can only mean a genuine defect. Pinned by `budget.rtaTerms.test.tsx`.
