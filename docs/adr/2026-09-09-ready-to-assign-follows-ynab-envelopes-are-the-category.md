---
title: Ready to Assign follows YNAB exactly — envelopes hold overspending until month-end, cards use payment envelopes, envelope is the only bucket (category is a label)
status: Accepted (the "ONE fix" — uncategorized/unlinked cash spend draining immediately — AMENDED by [2026-09-10-uncategorized-and-unlinked-cash-spend-holds-until-month-end](2026-09-10-uncategorized-and-unlinked-cash-spend-holds-until-month-end.md))
date: 2026-09-09
deciders: orchestrating Claude agent + repo owner (design settled in-session 2026-09-09, "Go, E2E")
---

# 2026-09-09. Ready to Assign follows YNAB — envelopes are the category

> **AMENDED 2026-09-10, on one point only.** This ADR's "**The ONE fix**" below — making
> uncategorized ("Unsorted") and unlinked-category spend drain Ready to Assign immediately — was
> decided on indirect research about YNAB. Direct observation the next day (live YNAB, screenshots)
> showed YNAB HOLDS that spend too, in its own uncategorized pseudo-bucket, exactly like an
> overspent envelope. The collapse to ONE rule for "no bucket" was right; the rule picked was the
> wrong one. See
> [2026-09-10-uncategorized-and-unlinked-cash-spend-holds-until-month-end](2026-09-10-uncategorized-and-unlinked-cash-spend-holds-until-month-end.md)
> for the corrected formula and the CASH-only scope. **Everything else in this ADR stands**: the
> clamped carry, the signed `held_in_envelopes`, the entire credit-card payment-envelope engine,
> RTA-neutral card spend (linked or not), and the retirement of `RTA ≤ cash − baseline`.

## Context

ADR 2026-09-08 replaced the balance-based RTA (ADR 2026-07-02 + 2026-07-09) with an
"unassigned cash" identity: every outflow reduced RTA the moment it left cash, negatives carried
forever with no reset, and card debt was netted continuously with no payment envelopes. It
shipped, was verified end to end, and merged as PR #112.

The next day, walking the shipped numbers with the owner surfaced the actual complaint: **the
2026-09-08 identity is not what YNAB, Actual Budget, or a zero-based-budgeting user expects, and
this app's only real difference from those apps is envelope-vs-category — not a different money
model.** Specifically:

- **Users moving from YNAB/Actual expect YNAB's rules.** Every help article, forum answer and
  onboarding flow those users have seen describes YNAB's hold/reset/cover-costs-RTA behavior.
  Shipping a third, invented model means every difference becomes a support ticket instead of
  something the user already knows.
- **Zero-based users are exactly the people who carry credit-card debt on purpose** (owner:
  "I think we should allow people to pay the card off over time"). YNAB's payment-envelope
  mechanism is the feature that makes that safe — assign toward the payment envelope monthly,
  pay from it, the debt shrinks on a schedule you chose. The 2026-09-08 identity deleted payment
  envelopes and continuously netted card debt into RTA, which has no room for "pay over time":
  a revolver's RTA reads permanently negative by the size of their old balance.
- **The engine 2026-09-08 replaced was already YNAB-faithful and already verified.** ADR
  2026-07-02's balance identity plus ADR 2026-07-09's payment-envelope engine were built,
  adversarially reviewed, and — per ADR 2026-07-09 — "verified YNAB-exact against real YNAB
  books." Comparing this app's shipped 2026-09-08 numbers against YNAB's and Actual's own
  documentation (support.ynab.com, actualbudget.org/docs, fetched and quoted 2026-09-09) showed
  the 2026-09-08 identity matches **neither**: both YNAB and Actual hold an overspend in-month
  and charge it at month-end (Actual: per-category, defaulting to charge, toggleable to carry);
  2026-09-08 drained immediately and carried forever, no reset, no toggle. That is not a
  deliberate simplification — it is a different, unrequested product.
- **The one genuine defect in the pre-2026-09-08 engine was narrow and already diagnosable from
  its own docstrings.** `_ready_to_assign`'s `reserved` sum folded uncategorized ("Unsorted")
  spend in as if it were a mini-envelope (`_unsorted_activity`, held, "exactly like a budgeted
  one"), while a category linked to no envelope drained immediately. Two treatments of the same
  situation (no bucket at all) was the actual bug 2026-09-08 set out to fix, and fixing it did
  not require deleting the hold/reset/payment-envelope engine around it.

## Decision

We restore the pre-2026-09-08 engine (ADR 2026-07-02 + 2026-07-09, git commit `6584ba673`,
verified against real YNAB books) as current truth, with one fix and one generalization:

```
available(m, env) = max(0, available(m−1, env)) + assigned(m, env) + activity(m, env)
    # a current-month overspend is SIGNED (counts negative); the CARRY clamps at 0 —
    # an unresolved overspend does not carry into next month as debt, it resets

ready_to_assign(m) = cash_as_of(m) − pre_budget_baseline − Σ_env available(m, env)
                                                           − card_uncovered_offset(m)
    # cash_as_of excludes credit-type accounts; a card's debt lives ENTIRELY in its
    # payment envelope's available, never in cash_as_of
```

- **An envelope holds its own overspend until month-end** — in-month, RTA does not move when a
  category goes red, whichever account paid for it. At month-end, an envelope still negative
  resets to 0 (clamped carry) and the hole is charged to next month's RTA — it falls straight out
  of the identity (cash already dropped for a cash overspend; the carry no longer offsets it).
- **The ONE fix** *(AMENDED 2026-09-10 — see the banner at the top of this file; the collapse to
  one rule stands, the direction is reversed)*: uncategorized ("Unsorted") spend is removed from
  `reserved` — it now drains RTA immediately, exactly like a category linked to no envelope.
  There is now exactly one rule for "no bucket": **an envelope holds; nothing else does.** (Was:
  `reserved += _unsorted_activity(ctx, month)`; now: dropped entirely — matches how a
  category-with-no-envelope was already treated.)
  **As of 2026-09-10 the rule is the opposite one: CASH spend with no bucket HOLDS, in a
  pseudo-envelope of its own (`held_outside_envelopes`), and is charged at the roll if still
  unresolved — because that is what YNAB was observed to do. "No bucket" is a CASH-only concept;
  the card side of this ADR is unchanged and was re-verified.**
- **Credit cards keep their automatic payment envelopes** (ADR 2026-07-09, unchanged): a card
  spend from a funded envelope auto-moves the covered amount into the card's payment envelope
  (RTA-neutral); an uncovered remainder is tracked by `_CARD_UNCOVERED_KEY`, which cancels the
  spending row's own drain so an unfunded card spend is *also* RTA-neutral in-month (the debt is
  real but hasn't cost you cash yet) — never manufacturing assignable cash out of unfunded debt.
  Standing/legacy card debt does not reduce RTA (a card starts its payment envelope at 0); a
  revolving-debt user pays it down by assigning toward the payment envelope over time, exactly
  like YNAB's documented recommendation for a Line-of-Credit-style balance.
- **Covering an overspent envelope now costs Ready to Assign**, dollar for dollar up to the
  hole's size — this is the reverse of 2026-09-08's "covering is free, never changes RTA" and is
  the genuine YNAB mechanic: holding an overspend means "someone still has to pay for this
  eventually," and assigning money to it is that payment. `_auto_cover_overspending` is capped at
  the displayed RTA (restored B4 rule, symmetric with `_auto_underfunded`) — auto-assign must
  never propose funding money that doesn't exist.
- **RTA can exceed cash on hand, in-month.** This is the deliberate trade the previous ADR
  refused: `RTA ≤ cash − baseline` is no longer an invariant. A held overspend is resolved by the
  month-end sweep, not by an instantaneous clamp. The property test asserting the old invariant
  is retired; a new one asserts the identity closes exactly (`RTA + reserved + baseline ==
  cash_as_of`) on every generated book instead.
- **Envelope, not category, is the only budgeting bucket** — this was already true before
  2026-09-08 (RFC `envelope-category-full-split.md`, 2026-07-04) and does not change: a category
  is a transaction label; an envelope is what holds or drains money. Every rule above is stated
  in terms of envelopes.
- **Fresh start, popover, and the identity's other surfaces are rebuilt against this restored
  formula**, applying 2026-09-08's genuine UX improvement (an exact, closed ledger with no
  unexplained residual) to the correct math instead of discarding it. The pre-2026-09-08 fresh
  start dialog had exactly the residual-term problem ("other_movements", "outside_envelopes_spend")
  that prompted the 2026-09-07/08 complaints; that defect is fixed here too, by building the
  ledger from the SAME closed terms the popover uses, not by restoring the old dialog verbatim.

### Comparison, verified against current docs (2026-09-09)

| | YNAB | Actual Budget | This app (restored) |
|---|---|---|---|
| Headline excludes card balance directly | Yes (cards in cash sum but payment envelope offsets) | No (card balance subtracted directly) | Yes — cards never in `cash_as_of`; entirely via payment envelope |
| Funded card swipe | Auto-moves into payment category, RTA-neutral | Category drains, RTA already reflects it | Auto-moves into payment envelope, RTA-neutral |
| Unfunded card swipe | Category red now; not charged to RTA at month-end (real debt) | Category red, headline already dropped; swept at month-end unless "Rollover Overspending" | Category red now; RTA-neutral now AND at rollover (debt lives on the card via `_CARD_UNCOVERED_KEY`/payment envelope, never re-charged) |
| Cash overspend | Held now; charged to next month's RTA | Held now; charged to next month's headline by default, toggle to carry | Held now; charged to next month's RTA (restored) |
| Pay card off over time | Native (assign into payment envelope monthly) | Manual (a user-made "card debt" envelope with Rollover Overspending on) | Native (assign into payment envelope monthly) |
| Payment envelope automatic | Yes | No | Yes (restored) |
| Uncategorized spend | Drains immediately (no bucket) | Drains immediately | Drains immediately (**the fix**) |

We match YNAB on every row except "Actual has no automatic payment envelope" (Actual doesn't
need one because it subtracts card debt directly, which forecloses pay-over-time) — the row the
owner specifically wants supported.

## Consequences

### Positive
- Every rule matches an app our users already know; the product doc can say "works like YNAB;
  your envelope is your category" instead of teaching a fourth model.
- Pay-off-over-time is native again, with no new mechanism to build.
- The S$12,750-style "sent above what you'd set aside" line that grows forever is fixed as a
  side effect of restoring the clamped carry (`max(0, prev)`) — a payment envelope driven
  negative this month resets to 0 next month instead of accumulating.
- The uncategorized-vs-unlinked inconsistency (the actual 2026-09-08 complaint) is fixed by one
  narrow change, not a new identity.

### Negative
- **Reverses yesterday's shipped behavior a user may have already seen** (PR #112, live one day).
  RTA jumps back up toward its pre-2026-09-08 level (owner's book: ≈ 31,625 → ≈ 44,000-range,
  reversing the drop 2026-09-08 caused) and the "RTA ≤ cash" guarantee is gone.
- **Covering an overspend costs RTA again** — the opposite of what shipped yesterday and of what
  the popover/Lucius copy said for one day. Must be corrected everywhere that sentence landed.
- The identity can show RTA above cash on hand in-month; this must be explained (a red envelope
  plus "held until month-end" framing), not hidden.
- Card-linked payment envelopes reappear on the Budget tab.

### Follow-ups
- Spec: `docs/superpowers/specs/2026-09-09-rta-follows-ynab-design.md`.
- Supersedes ADR `2026-09-08-ready-to-assign-is-unassigned-cash` (both its original decision and
  its two 2026-09-09 amendments) and restores ADR `2026-07-02-balance-based-rta` +
  `2026-07-09-ynab-engine-credit-card-payment-envelopes` as current truth (their "Superseded"
  status flips back to Accepted). Amends `2026-09-08-budget-fresh-start-moves-the-floor-not-the-data`
  (ledger terms change; the floor/undo/one-transaction mechanics do not).
- Docs: `PLANNING_MODEL.md`, `INVARIANTS.md`, `CLAUDE.md`, product budgeting page, acceptance,
  Lucius tool descriptions, `docs/adr/README.md` index (flip statuses).

## Alternatives considered

- **Keep 2026-09-08's identity, add a "pay over time" bolt-on** — rejected: there's no honest
  place to bolt it onto an identity that already nets all card debt continuously; any bolt-on
  either double-counts or re-invents a payment envelope, which is what we're restoring anyway.
- **A fourth, original model blending both** — rejected explicitly by the owner ("why do we need
  a different rule for different thing?"): every genuine difference this app has (envelope ≠
  category) is already expressible inside YNAB's own rules; nothing here justifies inventing more.
- **Match Actual instead of YNAB** — rejected: Actual's model forecloses pay-over-time (the
  owner's stated requirement) and needs no automatic payment envelope only because its headline
  already nets card debt, which is the exact tradeoff being avoided.

## Agent provenance

- **Decided by:** orchestrating Claude agent, in dialogue with the repo owner, one day after
  shipping ADR 2026-09-08.
- **Lane / branch:** `feat/rta-follows-ynab`, isolated worktree (the primary checkout had a
  concurrent, unrelated, uncommitted session in progress at branch time — never touched).
- **Alternatives weighed:** keep-and-bolt-on; blended fourth model; match Actual instead of YNAB.
- **Driving artifacts:** owner's live-book screenshot (four card rows, growing "sent above what
  you'd set aside" balances); owner questions 2026-09-09 ("why do we need a different rule for
  different thing?", "I think we should allow people to pay the card off over time", "give me
  final recommendations... think from product perspective"); YNAB/Actual docs fetched and quoted
  2026-09-09 (support.ynab.com, actualbudget.org/docs); git commit `6584ba673` (the last
  pre-2026-09-08 state of `backend/services/planning.py`, itself built under ADR 2026-07-02/07-09
  and described there as "verified YNAB-exact against real YNAB books").
- **Why this way (one line):** the app's only real difference from YNAB is envelope-vs-category,
  so the only honest model is YNAB's rules, restored from the verified engine that already
  implemented them, with the one narrow defect that engine actually had now fixed.
