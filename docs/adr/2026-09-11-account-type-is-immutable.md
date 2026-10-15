---
title: Keep account type immutable after creation
status: Superseded
date: 2026-09-11
deciders: Codex orchestrating agent + repo owner
---

# Keep account type immutable after creation

Superseded by [2026-09-11-account-identity-is-immutable](2026-09-11-account-identity-is-immutable.md).

## Context

The seven account types derive an account's ledger classification, net-worth band,
spendability, and budget behavior. A post-create change could turn a credit account into cash
or vice versa, including retiring its payment envelope and its assignment history. The edit UI,
REST endpoint, and Lucius tool previously exposed that mutation.

## Decision

We will make account type creation-only. The edit UI shows it as read-only; the REST endpoint,
Lucius tool, account service, and account store reject a type field in an update.

## Consequences

### Positive

- Account classification cannot silently invalidate budget or ledger assumptions.
- A credit card's payment-envelope history cannot be destroyed by a retype.
- Every supported write surface has the same rule.

### Negative

- Correcting a wrongly classified account requires creating the correct account and moving or
  reconciling its activity deliberately.

### Follow-ups

- Keep account creation as the only surface that accepts a type.
- Add an explicit migration flow only if users need to convert an account while preserving history.

## Alternatives considered

- Permit type changes and reconcile side effects — rejected because a retype changes financial
  meaning and can destroy budget history.
- Hide the picker only — rejected because API, tool, and internal callers could still mutate it.
- Allow type changes only when an account has no activity — rejected because it creates a
  conditional invariant that is difficult to explain and enforce consistently.

## Agent provenance

- **Decided by:** Codex orchestrating agent + repo owner
- **Lane / branch:** `codex/empty-state-automation-nudge`
- **Alternatives weighed:** controlled reconciliation, UI-only hiding, and a universal immutable rule
- **Driving artifacts:** owner request and Account editor screenshot, 2026-09-11
- **Why this way (one line):** type is structural financial data, so the safe default is to require a new account rather than reinterpret an existing one.
