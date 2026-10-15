---
title: Uncategorized and unlinked CASH spend HOLDS until month-end — "no bucket" is an envelope-shaped holder, not a drain
status: Accepted
date: 2026-09-10
deciders: orchestrating Claude agent + repo owner (settled in-session 2026-09-10 against live YNAB screenshots)
---

# 2026-09-10. Uncategorized and unlinked cash spend holds until month-end

Amends [2026-09-09-ready-to-assign-follows-ynab-envelopes-are-the-category](2026-09-09-ready-to-assign-follows-ynab-envelopes-are-the-category.md)
on ONE point — its "THE ONE fix". Everything else in that ADR (the clamped carry, the signed
`held_in_envelopes`, the whole credit-card payment-envelope engine, RTA-neutral card spend, the
retirement of `RTA ≤ cash − baseline`) stands unchanged and is re-asserted by this change's own
tests.

## Context

ADR 2026-09-09 restored the YNAB-faithful engine and, in the same breath, changed one thing about
it: uncategorized ("Unsorted") spend stopped being folded into `reserved`, so it drained Ready to
Assign the instant the transaction was recorded. The stated reasoning was consistency — a
labeled category linked to no envelope already drained, so folding the blank bucket in was "two
treatments of the same situation." Collapsing them to one rule was correct. **Picking the
draining one was not.**

That choice rested on indirect research about how YNAB behaves, not on watching YNAB behave.
This session the owner walked the case in a real YNAB book, side by side with this app,
screenshot by screenshot. YNAB holds it:

- Spend from a cash account with **no category at all** → YNAB's Ready to Assign **does not
  move**. The amount sits in YNAB's own uncategorized pseudo-bucket, red, exactly like an
  overspent category.
- Spend from a cash account on a **real category that has no budget line** → same: Ready to
  Assign does not move; the category shows the hole.
- Only at the **month roll**, if the spend still has no home, does the hole clamp to zero and
  get charged to the following month's Ready to Assign — the identical mechanic an overspent
  envelope already gets here.

So the rule "an envelope holds; nothing else does" was drawing the line in the wrong place. The
line is not *envelope vs. not-envelope*. It is *cash vs. card*, and everything on the cash side
holds.

Two facts make this more than a preference. First, draining immediately is not even
self-consistent inside 2026-09-09's own model: an overspent *envelope* is held for a month and
charged once at the roll, so the same S$15 of groceries moves RTA on completely different
schedules depending only on whether its category happens to be linked. Second, the drain is
irreversible in the user's mental model — RTA drops today, and categorizing the transaction
tomorrow silently puts it back, with nothing on screen having explained either move.

The card side was verified in the same session and is correct as shipped: an unfunded card
charge, categorized or not, linked or not, never moves RTA. It surfaces as card debt on the
payment envelope's `owed`/`underfunded`. That asymmetry is not an inconsistency to be tidied
away — see the Decision.

## Decision

We will treat CASH spending with no bucket as an **envelope-shaped holder**, under the same
clamped carry every envelope already gets:

```
held(m) = min(0, max(0, held(m−1)) + cash_activity(m))
```

one instance for the blank/Unsorted bucket, one **per** unlinked category, summed into a new
identity term:

```
ready_to_assign(m) = cash_as_of(m) − pre_budget_baseline
                     − held_in_envelopes(m)          # Σ available over envelope ROWS, SIGNED
                     − held_outside_envelopes(m)     # NEW: Unsorted + Σ unlinked categories
                     − card_spend_not_covered(m)
```

Three parts of that formula are load-bearing:

1. **It is `_available`'s own recurrence with `assigned ≡ 0`.** Nothing can be assigned to a
   bucket that is not an envelope, so the "assigned" term is structurally absent rather than
   merely zero. This is deliberately not a parallel mechanism: the hold, the reset at the roll,
   and the charge-forward all come from the same rule an envelope obeys, so they cannot drift
   apart from it.

2. **The upper clamp (`min(0, …)`) — the one thing an envelope does NOT have.** A no-bucket
   bucket can hold a *hole* but never a *surplus*. Nothing can be assigned there, so money
   arriving there is unassigned cash and belongs to Ready to Assign. This is not a new rule: it
   is exactly what `_activity_by_category` already does for income landing on an unlinked
   category ("RTA inflow, never absorbed into a reserve that doesn't exist"). Without it, a
   reimbursement arriving on-budget under an unlinked label (a categorized transfer with one
   on-budget leg, or an investment sell) would stop raising RTA and vanish into a bucket the
   user cannot spend from — a worse bug than the one being fixed.

   A consequence worth stating plainly: with the value pinned ≤ 0, `max(0, held(m−1))` is
   provably 0 for every month, so the recursion collapses to the current month's own activity.
   The implementation says so and takes no prior-month walk, rather than shipping a memoized
   recursion that always returns zero.

3. **`cash_activity`, never the full activity map.** The hold reads a CASH-ONLY fold: every
   contribution except a payment-tracked card's own spend drain.

Cards are untouched, and the asymmetry is the point rather than an oversight. "No bucket" is a
statement about *where the money that left is being held*. A card spend never took cash out of
the budget — there is nothing to hold, and nothing to charge later. Its shortfall is real debt,
it lives on the card, it is surfaced by the payment row's `underfunded`/`owed`, and the ONLY way
it touches Ready to Assign is by being funded (an assignment into the payment envelope). A card
swipe on an unlinked category therefore still shows its Activity row and holds exactly S$0.

We will publish the new term as its own wire field, `held_outside_envelopes`, rather than
folding it into `held_in_envelopes`. `held_in_envelopes` stays exactly the Σ over envelope rows
that `totals.available` renders — the app deliberately keeps those two the same number (ADR
2026-09-08, kept by 2026-09-09) so the header cannot disagree with the identity beside it — and
the RTA popover's "Held in envelopes" line stays true to its own label.

### Cash-only, structurally rather than by convention

The hold reads a per-key accumulator filled by `_iter_category_contributions` in the same pass
as `_activity_map`, threaded out via a `cash_out` out-parameter and memoized in the context as
`_cash_activity`. A leg lands in it only when the leg that ACTUALLY MOVED sits on an account in
`ctx["cash_ids"]` — the exact set `_cash_as_of` sums. That is what makes the guarantee
structural: a dollar the hold reserves is provably a dollar the identity's cash side counted,
because both are reading one account universe. Getting this wrong in either direction is a real
money bug — count card spend and an unfunded swipe is charged on both sides of the identity;
miss a cash leg and RTA runs high.

> **Correction, 2026-09-10 (same day, before merge).** As first shipped, this section said the
> hold read **the very accumulator the credit-card coverage cap already built**
> (`cash_activity_by_key`) — "one definition of which legs are cash, two readers". That was
> false, and the security review on this branch reproduced three money bugs from it. The cap's
> predicate is "every plan-source leg EXCEPT a payment-tracked card's own expense", and
> `is_plan_source` accepts `credit` as well as `cash`, so the accumulator admitted card legs
> the cash side never counted: a categorized transfer OUT of a live card to an off-budget
> account (S$200 → RTA +200), an ARCHIVED card's spend once its payment envelope is retired
> (S$100 → RTA +100), and an account just retyped from `cash` to `credit` before the next boot
> sync (S$300 → RTA +300). Each one FABRICATED Ready to Assign, and — because `_ready_to_assign`
> is defined as a function of `held_outside_envelopes` — each one closed the identity exactly
> while doing it.
>
> The two readers want genuinely different predicates and now have two accumulators. The cap
> keeps `cash_activity_by_key` unchanged: an archived card's spend still drains `_available` and
> must still shrink that headroom. The hold gets `cash_out`, gated on `ctx["cash_ids"]`. The
> identity-closure property test could never have caught this (it asserts a formula against its
> own inputs), so it gained a non-circular re-derivation of the term straight off the raw
> ledger, plus generator shapes that reach the card cases. Regressions:
> `tests/test_no_bucket_hold_is_cash_only.py`.

> **Correction 2, 2026-09-10 (second review round, before merge).** Two more of the same
> family, both reproduced. Neither was a value computation, which is why the first round's
> `cash_out` gate did not reach them.
>
> **(d) The KEY SET, not the value.** `_unbudgeted_keys` decides which unlinked categories are
> evaluated at all, and it filtered on FULL activity, reasoning that "card spend is never
> positive, so a key with negative cash activity always clears this filter too". True of
> EXPENSES only. A categorized TRANSFER arriving at an on-budget account folds POSITIVE (the XOR
> rule), and so does a categorized investment SELL. Land either at a `credit` account outside the
> payment-envelope fold — an archived card, a card un-archived since the last boot sync — under
> the same label as a real cash expense, and the full fold nets to 0.00 while the cash fold still
> holds the whole expense. The key vanished from the held map AND from the "Outside envelopes"
> display, and RTA was drained by exactly what should have been held, with nothing on screen to
> show for it (S$100 cash spend + S$100 offset → RTA +100). A key is now admitted on EITHER
> fold; the cash half is one-sided (`<= -0.005`) so a lone inflow still mints no row, and the
> VALUE still comes from the cash map alone. The same bug had a display twin in
> `buildOutsideRows`, which dropped a line on its Activity while the section's Available subtotal
> went on summing that line's hold.
>
> **(e) An inflow could not cancel its own hold.** The income branch folded only
> `if cat in linked`, so an unlinked category could open a hold on a spend and never close it on
> the matching refund: the hold stayed AND the refund raised RTA through cash — the same dollar
> twice, compounding per spend/refund cycle (4 × S$500 → RTA read 3,000 on 1,000 of cash). This
> directly contradicted this ADR's own safety story for the upper clamp. An unlinked inflow now
> folds into the no-bucket accumulator ALONE (`fold_no_bucket_only`) — never into Activity (there
> is no envelope for it to be Activity on) and never into the coverage cap's pool. The clamp then
> does what this ADR claims: a LONE reimbursement still raises RTA in full; a MATCHED one cancels
> its own hole. **A BLANK-category inflow deliberately still folds nowhere** — blank is an INFLOW
> category here (`_INFLOW_CATEGORIES = {"", "ready_to_assign"}`), and netting salary into the
> Unsorted bucket would wipe that bucket's hold for every book that has income (S$3,000 salary +
> S$200 uncategorized spend would hold nothing), undoing this ADR's headline behaviour.
>
> The raw-book re-derivation added by Correction 1 had the SAME blind spot as (e) — it skipped
> income unconditionally, so the "independent second implementation" was agreeing with the bug.
> Both it and the generator now cover both shapes, and the generator engineers the (d) collision
> deterministically, because two random amounts never cancel to the cent. Both fixes were
> verified to fail the property test when reverted.

> **Correction 3, 2026-09-10 (differential fuzz, before merge).** The first three rounds all
> asked WHICH legs the hold may reserve against. This one is about HOW MUCH, and it hid behind a
> gap in the generator rather than behind a gap in the rule.
>
> A transfer's `base_amount` is stamped by `ledger._transfer_base_value` from **whichever leg
> was already in base currency** — deliberately, since that leg needs no conversion. That is the
> right figure for the ENVELOPE (the historical-rate lock, ADR 2026-07-09: a transfer conserves
> value, so the stamp IS the moved value at the transaction-date rate). It is the wrong figure
> for the HOLD, which must reserve exactly what `_cash_as_of` counted moving — the ON-BUDGET
> (moving) leg, revalued at the book's CURRENT rates, which is neither the same leg nor the same
> rate. Two reproduced shapes, no FX drift involved in either:
>
> * **Cross-currency, on-budget leg foreign.** US$1,000 leaves a USD cash account and S$1,362
>   lands off-budget (the bank's rate, 1.362). The stamp takes the arriving SGD leg; the cash
>   side sees S$1,350 leave (1.35, the book's rate). Held −1,362 against a −1,350 hole →
>   **RTA +S$12, fabricated.**
> * **Same currency, unequal legs.** S$1,000 leaves an off-budget account and S$990 arrives
>   on-budget, a S$10 wire fee eaten in transit. The stamp takes the FROM leg — the off-budget
>   one — so 1,000 of "arrival" cancelled a 1,000 cash hole that only 990 had refilled →
>   **S$10 of real cash drained out of RTA.**
>
> And the read was ungated: `base_amount` was trusted with no check that `base_currency` still
> matches the current base, which this repo's own rule (`core.fx.txn_base_amount`) exists to
> enforce — a base-currency switch clears the FX cache and deliberately does NOT re-stamp
> existing rows. The same S$100 transfer held "100" before and after switching the book to USD,
> where it is worth US$74.07: **RTA +~US$26 on one row**, scaling with every categorized transfer
> in the book. That gate was missing for `held_in_envelopes` too, on the same read, since 2026-07-09.
>
> `fold()` now carries the two figures separately: the stamped `delta` for Activity and for the
> coverage cap's pool (which is compared against envelope Available and must stay locked), and a
> `cash_delta` for the no-bucket accumulator — the moving leg's own native amount at
> `ctx["rates"]`, the arithmetic `_cash_as_of` performs on that very account. **The cap's pool
> mirrors envelope math; the hold's mirrors cash math.** The FX-lock/current-rate split that
> `_cash_as_of` already documents for envelopes therefore stops leaking into the hold.
>
> Why four rounds of the property test missed it: the generator minted transfers that were
> either UNSTAMPED (falling into the leg-based branch, which was already right) or stamped from
> the very leg that moved — the stamp and the cash figure could not disagree. It now mints a
> cross-currency stamped transfer and an unequal-leg (fee) transfer, both on a label it also
> spends real cash on, and counts them only when the gap is material. Over the 200 books it
> generates, 53 book-months diverge under the old rule; the first one fails on book 0.
>
> One more, LOW and latent, fixed in the same pass: the expense branch decided "is this a card?"
> from the account's `payment_envelope_id` FK, while the hold is gated on `type`.
> `update_account` writes the type BEFORE the sync that clears the FK, with no shared
> transaction, so a slow or failed sync leaves a `cash` account still carrying one — and its
> spend took the card path and never reached the fold at all, draining RTA instead of holding.
> The FK still routes the coverage CREDIT (that is what it is for); the HOLD is now decided
> independently on `cash_ids`. Regressions: `tests/test_no_bucket_hold_is_cash_only.py` (f)/(g),
> generator + counters in `tests/test_rta_identity_property.py`.

### What the user sees

The "Outside envelopes" section's Available column showed a literal em dash, because a drained
amount holds nothing and there was no figure to print. It now shows the real held balance, red,
through the same `Fig kind="available"` cell every envelope row uses. Uncategorized income keeps
the em dash — it holds nothing and is already inside Ready to Assign. The banner copy above it
is unchanged: it already said "spending outside envelopes", which is still exactly what it is.

The RTA breakdown popover gains a "Held outside envelopes" row (hidden at zero, like the other
optional terms). Its value is ≤ 0, so its glyph is always `+`, and an unexplained credit for
money that has plainly left the account reads as a defect — so the row carries an inline note
saying what it is and when it will be charged, in the same shape as the existing
`HELD_CREDIT_NOTE` for a net-overspent envelope.

## Consequences

### Positive

- One rule for an unfunded cash outflow, whatever label it carries: **held this month, charged
  once at the roll if still unresolved, evaporating if it finds a home first.** An envelope's
  overspend, an unlinked category's spend, and an uncategorized spend are now the same event
  with the same schedule.
- Matches observed YNAB, to the cent, through a full overspend-then-roll cycle.
- Categorizing a transaction mid-month no longer moves Ready to Assign — which is right: sorting
  your receipts is bookkeeping, not budgeting. Under the old rule it silently handed money back.
- The user gets a month of grace to sort a receipt before it costs them anything, and the
  Available column now shows exactly what is riding on doing so.
- The identity still closes EXACTLY (pinned on 200 generated books), with the new term included.

### Negative

- **RTA can now sit further above cash on hand.** ADR 2026-09-09 already retired
  `RTA ≤ cash − baseline`; this widens the gap, because uncategorized spend now also lends room
  back until the roll. A user who never categorizes anything sees an RTA that looks healthy all
  month and drops at the turn. That is YNAB's behaviour, and the banner + the now-real Available
  figures are what make it legible — but it is a real cost and it is why the popover row carries
  a note rather than just a number.
- A fifth term in an identity that people read on screen. Every surface that renders the
  breakdown (popover, desktop rail, fresh-start ledger) had to take it, and any future one must.
  Mitigated by the fact that all three already share ONE `buildRtaLines` builder.
- The upper clamp is a genuine asymmetry with `_available` and someone will eventually "fix" it
  for symmetry's sake. Hence its own regression test
  (`test_unlinked_category_never_hoards_a_surplus`) and a property assertion that the term is
  never positive on any generated book.
- An UNCATEGORIZED transfer or investment leg still moves RTA immediately, because it folds into
  no bucket at all (`_iter_category_contributions` skips it) rather than into `""`. Defensible —
  moving cash to a tracking account is money genuinely leaving the budget, not spending without
  a home — but it does mean "uncategorized" behaves differently for an expense than for a
  transfer. Left as-is deliberately; pinned by
  `test_uncategorized_transfer_one_leg_on_budget_still_drains_rta_directly`.

### Follow-ups

- None blocking. The auto-assign surfaces (`_auto_cover_overspending`, `cover_overspending`) are
  untouched: they operate on envelope rows, and these buckets have no row to fund — the way to
  resolve one is to categorize the transaction or link its category, both of which already exist
  as row actions ("Review in Accounts", "Add to envelope…").

## Alternatives considered

- **Leave 2026-09-09's drain in place** — rejected: contradicted by direct observation of YNAB,
  and internally inconsistent with the hold an overspent envelope already gets for the
  identical outflow.
- **Fold the new term into `held_in_envelopes`** — rejected: breaks the deliberate
  `held_in_envelopes == totals.available` correspondence and makes the popover's "Held in
  envelopes" line a lie. A separate, explicitly-named term costs one row and explains itself.
- **Hold the card side too, for symmetry** — rejected: a card never had cash to lose, so there
  is nothing to hold and nothing to charge at the roll. Holding it would double-count the swipe
  against `card_spend_not_covered` and manufacture a charge for money that never left the
  budget. Verified correct as-is against YNAB in the same session (scenarios F and H).
- **No upper clamp — mirror `_available` exactly** — rejected: an inflow landing on an unlinked
  category (a reimbursement, an investment sell) would stop raising RTA and disappear into an
  unspendable bucket. Contradicts the engine's own existing rule for income on an unlinked
  category.
- **One `min(0, …)` over the SUM of all unlinked categories instead of per-category** —
  rejected: one category's refund would cancel another's still-open hole, so a book could be
  quietly under-charged at the roll.
- **A hold that carries the hole forward as debt instead of resetting** — rejected: it is the
  rule 2026-09-08 shipped and 2026-09-09 removed for good reasons; charging once at the roll is
  both YNAB's behaviour and the one already proven correct here.

## Agent provenance

Implemented by the orchestrating Claude agent from the owner's in-session YNAB walkthrough. The
formula was derived and hand-checked against the observed YNAB figures before any code was
written; the regression suite carries those exact figures (S$12,416.03 baseline, the S$15 and
S$8 cases, the S$12,401.03 roll-forward charge) rather than figures read back out of the new
implementation.
