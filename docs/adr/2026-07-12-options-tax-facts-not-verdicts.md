---
title: Options tax — record facts, never verdicts; jurisdiction decides retroactively
status: Accepted
date: 2026-07-12
deciders: orchestrating Claude agent (options-e2e lane) + repo owner (direction confirmed in-session)
---

# Options tax — record facts, never verdicts; jurisdiction decides retroactively

## Context

The options premium money path booked a short write's received premium as an income
transaction with `taxable=True` stamped unconditionally (a hold-over from the
category-opaque-ids migration, whose decision #3 comment read "received premium is
unconditionally taxable"). Two problems surfaced when the owner asked "taxable or not
depending on the country, no?":

1. **Whether/when option premium is taxed is jurisdiction-dependent.** In SG/HK an
   individual's trading gains are generally not taxable at all; even in the US,
   written-premium tax is *deferred* until the position closes/expires/assigns and is
   then capital-gain-like — taxing on receipt is the wrong timing everywhere.
2. **The stamp landed the premium in the wrong bucket entirely.** The tax report's
   employment section counts `kind="income"` txns by `txn_is_taxable` — a stamped
   premium leg was reported as *earned/other (employment) income*. Meanwhile a long's
   sell-to-close proceeds appeared nowhere, and no realized option P&L (premium −
   buyback) was computed anywhere.

The repo already has the right seam: `services/tax.py` is a pluggable-jurisdiction
*reporting* layer (a `Jurisdiction` dataclass with `cgt_treatment`, deliberately no
rates), and the profile carries `tax_residency_country`. The write path pre-deciding a
tax verdict bypassed all of it — and a verdict baked in at write time cannot be
re-derived when the user's residency (or the law) changes. The owner's direction:
keep whatever we do clean and keep **all the data** so tax can always be looked back
at per country.

## Decision

We will keep tax policy entirely out of the options write path and derive it
retroactively in the report layer, from facts:

1. **No taxable stamp on option premium cash legs.** `_maybe_book_premium_leg` books
   the short-premium income leg with `taxable=None` (facts only), exactly like
   dividends. The per-transaction `taxable` override in the register remains the
   operator's final word.
2. **The deterministic external_id is the authoritative marker.**
   `option-premium:<contract_id>` / `option-close:<contract_id>` legs are excluded
   from the employment-income section via `_is_investment_income_txn` (signal 3) —
   even if an operator hand-stamps `taxable=True` — because they are trading income
   owned by the options section, never employment income.
3. **The tax report gains an `options` section** built on
   `options_service.realized_outcomes(date_from, date_to)`: per-contract realized
   premium P&L for contracts closed/expired in the tax-year window (short: premium −
   buyback; long: sale − premium; expired: close leg = 0 by definition). A `closed`
   contract with no recorded close price realizes `None` — unknown is never fabricated
   as 0 (the lots unknown-basis invariant) — surfaced via `incomplete_count`. Base
   figures are recomputed from native premium × current FX on every read. The section
   mirrors the jurisdiction's `cgt_treatment` tag (informational, no rates — the
   `_realized_section` convention) and folds `net_realized_base` into
   `taxable_total_base` the same unconditional way realized gains already do.

This supersedes the "received premium is unconditionally taxable" fragment of the
category-opaque-ids migration decision #3 (recorded in code comments/tests, not its
own ADR — no status flip to perform; the superseding is noted here).

## Consequences

### Positive
- A change of `tax_residency_country` (or jurisdiction rules) re-derives the entire
  options tax story from stored facts — nothing baked in, nothing to migrate.
- Options P&L is finally computed and reported once, in the right bucket, instead of
  premium-as-employment-income and invisible sell-to-close proceeds.
- Data completeness is honest: unrecorded close prices show up as `incomplete_count`
  rather than silently distorting totals.

### Negative
- The report's options figure is only as complete as the recorded close prices — a
  status-flip-only close leaves a `None` row the operator must backfill for an exact
  year total (deliberate: unknown ≠ 0).
- `assigned`/`exercised` outcomes are surfaced only as a `deferred_count`; their
  share-leg economics belong to the lot spine and remain a follow-up.
- The Tax tab UI does not yet render the new section (payload/AI-first; UI is a
  follow-up), so the look-back is visible via `get_tax_estimate`/API before it is
  visible on screen.

### Follow-ups
- Tax tab UI line for the options section (render rows + incomplete-count warning).
- Assignment/exercise recording (deferred from the options RFC) should stamp enough
  facts for the same look-back treatment.
- Consider surfacing per-jurisdiction premium *timing* (receipt vs realization) if a
  jurisdiction pack ever needs it; today realization-on-close/expiry is the single
  model.

## Alternatives considered

- **Keep `taxable=True` on short premium** — rejected: wrong bucket (employment),
  wrong timing (taxed on receipt), wrong in no-CGT jurisdictions; unfixable
  retroactively once stamped.
- **Stamp `taxable=False` instead** — rejected: still a baked-in verdict, just the
  opposite assumption; wrong for jurisdictions/operators where premium *is* taxable.
- **Resolve the stamp from `tax_residency_country` at write time** — rejected: a
  write-time snapshot of a mutable fact; a residency change would silently strand
  historical rows with the old verdict.
- **A user-managed "Options premium" category with `taxable_default`** — rejected:
  the opaque-ids migration deliberately retired system-seeded categories, and a
  category default still can't express "premium − buyback realized on close".

## Agent provenance

- **Decided by:** orchestrating Claude agent (options-e2e session), direction set by
  the repo owner in-session ("make it clean… have all data… tax is always something
  that can look back… depending on the country").
- **Lane / branch:** `lane/options-e2e` (worktree `.claude/worktrees/options-e2e`).
- **Alternatives weighed:** keep True stamp / stamp False / residency-resolved stamp
  at write / category-default — all rejected for baking a verdict into facts.
- **Driving artifacts:** `docs/superpowers/specs/2026-07-12-options-e2e-design.md`,
  `docs/rfc/investments/sys-options-data-model.md`, owner Q&A in-session; tests
  `tests/test_options_tax.py`.
- **Why this way (one line):** a tax verdict is a function of (facts × jurisdiction ×
  time) — store the facts, keep the function in the report layer where it can always
  be re-run.
