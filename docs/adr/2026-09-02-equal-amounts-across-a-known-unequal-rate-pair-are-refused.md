---
title: Equal amounts across a known, unequal-rate pair are refused; an unknown pair is warned
status: Accepted
date: 2026-09-02
deciders: debugging agent (Claude) + repo owner
---

# Equal amounts across a known, unequal-rate pair are refused; an unknown pair is warned

## Context

ADR `2026-08-23-an-impossible-transfer-rate-is-refused-at-the-choke` guards a cross-currency
transfer by comparing the rate its two legs imply against `core.fx.known_rate`, refusing only
past a 10x band. That ADR explicitly REJECTED the earlier-proposed "equal amounts in different
currencies" test, for two reasons: it catches only an exact copy, and it is permanently wrong
for a genuinely 1:1 pair (USD↔USDC).

On 2026-08-31 a headless automation (a small model reading broker statements) booked two FX
conversions with the SAME number on both legs — the shape of "€10,000 → $10,000" on a pair
whose known rate is ~1.16, and "€2,500 → S$2,500" on a pair at ~1.47. The run's transcript
showed the model computing the rate in its reasoning and then passing `to_amount = amount` in
the actual tool call: it knew the rate and never multiplied by it. Both rows sailed through
the 10x guard — a 1.16x or 1.47x miss is nowhere near an order of magnitude — and surfaced
only as REVIEW-flagged rows a human noticed.

So the 2026-08-23 reasoning stands on its first point (an equality check is not a substitute
for the magnitude guard) and needs qualifying on its second: the equality check is wrong for a
1:1 pair ONLY if it cannot tell a 1:1 pair from an unequal one. `known_rate` can. Refusing
equal amounts exactly when the app KNOWS the pair is not 1:1 has none of the USD↔USDC blind
spot the original rejection was about.

Two further forces shaped the shape of the escape hatch and the edit path:

- The duplicate guard already sets the pattern for "refuse, but let a caller who knows better
  through": `DuplicateTransactionError` (409) with `allow_duplicate` → `force=`. The ledger's
  existing naming mismatch between those two layers (the tool says `allow_duplicate`, the
  service says `force`, the hint text says `force=true`) has confused callers before.
- `update_transaction` rebuilds a row through `create_transaction`. The 10x guard has no
  bypass, so no stored row can ever violate it and re-running it on edit is harmless. A guard
  WITH a bypass is different: a forced row exists in the store, and re-judging it on every
  later note/category edit would refuse an edit for a shape the store already holds.

## Decision

We will refuse, in `services.ledger.resolve_transaction_row` (so preview and write agree),
any `kind="transfer"` whose two legs carry the **exact same number** (to 2 dp) in different
currencies **when `known_rate` says the pair is not 1:1** — the rate is known and more than
`_TRANSFER_ONE_TO_ONE_TOLERANCE` (2%) away from 1.0. The error is
`ImplausibleTransferAmountError` (`ConflictError`, code `implausible_transfer_amount`, 409),
naming both figures, the known rate, and the amount the destination leg should have carried.

- **A sibling of the 10x guard, not a replacement.** `_assert_transfer_rate_plausible` still
  runs first and unconditionally; `_assert_transfer_amount_converted` runs after it and only
  on the exact-equal shape. Two different numbers are never this guard's business.
- **Ignorance never refuses — it warns.** A `None` rate (either currency absent from the FX
  cache, which is where every crypto pair sits, since the cache only ever holds Yahoo fiat
  crosses) declines to judge, exactly as the 10x guard does. Instead `add_transactions`
  appends a non-blocking `unverified_equal_amount_transfer` warning (single-row and bulk, the
  latter tagged per `txn_id`) telling the agent to double-check it converted and to repair
  with `update_transaction` if not. Once the pair later becomes known the same shape is
  refused outright; the escalation needs no configuration.
- **One bypass, one name at every layer.** `force_transfer_rate: bool` on the
  `add_transactions` tool, on `create_transaction` / `bulk_create_transactions`, on
  `preview_create`, and on `resolve_transaction_row`. It bypasses ONLY this guard, never the
  10x one, and never changes the amounts — it books the row as stated. The same word top to
  bottom, deliberately unlike `allow_duplicate` → `force`.
- **An edit re-judges only the legs it changes.** `update_transaction` rebuilds with
  `force_transfer_rate=not _leg_changed`: untouched legs were accepted by the write that
  stored them; a leg the edit actually changes is judged fresh, exactly as on create.
- **2% is the parity band, not a tolerance on the guard.** It exists so a stablecoin's own
  de-peg wobble (1.01) never trips a check that is meant for 1.16 and beyond.

## Consequences

### Positive

- The "computed the rate, never applied it" mistake — the one small models make most, because
  the right rate is already in their own reasoning — is refused at write time for every fiat
  pair the book already uses, with the correct figure in the error. The preview refuses too.
- A pair the app cannot judge still gets a nudge in the tool result, so the agent has a
  chance to catch its own error before a human reads an absurd balance.
- The bypass has the same shape as the duplicate one, so a caller who has learned one has
  learned both — and its name does not change between the tool and the service.
- A row booked at parity on purpose stays editable.

### Negative

- **A new way for a write to fail on a fiat pair.** A transfer that genuinely cleared at the
  same number in both currencies — a stale broker quote, a fee-inclusive net settlement —
  is refused until re-sent with `force_transfer_rate=true`. Judged acceptable: the message
  says exactly that, and the honest case is rare against the mistake it catches.
- The guard is only as good as the FX cache. An unknown pair is a warning, which the
  2026-08-23 ADR's own "warn vs. refuse" bullet notes is exactly what a terse "Done" buries.
  Accepted knowingly: refusing on a rate we do not have would break legitimate writes for a
  reason the user cannot act on, and the warning is strictly better than the silence before.
- The edit-path rule means a forced row that is later edited into a DIFFERENT equal-amount
  shape (both legs changed together, still equal) is judged fresh and may be refused — the
  caller must pass the bypass again. Correct, but a surprise the first time.
- A second `_assert_*` in `resolve_transaction_row`'s transfer branch. Two is fine; a third
  should probably become a small pipeline.

### Follow-ups

- The 2026-08-31 run also narrated an idempotent `dispose_lots` hit as fresh work in its
  summary. Separate defect in automation summary narration; not touched here.
- The `duplicate_hint` text still says `force=true` where the tool parameter is
  `allow_duplicate`. Pre-existing; not touched here.
- 2026-09-19: both of the above are closed. `dispose_lots` narrates an idempotent replay as
  ALREADY booked (single call and batch, the latter with an `already_booked` count beside
  `succeeded`), and the refusal text `add_transactions` surfaces names `allow_duplicate=true`
  — rewritten at the tool boundary (`_names_the_tool_flag`); the service and REST keep `force`.
- The 2026-08-23 ADR's "Alternatives considered" bullet on equality is now qualified by this
  record. Its `status` is unchanged — the 10x decision stands in full.

## Alternatives considered

- **Leave the 10x guard as the only guard** — rejected: it cannot see a 1.16x miss, and the
  incident showed the miss is the common one for a model that already has the rate in hand.
- **Tighten the 10x band to, say, 25%** — rejected for the reason the 2026-08-23 ADR gave: a
  historical transfer entered at its own day's rate on a long-depreciating pair legitimately
  sits a factor of two from today's, and a guard that fires on honest data gets disabled.
  Equality is a different signal from "off by a percentage": it is the source figure copied.
- **Fetch a live rate at write time before deciding** — rejected: the guard already reads the
  same cache the app converts with, refreshed by the price loop for every currency in use;
  a network call in the write choke adds a failure mode and does not help the crypto case,
  which the cache never holds anyway.
- **Warn only, never refuse** — rejected for a known pair: the 2026-08-23 ADR's own finding
  that a warning in a tool result is what a terse "Done" buries applied directly to this
  incident. Kept for an unknown pair, where refusing would be acting on ignorance.
- **Reuse `allow_duplicate` / `force` as the bypass** — rejected: they mean "I know this
  looks like a row that already exists"; conflating the two would let one flag silence two
  unrelated refusals.
- **Skip the guard entirely on the edit path** — rejected: a correct row edited INTO the bad
  shape is the same mistake arriving late. `not _leg_changed` judges only what moved.

## Agent provenance

- **Decided by:** debugging agent (Claude), tracing the 2026-08-31 automation run; the
  "make it a refusal with a bypass like duplicates", "keep it working for USD↔USDC", and "warn
  on an unknown pair rather than stay silent" reshapings were each raised by the owner
  mid-session and adopted.
- **Lane / branch:** `main`, two commits (guard + bypass; then warning + edit-path rule).
- **Alternatives weighed:** 10x-only vs. sibling equality guard; tighter band; live fetch;
  warn vs. refuse; reusing the duplicate bypass; edit-path re-judging.
- **Driving artifacts:** the 2026-08-31 automation run's transcript and payload; ADRs
  `2026-08-23-a-transfer-figure-and-its-unit-belong-to-the-same-leg` and
  `2026-08-23-an-impossible-transfer-rate-is-refused-at-the-choke`; tests
  `tests/test_transfer_rate_sanity.py` (guard, bypass, parity band, edit path) and
  `tests/test_currency_atomic.py` (unknown-pair warning).
- **Why this way (one line):** the equality test the earlier ADR rejected is wrong only when
  it cannot tell a 1:1 pair from an unequal one — `known_rate` can, so refuse exactly when it
  says "not 1:1", warn when it says "don't know", and let one clearly-named flag through.
