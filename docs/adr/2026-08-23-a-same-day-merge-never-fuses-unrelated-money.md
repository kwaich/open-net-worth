---
title: A same-day merge never fuses unrelated money
status: Accepted
date: 2026-08-23
deciders: debugging agent (Claude) + repo owner
---

# A same-day merge never fuses unrelated money

## Context

`services.recurring._merge_same_day` collapses same-payee + same-day ledger rows into one
event before clustering. It exists for a real reason, pinned by
`test_same_payee_same_day_rows_merge_into_one_event`: a Meridian Bank instalment booked as
1,960.00 + 118.30 on one day is the 1,200.00 charged every other month, and per-row
clustering saw only the fragment, reporting a bill that had shrunk.

It summed a day BLINDLY, and its own docstring recorded the trade-off as harmless —
"two genuinely separate charges from one payee on the SAME DAY also merge... this costs
itemisation detail, never accuracy."

That was wrong, and the owner's own book proved it. A monthly S$1,750.00 PayNow loan
repayment landed on 28 Jul 2026 beside an unrelated S$1,100.00 transfer from the same payer
(all seven of that counterparty's rows share one `payee_id`, so grouping was never the
issue). The day summed to S$6,234.50 — outside the ±5% `AMOUNT_TOLERANCE` — so July left the
cluster, the stream's last occurrence became 27 Jun, `_is_stale` marked it `ended`, and it was
demoted to a candidate and never projected.

The consequence was not lost itemisation. It was a **one-sided forecast**: the S$1,750.00/mo
BANKX FLEXILOAN outflow this inflow reimburses is a matured, active stream and kept projecting,
while the inflow that cancels it vanished. Free cash flow read S$9,900 − S$11,000.00 =
−S$1,100.00/mo when the truth was +S$880.00/mo — a S$1,980/mo error, on the number that
decides whether a S$3,300/mo commitment set-aside is affordable.

## Decision

A day is merged only as far as the payee's own history supports.

Days on which a payee booked **exactly one row** are unambiguous observations — anchors for
what that payee normally moves. `_merge_one_day` then tries, in order:

1. the whole-day sum, if it matches an anchor (the ordinary split booking);
2. the largest subset that matches an anchor, leaving the rest as separate events;
3. failing both, fusing the day — the pre-2026-08-23 behaviour, unchanged.

Step 3 is what keeps a stream that ALWAYS arrives split working: with no single-row day there
are no anchors, nothing can match, and the original merge stands. The Meridian Bank test passes
untouched for exactly this reason.

Subset search is capped at `_MAX_SAME_DAY_SUBSET = 6` rows; a payee booking seven rows on one
day is not the split-booking case and is not worth the combinatorics.

## Consequences

- A recurring stream survives an unrelated same-day payment from the same counterparty.
- Merging is now evidence-led rather than unconditional, so it can under-merge where a payee
  has no single-row history AND a genuinely split booking whose parts individually resemble
  nothing. That case falls to step 3 and behaves as before.
- The forecast asymmetry this caused was found only because the two halves of one arrangement
  (a loan payment and its reimbursement) were classified differently. Worth remembering as a
  smell: a stream whose exact counter-party mirror is NOT also a stream deserves a look.

## Agent provenance

Found while tracing why the Commitments tab showed "+$21K funding in" for Feb 2027 (that turned
out to be a separate mislabelling bug). The owner asked for the free-cash-flow figure behind a
set-aside affordability check; verifying it against a representative instance surfaced the unpromoted
`stream_candidates` entry, and the anchor rule was designed to fix it without weakening the
maturity gate or the case the original merge exists for.
