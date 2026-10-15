---
title: A transfer's figure and its unit belong to the same leg
status: Accepted
date: 2026-08-23
deciders: debugging agent (Claude) + repo owner
---

# A transfer's figure and its unit belong to the same leg

## Context

`ai/tools/_shared.py::_txn_data_from_ai` maps the AI tool's anchor-based transaction shape
(`account_id` + `direction`, with `from_account_id`/`to_account_id` as counterparty modifiers)
onto `create_transaction`'s symmetric two-leg schema. It carried three separate transfer
branches — one per anchor form — each assigning the leg currencies its own way.

Two of the three **split the caller's `amount` and `currency` across different legs**. In the
`explicit_from_id` and `direction="credit"` branches, the NUMBER went to the source leg while
the caller's UNIT went to the destination leg, and the source leg silently took its own
account's currency instead:

```python
"from_amount":   amount,                                              # the caller's number
"from_currency": item.get("to_currency") or _account_currency(from_id),  # NOT the caller's unit
"to_amount":     to_amount if to_amount is not None else amount,
"to_currency":   currency,                                            # the caller's unit, other leg
```

This fired on a representative book on 2026-08-23. Lucius logged a ₫430M loan repayment into a
VND bank from an SGD receivable, in the credit form a model naturally emits:

```json
{"amount": 250000000, "currency": "VND", "direction": "credit",
 "account_id": "<Mekong Bank, VND>", "from_account_id": "<Alex Tan's Loan, SGD>"}
```

The call is correct per the tool schema. The mapping handed `_normalise_leg` an amount already
wearing `SGD`, so the `cur != acc_cur` conversion was a no-op, no `original_amount` was
captured, and `_currency_conversion_warning` — which keys off exactly that field — stayed
silent. **₫250,000,000 was booked as S$250,000,000** on a receivable: a ~20,300x error with no
guard left standing, visible only as an absurd net-worth figure the owner happened to notice.

A second hazard sat in **all three** branches: a missing counterpart amount defaulted to
`amount` verbatim while taking the *other* account's currency. That default is exactly right
when both legs share a currency (the case it was written for) and fabricates an unconverted
figure across a currency boundary — S$12,340.76 arriving as ₫12,340.76.

Both are instances of the scar this same file already carries twice: FIX A (the Mekong Bank
XLSX incident) and FIX A2 (the SaaS-vendor/Meridian Bank incident), both recorded in
`tests/test_currency_atomic.py`. Each previous fix hardened the *stated* currency; neither
covered the currency the mapping **assigns on the caller's behalf**.

## Decision

We will collapse the three transfer branches into ONE mapping, under a single rule:

> **`amount`/`currency` describe the SOURCE leg. `to_amount`/`to_currency` describe the
> DESTINATION leg. The anchor (`account_id` + `direction` + which counterparty key was used)
> decides only WHICH account is on which side — never how the caller's figure and its unit are
> paired.**

`from_currency` is therefore always the caller's `currency`, in every anchor form. This is
already what the debit branch did and exactly what the `to_amount`/`to_currency` schema prose
says ("destination leg"), so no tool-schema wording changes.

A destination amount the caller did not supply is derived by `_counterpart_amount`: the same
number when both legs share a currency (exact, no FX call, no drift), otherwise converted at
today's rate via `svc.to_base`. **Never the raw number carried across a currency boundary and
relabelled.**

## Consequences

### Positive

- The incident shape books correctly: the SGD receivable is credited the converted ~S$12,340,
  the VND bank the stated ₫250,000,000.
- One mapping instead of three means a future currency rule cannot be applied to two branches
  and forgotten on the third — which is precisely how this survived two prior currency fixes.
- `check_duplicates` (`ai/tools/matching.py`) shares this choke, so the dedup preflight now
  fingerprints the same legs the write will store.
- The debit form with both legs stated explicitly is byte-identical to before.

### Negative

- **The credit form can no longer state the source leg's exact figure.** `to_currency` used to
  double as "the source leg's currency" in two branches; it now means what it says. A caller
  wanting both legs pinned exactly must use the debit form (`account_id` = source,
  `to_amount`/`to_currency` = destination) — the canonical shape, and the one Lucius's own
  retry used. In the credit form the source leg is FX-derived.
- A derived leg uses **today's** rate, which may not be the rate that actually posted. That is
  the same trade `settled_amount`'s "omit to convert at today's rate" already makes for
  expenses, but on a transfer it can leave a receivable a few units short of zero.

### Follow-ups

- Not built, deliberately deferred (see Alternatives): a warning when a leg was FX-derived, and
  a defense-in-depth refusal at the `create_transaction` choke for a transfer whose legs carry
  equal amounts in different currencies.
- The corrupt rows from the incident were already soft-deleted by the owner; the loan
  account nets to `0.0`. No data migration is needed.

## Alternatives considered

- **Refuse a cross-currency transfer with only one leg stated** (raise a prescriptive
  `ValueError` naming the field to send, mirroring how this file already refuses a missing
  `currency`) — rejected as the primary mechanism: it blocks a well-formed call and forces a
  round trip, and the model would supply a rate it derived the same way `to_base` would. Kept
  in reserve if derived-rate drift proves to matter in practice.
- **Bind `amount`/`currency` to the ANCHOR's leg** (destination in the credit forms) rather
  than always the source — semantically closer to what a model means by "₫250M arrived in
  Mekong Bank", and it books this incident correctly too. Rejected because it keeps three
  branches shaped differently, which is the structural cause here; and because with the
  same-currency fast path the two rules differ only in which leg holds the exact figure.
- **Convert silently, no warning** — the smallest diff, and what shipped. Rejected as a *final*
  answer rather than as this change: a plausible-but-slightly-wrong number is harder to catch
  than an absurd one. The absurd one is what saved this incident. Hence the follow-up.
- **Fix only `from_currency` and leave the copy default alone** — rejected: it fixes the
  incident shape and leaves the mirror (debit, cross-currency, `to_amount` omitted) live.

## Agent provenance

- **Decided by:** debugging agent (Claude), systematic-debugging skill, root cause confirmed
  against the owner's `sessions` transcript and `transactions` rows before any fix.
- **Lane / branch:** `main`, single-lane bug fix.
- **Alternatives weighed:** refuse vs. FX-derive vs. anchor-bound pairing vs. partial fix —
  presented to the owner, who chose implementation with test coverage.
- **Driving artifacts:** a reported incident, 2026-08-23 (session `99246462-…`, txns <!-- pii-lint: ok (session uuid) -->
  `txn_c47594df0000` / `txn_e868e0480000`); regression tests are FIX A3 in
  `tests/test_currency_atomic.py`, beside the FIX A / FIX A2 scars in the same file.
- **Why this way (one line):** three branches each pairing a figure with a unit their own way is
  the defect — one rule, applied once, is the only shape a fourth currency bug cannot hide in.
