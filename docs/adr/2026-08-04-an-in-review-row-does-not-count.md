---
title: An in-review row does not count — `needs_review` becomes a gate, not a flag
status: Accepted
date: 2026-08-04
deciders: orchestrating agent (Claude) + repo owner
---

# An in-review row does not count — `needs_review` becomes a gate, not a flag

## Context

We already have the review *primitive*. `Transaction.needs_review` exists
(`models/transaction.py:143`), the create choke sets it from
`core.txn_sources.default_needs_review` (`services/ledger.py:1010`, `:1528`), the bulk-update
path accepts it as an approve/reject verb (`:2048`), and `approve_import_batch` clears it for a
whole import (`:2171`). The register renders it and flips it optimistically
(ADR `2026-07-30-a-flag-flip-paints-optimistically-a-derived-change-refetches`).

Two facts make it advisory rather than load-bearing:

1. **Nothing reads it when computing money.** `services/accounts.py`, `services/envelopes.py`,
   `services/budgets.py`, the net-worth readers, and `services/aggregation/` contain **zero**
   references to `needs_review`. A flagged row moves the account balance, net worth, and envelope
   spend the instant it is created. The flag is a UI annotation on a row that already counts.
2. **Bank/broker connector syncs are excluded by design.** `REVIEW_ON_CREATE_SOURCES`
   (`core/txn_sources.py:150`) is `{ai, csv_import, qif_import, agent, email, recurring,
   automation}` — the comment states "trusted feeds stay out: bank/broker connector syncs carry
   authoritative data". So the highest-volume unattended ingest path never enters review at all.
   Separately, the set has drifted: `ofx`, `camt`, `mt940`, `xlsx`, and `pdf` imports are *file*
   imports with the same trust profile as `csv_import`/`qif_import` but are absent from it.

A competitor teardown (Zerosum's *Bank Sync* / *Reviewing, matching & linking* help articles,
2026-08-04) made the distinction explicit: their imported rows render inline in the register with
an amber edge and **do not affect balances or the budget until approved**. Ours look the same and
behave differently, which is the worse of the two failure modes — the affordance implies a gate
that is not there.

This also compounds a defect already on the board: the 2026-07-31 duplicate-card-payment incident
("DEDUP SURFACING") turned on a review item that was correctly created and never surfaced. ADR
`2026-08-01-refuse-strong-duplicates-at-the-write-choke` responded by moving *strong* duplicates
to outright refusal, noting the review queue "only protects a book someone actually reads". That
ADR fixed the unambiguous case by removing it from the queue. It did not make the queue itself
load-bearing, which is what remains.

## Decision

We will make `needs_review` a **gate**: a row with `needs_review=True` is excluded from every
derived money figure — account working balance, net worth, envelope/budget spend, cashflow, and
runway — until it is approved. It stays visible in the register, sorted in place, and is included
in explicitly review-scoped reads.

Concretely:

- Exclusion happens in **`services/`**, at the readers that fold rows into a figure — not by
  filtering at the store, which would hide the rows from the register that must show them.
- Every affected read reports the excluded magnitude alongside the figure (a `pending_base` /
  `in_review_count` companion), so a balance can never differ from the bank without the UI being
  able to say by how much and why.
- **Connector syncs enter review** — `REVIEW_ON_CREATE_SOURCES` gains the connector sources.
  "Authoritative" describes the *data's provenance*, not the owner's *awareness of it*, and the
  bank being right is exactly why an unreviewed feed row silently moving net worth is the case
  worth gating.
- The set also gains the missing file-import sources (`ofx`, `camt`, `mt940`, `xlsx`, `pdf`) —
  their absence is drift, not a decision.
- A **manual hand entry stays out of review**, unchanged. The owner's own entry is the owner's
  own decision.

## Consequences

### Positive

- The register's amber affordance becomes true. "This has not counted yet" is a state the system
  actually enforces rather than a label.
- The duplicate-card-payment class of incident stops silently moving money: an unreviewed
  duplicate is inert until someone looks at it.
- It makes pending→posted representable at all (see the companion ADR on re-delivered external
  refs) — a pending row can be shown without it moving the balance.
- Net worth gains a defensible definition against the bank: cleared, approved, and reconciled
  become a coherent ladder instead of three unrelated booleans.

### Negative

- **This is a broad, cross-cutting read-path change** touching every money reader in `services/`.
  It is the single most invasive item in the import-parity program and carries real regression
  risk against the three locked flows (① Foundation, ② Budgeting, ③ Investments), whose lock
  suites will need to be extended rather than merely kept green.
- **Numbers will move on the owner's book at rollout.** There are 16+ pending review items
  outstanding (oldest 2026-06-15) plus whatever connector rows the new source-set flags on first
  sync. Net worth will drop by the pending magnitude until those are triaged. This needs a
  one-time reconciliation pass, not a silent migration.
- Gating connector syncs adds review volume to the highest-throughput path. Without the
  surfacing work (still open on the board) this makes an unread queue *worse*, not better — so
  the surfacing is a hard prerequisite, not a follow-up.
- A third state between "not there" and "counted" is genuine new conceptual load for a
  self-hoster, and every read that reports a figure now has a companion figure to render.

### Follow-ups

- Extend `tests/test_foundation_acceptance.py` and `tests/test_budgeting_acceptance.py` with the
  gate: a `needs_review=True` row must not move balance/net-worth/envelope spend; approving it
  must move all three by exactly its base amount.
- Wire the DEDUP SURFACING board item first — the gate is not shippable without a queue the
  owner can see.
- One-time reconciliation pass over the existing pending queue before the gate goes live.
- Update `CLAUDE.md`'s Key invariants and `docs/INVARIANTS.md` with the gate as a stated invariant.
- UI Quality Gate applies: acceptance file, `make qa-e2e`, `make ui-gate`, screenshot evidence.

## Alternatives considered

- **Leave `needs_review` advisory; fix only the surfacing** — rejected because it preserves the
  exact mismatch the affordance creates, and because pending→posted has no representation without
  a state that does not count.
- **Filter at the store / repository layer** — rejected: it would hide in-review rows from the
  register, which is precisely where they must appear, and it violates the rule that all monetary
  math lives in `services/`.
- **A separate `pending_transactions.json` staging store** — rejected: it duplicates the ledger's
  resolution logic (legs, base snapshot, splits, rules, payee, category precedence), and
  `resolve_transaction_row` already exists to render a would-be write without storing it. A
  parallel store would drift from the choke point.
- **Keep connector syncs out of review** — rejected on the reasoning above: provenance
  authority is not the same as owner awareness. Noted as reversible if review volume proves
  unmanageable in practice.
- **Gate only `cleared=False` rows** — rejected: `cleared` is reconciliation state against a
  statement, an orthogonal axis. Overloading it would break the reconcile flow.

## Agent provenance

- **Decided by:** orchestrating Claude agent + repo owner (in-session, 2026-08-04)
- **Lane / branch:** `feature/automations-redesign` (decision recorded only; no implementation)
- **Alternatives weighed:** advisory-flag-plus-surfacing · store-layer filter · separate staging
  store · connector syncs exempt · overload `cleared`
- **Driving artifacts:** Zerosum help-centre teardown (*Bank Sync*, *Reviewing, matching &
  linking imported transactions*, 2026-08-04) · board item "DEDUP SURFACING" (2026-07-31) · ADR
  `2026-08-01-refuse-strong-duplicates-at-the-write-choke`
- **Why this way (one line):** the register already shows a row as "not counted yet", so the
  honest fix is to make that true rather than to remove the claim.
