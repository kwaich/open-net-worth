---
title: Declared recurring streams are retired; only detected streams feed the projection
status: Accepted
date: 2026-10-16
deciders: repo owner
---

# 2026-10-16. Declared recurring streams are retired

## Context

[2026-07-27](2026-07-27-cashflow-classified-streams.md) added `declare_recurring_stream` so
Lucius could record a FUTURE change that history cannot show (a raise, a new rent, an end date),
layered on top of detected streams.

In practice it was used for something else: restating income the history already showed. A
prompt rule told Lucius to declare a salary the user stated, so the same pay reached the
projection twice, once from the ledger and once from the declaration. Commit `a2d07e41a` ("a
declared stream replaces the history it restates") answered that by letting a declaration absorb
matching history on amount alone, in both the detected streams and the residual rows. That
silently swallowed other payers' income: a declared S$5,400 salary took a tenant's S$5,300 rent
off the projection. Persona QA called it a launch blocker.

## Decision

The concept is removed. A stream is either detected from the ledger (matured, or a tentative
one the user confirmed) or it does not exist.

- `recurring_baseline.resolve_baseline` builds `streams` from detected streams only.
  `_fold_history_into_declared` and the declared-to-stream conversion are deleted, along with the
  `start_date` / `end_date` window on a stream, which only declarations carried.
- The cash-flow projection, Next 30 days (`upcoming_income`, `upcoming_bills`), the daily
  briefing and the spending breakdown read the same detected streams. Nothing reads a declared row.
- The `declare_recurring_stream` and `remove_recurring_streams` tools and the `POST`, `PATCH` and
  `DELETE /api/recurring/declared` routes are gone. `get_recurring_streams` and
  `set_recurring_status` stay for detected streams, and `GET /api/recurring` and
  `POST /api/recurring/{key}/status` stay.
- What declarations were for is covered elsewhere: Commitments hold dated events, the profile's
  figures remain the empty-book fallback, and `simulate_cashflow` answers what-ifs.
- Existing `declared` rows stay in the `recurring_streams` table untouched. No migration, no
  destructive cleanup. `RecurringStreamStore` only reads and writes overlays and `overlays()`
  skips the rest.

## Consequences

A raise or a new rent shows in the projection once the ledger shows it, or after the user
confirms the stream. A brand-new book with fewer than three occurrences leans on the variable
average, as before. Income from different payers can no longer cancel each other, because
nothing matches on amount alone.

## Alternatives considered

- **Keep declarations, key the absorption on payee** (rejected). A declaration has no payee, and
  adding one makes the user restate what detection already knows.
- **Keep declarations only for future-dated changes** (rejected). The tool could not tell a
  future change from a restatement, and Commitments and `simulate_cashflow` already carry that.
- **Delete the stored rows** (rejected). Not needed for correctness, and a destructive migration
  on user data buys nothing.

## Agent provenance

- **Decided by:** repo owner, 2026-10-16
- **Driving artifacts:** `tests/test_cashflow_stream_projection.py` (independent income streams
  both project, a dormant declared row feeds nothing, no declare tool is registered)
- **Why this way (one line):** a second, user-asserted source of the same fact double-counted,
  and the fix for that swallowed real income, so the second source is removed.
