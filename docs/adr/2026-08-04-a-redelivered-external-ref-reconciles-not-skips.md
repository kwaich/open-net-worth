---
title: A re-delivered `external_ref` reconciles against the existing row — it is not skipped
status: Accepted
date: 2026-08-04
deciders: orchestrating agent (Claude) + repo owner
---

# A re-delivered `external_ref` reconciles against the existing row — it is not skipped

## Context

Our ingestion idempotency floor treats a known `external_ref` as "already have it, move on".
`ledger.bulk_create_transactions(dedup_by_external_ref=True)` (`services/ledger.py:1410-1415`)
skips any row whose `(source, ref)` pair already exists live in the ledger, mirroring
`TransactionStore.has_external_ref`. That is correct for the case it was built for — an
overlapping re-sync delivering the *same* facts twice.

It is wrong whenever the provider re-delivers the same identity with **different facts**. Two
real cases:

1. **Pending → posted.** A card swipe arrives pending, then settles, often with a different
   amount (tip, FX settlement, fuel hold) and a different date. We currently side-step this by
   discarding pending rows entirely: `services/aggregation/plaid.py:280` returns `None` for
   `plaid_txn["pending"]`, with the comment that pending txns only get a stable id once posted.
   The cost is that today's spending is invisible until it settles — for a card-heavy book that
   is most of the recent register.
2. **A correction to an already-booked row.** A charge posts at $50 and the owner reconciles it.
   Days later the bank corrects it to $52.30. The provider re-delivers the same ref with a new
   amount, the dedup floor sees a known ref, and the correction is **silently dropped**. The
   reconciled balance is quietly $2.30 wrong against the bank, permanently, with no audit event
   and nothing on screen.

`_maybe_adopt_settled_amounts` (F16c, `services/ledger.py:2485`) does not cover this. It fires
only when the absorbed row is feed-grade **and the canonical is estimate-grade** (no
`import_batch_id`, source `None` or `email`). A reconciled bank row is feed-grade on both sides,
so the guard declines. F16c solves "the email receipt guessed, the feed knows" — a different
problem.

Zerosum's *Bank Sync* and *Reviewing, matching & linking* articles document the shape we lack:
the provider's own pending→posted reference is treated as authoritative; when it is absent they
fall back to a bounded heuristic (same direction · compatible merchant · posted date within 3
days · amount change ≤20% · unique candidate, else leave a visible duplicate rather than guess);
and a bank correction to a *reconciled* row waits as a review item that, on approval, adopts the
bank amount **and keeps the row reconciled**.

## Decision

We will make re-delivery of a known `external_ref` a **reconciliation**, not a skip.

- The dedup floor stops being a boolean. On a known ref it compares the incoming row against the
  stored one on the material fields (**amount, date, direction, cleared/posted state**). Identical
  → skip, exactly as today. Materially different → it is a **correction**.
- **A correction never silently overwrites.** It lands as a review item carrying both sides
  (stored vs. delivered), resolvable as approve (adopt the bank's values) or reject (keep ours,
  and remember that decision — see the companion ADR on remembered rejections).
- **Approving a correction preserves `reconciled_at`.** Adopting the bank's amount is what makes
  the row match the bank; forcing a re-reconcile would be backwards.
- **We ingest pending rows** rather than dropping them. A pending row is created
  `needs_review=True` and therefore does not count (see the companion ADR on the review gate) —
  which is the precondition that makes ingesting them safe.
- **Pending→posted resolution prefers the provider's own reference** where the provider supplies
  one. Where it does not, we fall back to a bounded heuristic over the *same account only*, and
  when more than one candidate fits we **leave both visible rather than guess** — consistent with
  the existing matching engine's refusal to auto-resolve ties (`swap_review_canonical`, the
  banded WEAK tier, ADR `2026-07-29-one-matching-engine-instructions-as-policy`).
- The heuristic itself is expressed as a band in **`services/matching.py`**, not as new bespoke
  logic in each provider adapter. There is one matching engine; ADR
  `2026-07-29-one-matching-engine-instructions-as-policy` settled that, and a pending→posted
  tier belongs inside it.

## Consequences

### Positive

- Today's card spending becomes visible on the day it happens, which is most of the value of a
  bank feed for a daily-use book.
- A bank correction can no longer silently desynchronize a reconciled balance — the one class of
  error that reconciliation exists to prevent.
- Every amount change acquires provenance: a review item and an audit event, instead of a
  dropped delivery.
- Pending→posted lives in the one matching engine, so its tuning, policy, and golden-book
  coverage come for free rather than being reinvented per provider.

### Negative

- **The dedup floor stops being O(1)-cheap.** It currently answers a set-membership question; it
  will need to load and compare the stored row. On a full re-sync this is a real cost.
- Ingesting pending rows increases row volume materially, and every pending row eventually
  becomes a resolution decision. Provider adapters that today return `None` early get more
  complex.
- The fallback heuristic is a guess with a tolerance, and tolerances are exactly where the
  matching engine has historically produced false pairings (see the two `must_not_pair` goldens
  in `backend/scripts/eval_coverage.py`). It needs golden coverage before it ships, not after.
- Providers differ in whether a pending row's id survives settlement. Where it does not, the
  heuristic is the *only* path, and a missed match shows as a visible duplicate — honest, but
  more review work than a silent (wrong) merge.

### Follow-ups

- Add a pending→posted band to `services/matching.py` with explicit tolerances, plus golden
  fixtures — including a `must_not_pair` case for two same-day same-amount pending rows.
- Extend `backend/scripts/eval_coverage.py` with a pending→posted dimension so the tolerances are
  measured rather than remembered.
- Regression test the reconciled-correction path end to end: reconcile, re-deliver with a changed
  amount, assert a review item exists, assert approval adopts the amount and `reconciled_at`
  survives.
- Revisit `aggregation/plaid.py:280` and every sibling adapter for the pending short-circuit.
- Record the material-field comparison set in `docs/DATA_MODEL.md`.

## Alternatives considered

- **Silently adopt the new values on re-delivery** — rejected: it mutates a reconciled row behind
  the owner's back, which is the failure mode reconciliation exists to prevent.
- **Keep skipping; surface a periodic drift report instead** — rejected: it defers the discovery
  to a report nobody reads, and the board already has one incident (2026-07-31) caused by a
  correct signal nobody saw.
- **Keep dropping pending rows** — rejected by the owner: the feed's daily value is precisely the
  un-settled activity.
- **Handle pending→posted inside each provider adapter** — rejected: five-plus adapters would
  each grow their own tolerances, directly against the one-matching-engine decision.
- **Treat a changed amount as a brand-new transaction** — rejected: it double-counts, and the
  identity the provider supplied says plainly that it is the same event.

## Agent provenance

- **Decided by:** orchestrating Claude agent + repo owner (in-session, 2026-08-04)
- **Lane / branch:** `feature/automations-redesign` (decision recorded only; no implementation)
- **Alternatives weighed:** silent adoption · drift report · keep dropping pending · per-adapter
  handling · treat as new row
- **Driving artifacts:** Zerosum help-centre teardown (*Bank Sync*, *Reviewing, matching &
  linking imported transactions*, 2026-08-04) · ADR
  `2026-07-29-one-matching-engine-instructions-as-policy` · F16c
  (`_maybe_adopt_settled_amounts`, `services/ledger.py:2485`)
- **Why this way (one line):** the provider re-delivering an identity with new facts is
  information, and the dedup floor was throwing it away.
