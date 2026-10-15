---
title: An impossible transfer rate is refused at the write choke
status: Accepted
date: 2026-08-23
deciders: debugging agent (Claude) + repo owner
---

# An impossible transfer rate is refused at the write choke

## Context

ADR `2026-08-23-a-transfer-figure-and-its-unit-belong-to-the-same-leg` fixed the caller that
booked ₫250,000,000 as S$250,000,000 on an SGD receivable: `ai/tools/_shared.py::
_txn_data_from_ai` split a caller's `amount` from its `currency` across two legs. That fix is
correct and pinned, but it is a fix at ONE caller. The ledger write choke itself accepted the
row without complaint, and would accept the same shape from `POST /api/transactions`, the
CSV/OFX/QIF/CAMT importers, a future tool, or a hand-built dict.

Nothing downstream could catch it either. `_normalise_leg` compares a leg's currency against
its own account's and converts if they differ — it never looks at the OTHER leg, so two legs
that are each internally consistent but jointly absurd pass cleanly. `_transfer_base_value`
picks whichever leg is already in base and stamps it. The error surfaced only because a human
read an absurd net-worth figure.

The follow-up recorded on that ADR was phrased as "refuse a transfer whose legs carry equal
amounts in different currencies". Implementing it showed that test is wrong in both
directions: it catches only an EXACT copy (a caller that copies-then-rounds, or converts with
a rate wrong by 1000x, walks past it), and it is permanently wrong for a genuinely 1:1 pair,
which a USD↔USDC wallet transfer really is.

One further constraint shaped the design. `core.fx.to_base` passes an unknown currency pair
through 1:1 by deliberate design (fail-soft: an unconverted reporting figure beats a crashed
page). Its result is therefore identical for "the rate is 1.0" and "this currency is not in
the table at all" — so no guard built on a conversion result can tell an honest 1:1 transfer
from one whose rate we simply do not know.

## Decision

We will refuse, at `services.ledger.resolve_transaction_row`, any `kind="transfer"` whose two
legs imply an exchange rate more than **an order of magnitude** away from the rate the app
actually knows for that pair.

- **A rate comparison, not an equality test.** The implied rate is `to_amount / from_amount`;
  it is compared against `known_rate(from_currency, to_currency)`. This catches the whole
  magnitude-error class and has no 1:1 blind spot.
- **`core.fx.known_rate` is new** and is the counterpart `to_base` cannot be: it returns
  `None` when either currency is absent from the rates table, rather than a number. `to_base`
  keeps its fail-soft 1:1 behaviour, unchanged.
- **Ignorance never refuses.** A `None` rate means the guard declines to judge.
- **Placed in `resolve_transaction_row`, not `create_transaction`,** so a PREVIEW refuses too —
  a confirm card must never render a row the write would then reject. And placed AFTER
  `_normalise_leg` (not in `_validate_legs`, which runs pre-normalisation on whatever
  currencies the caller happened to state), because the rate between the legs is only
  meaningful once both are in their accounts' own currencies.

Separately, and much smaller: `_txn_data_from_ai` gains a `derived_out` out-param, filled only
when a destination leg was derived ACROSS a currency boundary, surfaced by `add_transactions`
as a `leg_amount_derived` warning. `_currency_conversion_warning` structurally cannot report
this — it reads `original_amount`, which `_resolve_original` returns as `(None, None)` for
transfers by design.

## Consequences

### Positive

- The bug class becomes unrepresentable rather than fixed at one caller. Any writer producing
  an arithmetically impossible transfer is refused, with an error naming both figures, the
  implied rate, the known rate, and the amount the leg should have carried.
- The preview refuses too, so the confirm card cannot show a row that then fails.
- `known_rate` gives the codebase a way to ask "do we actually know this rate?" — a question
  it previously could not express at all.
- The derived-leg warning closes the "settles to $2.40 instead of zero" failure on a
  receivable paid off in tranches.

### Negative

- **A new way for a write to fail.** A legitimate transfer at a genuinely extreme rate — a
  redenomination, a currency whose cached rate is a year stale and has moved >10x — is refused
  until the rates cache is refreshed. Judged acceptable: 10x is far outside a spread, a stale
  cache, or a historical entry, and the error says exactly what it expected.
- The guard is only as good as the rates table. An empty or wrong cache means it silently does
  not fire (by the ignorance rule) — it is a backstop, never a substitute for callers that
  pair a figure with its unit correctly.
- `derived_out` is a third out-param convention in this area (`meta_out`, `diagnostics`,
  now `derived_out`). Consistent with the existing ones, but the pattern is accumulating.

### Follow-ups

- **A pre-existing test-infrastructure bug found while building this, NOT fixed here.**
  `tests/conftest.py::_reset_global_fx_and_profile` snapshots `core.fx._fx_rates_loader`
  before each test and restores it after. On the FIRST test of a session that snapshot is
  `None` — `services.fx_cache`, which registers the real loader at import, has not necessarily
  been imported yet — so the teardown pins `None` for the entire run. Every test after the
  first gets `_load_rates() == {base: 1.0}`, silently converting every non-base currency 1:1.
  Any test that believes it is exercising real FX conversion through the `rates=None` path is
  not. Left alone deliberately: fixing it would make FX real in tests that currently get 1:1
  and could flip assertions across the suite — its own change, with its own verification.
  `tests/test_transfer_rate_sanity.py` sidesteps it by patching the loader hook directly.

## Alternatives considered

- **Equal amounts in different currencies → refuse** (the originally-recorded follow-up) —
  rejected during implementation: misses any non-exact copy, and permanently refuses a real
  1:1 pair. Reasoning kept in `tests/test_transfer_rate_sanity.py`'s module docstring so the
  question is not relitigated from the ADR that first proposed it.
- **A tight tolerance (say 25%)** — rejected: a historical transfer entered at its own day's
  rate on a long-depreciating pair can legitimately sit a factor of two from today's, and a
  guard that fires on honest data gets disabled.
- **Warn instead of refuse** — rejected for this specific check. A magnitude error is not a
  judgement call, and the incident proved a warning in a tool result is exactly what a terse
  "Done" buries. The derived-leg case above IS a judgement call, and is a warning.
- **Guard in `_validate_legs`** — rejected: it runs before `_normalise_leg`, on the caller's
  stated currencies rather than the accounts', so it would compare the wrong pair.
- **Guard in `create_transaction` only** — rejected: previews would render rows the write
  refuses.

## Agent provenance

- **Decided by:** debugging agent (Claude), continuing the incident lane; the "rate, not
  equality" reshaping was raised before implementation and approved by the owner.
- **Lane / branch:** `main` (owner merged the predecessor fix mid-session).
- **Alternatives weighed:** equality vs. rate comparison; tolerance width; warn vs. refuse;
  placement across `_validate_legs` / `resolve_transaction_row` / `create_transaction`.
- **Driving artifacts:** a reported incident, 2026-08-23; predecessor ADR
  `2026-08-23-a-transfer-figure-and-its-unit-belong-to-the-same-leg`; tests
  `tests/test_transfer_rate_sanity.py` and FIX A3b in `tests/test_currency_atomic.py`.
- **Why this way (one line):** the fix repaired the caller that happened to be wrong; this
  makes the shape itself impossible to store, from any caller, without ever refusing on a
  rate we do not know.
