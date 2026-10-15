---
title: Cash-flow projection extrapolates classified streams, not one blind rolling average
status: Accepted
date: 2026-07-27
deciders: Claude, on an explicit owner directive
---

# Cash-flow projection extrapolates classified streams, not one blind rolling average

## Context

`cashflow.build_projection()` derived its entire forward baseline from two numbers — the mean
of **every** `kind=income` and `kind=expense` transaction over a trailing 3-month window — and
applied that pair to every future month for up to 40 months. Nothing in that computation asked
whether a transaction was *recurring*, so a one-off was extrapolated forever.

Measured on a real production book: three equity-sale proceeds (correctly booked as income) sat
in the window and produced a **S$42,000/month** income baseline against a true **S$7,500**
salary. Net projected flow was **+S$26,800/month, forever** — the permanently-climbing cushion
chart that opened the investigation ("it looks always going up which I don't know if that's the
case"). The expense side had the identical defect (a one-time repair or purchase inflates the
baseline the same way).

The owner set the bar explicitly: *"I don't want to invent a number or a new way to do this —
other apps must have solved it already"* and *"research and align on what is the best way…
not just try to implement things based on my data to make it look good on my data only."*

## Decision

A ledger contains **three kinds of cash flow**, each needing a different treatment. The old
code collapsed all three into one mean; that conflation *is* the bug.

1. **Recurring streams** (salary, rent, subscriptions, a quarterly dividend) — detected by
   payee + amount band + **interval regularity**, and projected on their **own calendar
   months** at their own amount, never smoothed. An annual bonus lands in its month, not as
   1/12th every month.
2. **Variable baseline** (groceries, transport, coffee) — irregular individually, real and
   stable collectively. **Averaged** — the old mechanism, applied only where it is correct.
   This bucket is why "only count recurring things" would be wrong: dropping it would
   massively understate spending.
3. **One-off / non-operating** (a stock sale, a one-time repair) — **excluded** from the
   forward baseline, still fully visible in history and still in the starting balance.

Manual input is a **first-class stream**, not a fallback mode: `declare_recurring_stream`
records a raise starting next month, a new rent, or an `end_date` cancellation — things
history cannot show. `profile.json`'s figures keep their existing role as the empty-book
fallback (see "deviation" below).

Only **matured** streams (≥3 occurrences — Plaid's production threshold) are projected. A
2-occurrence **tentative** stream is surfaced for confirmation but never silently drives a
number; confirming it is the escape hatch for a new salary paid only twice.

Result on the real book: income **42,000 → 22,600**, net **+26,800 → +7,400/month**, with the
three equity-sale proceeds explicitly listed as excluded.

## Alternatives considered — all four were built and measured, then rejected

Recorded so they are not re-proposed:

1. **Median instead of mean / widen to TTM.** Barely moved (median 43.3K vs mean 44.7K): two
   of three months were contaminated, and the book only has 4 months of income history.
2. **IQR / outlier trimming on raw transaction amounts.** Actively harmful — it flagged the
   real S$7,500 salary as an outlier (it dwarfs dozens of small refunds) and collapsed the
   baseline to S$5,000. **With salary in the pool, no magnitude threshold separates
   "large but recurring" from "larger and one-off."** This is precisely why outlier detection
   now runs on the *residual*, AFTER streams are extracted — same technique, different
   position in the pipeline, correct result.
3. **Hard cutover to `profile.json` below N months of history.** Rejected on generalization:
   that field is set-once and never validated. For most users it is unset (→ projects **$0
   income**, worse than a noisy average) or stale, and it puts a discontinuity in every user's
   chart at the threshold month.
4. **A per-payee "this payee is recurring" flag.** Rejected by the owner's counter-example:
   *"kstl → they can send me 5000 one time bonus."* Same payee, two natures. Recurrence is a
   property of a **stream**, not of a counterparty.

Two more were tried during implementation and reverted (both recorded in code comments):

- **A cluster "coverage gate"** (a stream must explain ≥60% of its payee's traffic) suppressed
  the coffee false-positive but **killed the salary in the very book this work exists to fix** —
  that employer also sends one-off proceeds, so the salary covered only 3/8 of its payee's
  traffic. Coverage separates the two cases the *wrong way round* (salary 0.375 vs coffee 0.4).
- **Iterative one-off removal** cascaded: each removal shrinks the denominator, so the next item
  looks dominant, and the loop ate a real salary and then the refunds behind it. The threshold
  is now measured **once** against the original pool.

## Consequences

### Positive
- The forecast no longer inflates or deflates because of a single windfall or one-time expense.
- Streams appear as their own **named** events ("Salary", "Rent", "Netflix") in each projected
  month instead of one opaque "Living expenses" blob.
- Exclusions are **reported** (`excluded_one_offs`), so a surprising projection is explainable
  rather than mysterious.
- `services/forecast_scorecard.py` — repointed at the new baseline in the same change — now
  measures whether this actually improved accuracy, on real books, automatically.

### Negative / accepted
- **Detection needs history.** With <3 occurrences nothing is a stream; a brand-new book leans
  on the variable average and declared streams. Guarded so a first-ever paycheck is never
  excluded as a windfall (a side with no stream to carry it is never gutted).
- **Two heuristics remain tunable, not proven**: `ONE_OFF_POOL_SHARE` (0.15) and the cadence
  windows. Both are named constants with rationale, not magic numbers, and both are now
  measurable via the scorecard.
- **A coincidental pair can still surface as a `tentative` candidate** (two similar coffees a
  month apart). Contained, not eliminated: tentative never enters a projection.
- **Deviation from the RFC**: `profile.json` figures were specified as declared streams but are
  implemented as the unchanged empty-book fallback. Making them additive streams would
  **double-count** for any user who has both a profile figure and a detected salary — a severe
  and silent error. Declared streams cover the real capability (future changes) without it.

## Provenance

- **Decided by:** Claude, after the owner explicitly delegated the four open RFC questions
  ("I don't want to decide, you decide!") and declined a feature flag.
- **Design of record:** `.claude/rfcs/2026-07-27-cashflow-stream-model.md`.
- **Prior art:** [Plaid recurring transactions](https://plaid.com/blog/recurring-transactions/)
  (description + amount + cadence; ≥3 occurrences = matured; habitual-but-irregular spend
  deliberately excluded), [Numeric's cash-flow forecasting guide](https://www.numeric.io/blog/cash-flow-forecasting-guide)
  (operating vs non-operating; do not smooth lumpy items; layer declared future events),
  13-week direct-method practice.
- **Supersedes:** the "expense-only" and "does NOT feed cashflow" scoping decisions in
  `.claude/rfcs/phase2/sp3-subscription-detection.md` (never implemented).
