---
title: Remove category taxability
status: Superseded
date: 2026-09-21
deciders: Codex + repo owner
supersedes: docs/adr/2026-08-25-remove-the-tax-feature.md
superseded_by: docs/adr/2026-09-29-drop-transaction-taxable.md
---

# Remove category taxability

## Context

The 2026-08-25 tax-feature removal deliberately retained category-level tax tagging for a
possible future tax engine. That left an otherwise unused `taxable_default` field across the
category payload, REST API, Lucius tools, Planning response, and two category-management UIs.
The owner has now explicitly retired that category field end to end.

Transaction-level tax facts remain distinct: `transactions.taxable`, disposal `is_taxable`,
withholding, grant tax events, and tax lots describe individual financial events and remain.

## Decision

We will remove category-level taxability completely. Categories contain identity,
classification, presentation, and visibility only. Income transactions default taxable unless
their own tri-state `taxable` field explicitly says otherwise. A forward-only migration removes
`taxable_default` from every stored category payload; stale create clients cannot restore it.

The category REST setter and Lucius setter are deleted, the create contracts no longer advertise
or persist the field, and category rows no longer expose a derived `taxable` property. Planning's
taxable totals, while they remain, are computed from transaction-level facts.

## Consequences

### Positive

- Category management no longer exposes a tax concept with no active tax feature.
- Storage, REST, Lucius, and UI share one smaller category contract.
- A category rename or reassignment can no longer silently change an income transaction's tax
  treatment.

### Negative

- Users cannot mark all future transactions in one category non-taxable; each non-taxable income
  transaction must carry its own explicit fact.
- The discarded category preferences cannot be reconstructed by downgrade.

### Follow-ups

- A future tax engine must derive jurisdictional treatment from transaction and investment facts
  or introduce a newly designed contract; it must not revive this field implicitly.

## Alternatives considered

- Keep the field hidden for a future tax engine — rejected because it preserves dead data and a
  category-level verdict that can mutate historical interpretation.
- Remove every tax-named field in the product — rejected because transaction, withholding,
  disposal, grant, and lot fields are recorded financial facts outside category scope.
- Remove taxable Planning totals too — rejected because they can be computed correctly from the
  retained transaction-level fact without category metadata.

## Agent provenance

- **Decided by:** Codex implementing the repo owner's explicit request
- **Lane / branch:** current local checkout
- **Alternatives weighed:** retain hidden category metadata, remove all tax facts, or remove only category taxability
- **Driving artifacts:** owner request and `docs/adr/2026-08-25-remove-the-tax-feature.md`
- **Why this way (one line):** Taxability belongs to an individual financial event, not mutable category metadata.
