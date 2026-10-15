---
title: Refuse missing FX conversions
status: Accepted
date: 2026-09-25
deciders: Codex implementation team, requested by repository owner
---

# Refuse missing FX conversions

## Context

Fresh multi-currency onboarding could persist foreign opening balances as base-currency
amounts at an invented 1:1 rate. The warning existed only in logs. Historical-rate locking
then correctly preserved an incorrectly created stamp. Portfolio refresh requested only
market-position currencies, leaving cash-only currencies absent indefinitely.

## Decision

We will refuse a nonzero cross-currency conversion unless both rates and their ratio are
finite and positive. `MissingFxRateError` identifies the pair and maps to HTTP 503. Exact
zero and same-currency amounts do not need rates. Per-unit portfolio conversions follow
the same rule without rounding prices to cents.

Account creation resolves missing FX through a bounded shared fetch before the database
transaction. Lucius dispatches that blocking service off its event loop. Portfolio live
refresh includes cash-account, owned-asset and original cost currencies.

Original position cost currency is independent of quote currency; onboarding preserves it
without converting the source basis at today's FX. Lot and grant ownership guards apply
to the currency as well as the amount.

## Consequences

### Positive

No new fabricated conversion is silently accepted or shown. Valid historical base stamps
remain locked. Original foreign cost basis survives the tool confirmation and write paths.

### Negative

A report requiring unavailable FX returns an actionable error until rates recover. A new
foreign account can take up to the bounded lookup timeout, and cannot be saved with a
nonzero opening if the provider supplies no valid rate.

### Follow-ups

Existing 1:1 stamps are not automatically rewritten: parity alone does not establish a
bad historical rate. Repair only records whose provenance proves the missing-rate defect,
through normal audited write paths after preserving evidence.

## Alternatives considered

- Keep 1:1 and add a warning: rejected because consumers still receive fabricated money.
- Return zero: rejected because this falsely removes assets or liabilities.
- Rewrite every parity stamp: rejected because legitimate rates and historical locks exist.

## Agent provenance

- **Decided by:** Codex root and focused implementation agents; independent code review.
- **Lane / branch:** existing working tree on main; unrelated edits preserved.
- **Driving artifacts:** fresh-user onboarding reproduction; `test_core_fx.py`,
  `test_account_fx_readiness.py`, `test_position_cost_currency.py`.
- **Why this way:** an explicit unavailable conversion is safer than a plausible wrong total.
