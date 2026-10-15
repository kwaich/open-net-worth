---
title: Accounts have no user-configured colour
status: Accepted
date: 2026-09-12
deciders: Codex orchestrating agent + repo owner
---

# Accounts have no user-configured colour

## Context

Account colour was a decorative preference rendered as dots and tints across the register,
pickers, sidebar, imports, and Lucius. It did not describe a financial property and made the
account editor ask users to make an unnecessary choice.

## Decision

Remove account colour from the persisted payload, REST and Lucius write schemas, account UI, and
account-derived display rows. Category colour remains because it conveys budgeting context.

## Consequences

- Accounts are identified by their name and type icon rather than a user-selected tint.
- The data migration irreversibly discards existing account colours.
- No new account writer can reintroduce the removed payload key.

## Alternatives considered

- Hide the picker but retain colour elsewhere — rejected because the preference would continue to
  affect UI without a user-facing meaning or edit surface.
- Keep the field for compatibility — rejected because the owner explicitly requested its removal
  from stored account data.

## Agent provenance

- **Decided by:** Codex orchestrating agent + repo owner
- **Lane / branch:** `codex/empty-state-automation-nudge`
- **Why this way:** account colour is purely decorative, while category colour remains meaningful.
