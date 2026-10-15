---
title: Keep account type and currency immutable after creation
status: Accepted
date: 2026-09-11
deciders: Codex orchestrating agent + repo owner
---

# Keep account type and currency immutable after creation

Supersedes [2026-09-11-account-type-is-immutable](2026-09-11-account-type-is-immutable.md).

## Context

Account type determines ledger classification and budget behavior. Account currency determines
how the account's raw transaction-leg balance is interpreted and converted to the book's base
currency. Changing either field after activity exists can silently reinterpret history, break
imports, or make transaction legs disagree with the account identity.

## Decision

We will make account type and currency creation-only. The edit UI presents both as read-only,
and REST, Lucius, service, and store update paths reject either field.

## Consequences

### Positive

- Existing transaction amounts retain their native-currency meaning.
- Account balances and base-currency valuation cannot be silently reinterpreted.
- Every supported update surface enforces the same identity rule.

### Negative

- Correcting a wrongly configured account requires a new account and a deliberate migration or
  reconciliation of activity.

### Follow-ups

- Keep type and currency only on account creation contracts.
- Design a dedicated account-migration workflow if preserving history across a correction becomes necessary.

## Alternatives considered

- Permit a currency update and convert historical transaction legs — rejected because it is a
  financial migration requiring explicit rates and audit treatment, not an account edit.
- Permit currency updates when an account has no transactions — rejected because conditional
  identity rules are difficult to explain and enforce across all writers.
- Hide the controls only — rejected because REST, Lucius, and internal callers would remain able to mutate them.

## Agent provenance

- **Decided by:** Codex orchestrating agent + repo owner
- **Lane / branch:** `codex/empty-state-automation-nudge`
- **Alternatives weighed:** historical conversion, an activity-dependent exception, and immutable identity fields
- **Driving artifacts:** owner request and ledger balance/value implementation, 2026-09-11
- **Why this way (one line):** changing type or currency reinterprets financial history, so it must be an explicit future migration rather than an ordinary edit.
