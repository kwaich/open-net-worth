---
title: Delete institutions by unlinking accounts
status: Accepted
date: 2026-09-12
deciders: Product owner, Codex
---

# Delete institutions by unlinking accounts

## Context

Institutions are shared, book-scoped account identities. Users need to correct a shared name or
remove an unused label, but an account can carry balances, transactions, lots, and other
financial records that must never be deleted as a side effect of registry cleanup.

## Decision

We will rename an institution globally and delete it only after explicit confirmation. Deleting
an institution atomically clears `institution_id` from every linked account, then removes the
institution row. It never cascades to account or financial data.

## Consequences

### Positive

- A shared label can be corrected once and reflected across the book.
- Registry cleanup cannot destroy accounts, balances, or transactions.

### Negative

- Deleted institutions leave formerly linked accounts ungrouped until a user assigns another
  institution.

### Follow-ups

- Institution aliases and merge remain deferred in
  `docs/prd/2026-09-12-account-model-follow-ups.md`.

## Alternatives considered

- Cascade-delete linked accounts — rejected because an institution is descriptive metadata, not
  the owner of financial records.
- Refuse deletion while accounts are linked — rejected because unlinking is safe and users need
  a way to remove a mistaken label.

## Agent provenance

- **Decided by:** Product owner and Codex.
- **Lane / branch:** `codex/manage-institutions`.
- **Alternatives weighed:** cascade delete, block deletion, and atomic unlink.
- **Driving artifacts:** `docs/adr/2026-09-12-normalize-account-institutions.md`.
- **Why this way (one line):** accounts retain their financial identity independently of their
  institution label.
