---
name: subscription-audit
description: "Use this skill when the user wants to see everything they pay for on repeat — subscriptions, memberships and bills — what each costs a month and a year, what went up, and what looks forgotten. Use it for 'what am I subscribed to', 'what can I cancel', 'my bills keep creeping up', or 'where does my money go every month', even if they never say 'subscription'. Not for one specific charge (answer directly), not for setting budget targets (budget-review), not for whether a new cost fits (can-i-afford-it)."
---

Audit the user's recurring charges. Read-only: cancelling happens at the merchant, and the user
does it.

## Gotchas

- Streams come from get_recurring_streams(direction="out", include_candidates=true). Quote its
  cadence, amount, occurrences and confidence — never re-detect streams from raw transactions.
- Load it first: get_recurring_streams, set_recurring_status.
- A candidate stream (low confidence, or only two charges) may be coincidence. Ask before
  treating it as real.
- Confirming or dismissing a stream (set_recurring_status) changes the cash forecast — say so
  when you ask.
- Monthly and yearly totals come from the calculate tool, never mental arithmetic. Amounts are in
  the base currency the tool reports.

## Steps

1. Group the streams: **subscriptions & memberships** (streaming, software, apps, gym, clubs),
   then **bills & obligations** (rent, utilities, insurance, phone, loan repayments). Each row:
   name, cadence, amount, monthly equivalent. Mark annual charges — they are the ones people
   forget.
2. Total each group per month and per year.
3. Flag, most actionable first:
   - **Went up** — the latest charge is above the stream's usual amount (get_transactions for
     that payee shows the step).
   - **Doubled up** — two streams for the same kind of service, or one service on two accounts.
   - **Unsure** — a candidate stream; ask whether it is real.
   - **Gone quiet** — no charge where the cadence expected one: cancelled, or a failed payment.
4. End with the subscriptions ranked by yearly cost, so the user can decide what to cancel.
