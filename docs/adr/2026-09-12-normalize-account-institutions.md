---
title: Normalize account institutions
status: Accepted
date: 2026-09-12
deciders: Product owner, Codex
---

# Normalize account institutions

## Context

The account `platform` payload string has become a cross-cutting grouping key for money
routing and custody. It is mutable separately on each account, although users understand an
institution as one shared object.

## Decision

We will store a book-scoped Institution entity and persist its id on accounts. User-facing
contracts use the term `institution`; `platform` is migrated and then rejected. The account
service is the sole resolver from a supplied name to an institution id.

## Consequences

### Positive

- Shared renames and routing use a stable identity.
- API, UI and Lucius use the same term users see.

### Negative

- Raw account store reads need hydration before they can render a name.
- The migration spans account, statement and investment consumers.

### Follow-ups

- Institution merge/delete and aliases remain deferred in
  `docs/prd/2026-09-12-account-model-follow-ups.md`.

## Alternatives considered

- Keep a renamed free-text field: rejected because duplicates and one-account-at-a-time rename
  remain.
- Global provider catalogue: rejected because personal labels need no shared global identity.

## Agent provenance

- **Decided by:** Product owner and Codex.
- **Lane:** `codex/institutions-model`.
- **Driving artifacts:** `docs/prd/2026-09-12-institution-registry.md` and
  `.Codex/rfcs/2026-09-12-institution-registry.md`.
- **Why this way:** a local registry supplies one stable financial identity without prematurely
  turning it into an external-provider catalogue.
