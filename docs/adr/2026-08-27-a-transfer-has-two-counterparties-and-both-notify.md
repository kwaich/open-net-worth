---
title: A transfer has two counterparties and both notify — F03 must not veto the pair
status: Superseded
date: 2026-08-27
deciders: orchestrating agent (Claude) + repo owner
---

# A transfer has two counterparties and both notify — F03 must not veto the pair

> **Superseded by [2026-09-02-the-emitter-is-the-unit-of-identity-not-the-namespace](2026-09-02-the-emitter-is-the-unit-of-identity-not-the-namespace.md).** The same-route
> transfer exemption recorded here was shape-scoped: it could not reach an expense, so the
> identical bug recurred on 2026-08-28 (one giga charge, a bank alert and a merchant receipt).
> It was also too broad within its shape, exempting any two same-route transfers regardless of
> who announced them — which left two genuinely distinct same-day payments auto-merge-eligible.
> The route was a proxy for "two counterparties"; the successor tests the emitter directly and
> retires this exemption. The bug and reasoning below stand as the record of how it was found.

## Context

A Beacon Bank → Meridian Bank card bill payment of S$180.00 produced two ledger rows:

| row | date | sender | subject |
|---|---|---|---|
| `txn_606692ee0000` | 24 Aug | `alerts@bank-a.example` | "Beacon Bank Alert – Successful payment to another bank's card" |
| `txn_b211fd800000` | 26 Aug | `alerts@bank-b.example` | "Meridian Bank Alerts – Credit Card/Ready Credit Payment Received" |

One movement. The sender's bank emailed when it sent; the receiver's bank emailed when it
received, two days later (GIRO settlement lag). It is the only same-amount pair in that route's
entire history — every other card payment on it is one row at a distinct amount.

The engine did not miss the match by scoring it low. It **never scored the pair at all**.
`_classify` returned `None` at the F03 veto (`backend/services/matching.py`):

```python
if _distinct_same_source_event(incoming, candidate):
    return None
```

Invariant 1 (F03): two rows carrying distinct authoritative per-event refs under the SAME source
namespace are two real events and must never be collapsed. Both rows carry `source: "email"` with
different Message-IDs, so the veto fired. `add_transactions` then reported no duplicates in
complete good faith, and the second row was written silently.

F14d — "two two-leg TRANSFERS on the same route", where the route stands in for the description
similarity an ordinary near-duplicate demands — was added on 2026-08-09 for a reported bug of
*exactly this shape* (an email-ingested Beacon Bank card payment landing beside a Lucius-logged one). It
sits ~15 lines below the F03 veto and was shadowed by it for every email-ingested pair, which is
every pair it was written to catch.

F03's premise is that one namespace is one issuer's event feed, so two ids mean two events. That
holds for `ofx`, `simplefin`, `plaid`, `ibkr` — per-transaction ids minted by the institution
holding the account. It does not hold for a transfer between two accounts the book holds: that
movement has two counterparties and each notifies independently.

## Decision

We will **exempt two two-leg transfers on the same route from the F03 veto** — same `kind`, same
`from_account_id`, same `to_account_id` — so F14d can classify the pair as it was designed to.

The exemption is **shape-scoped, not namespace-scoped**. It keys off the transfer's structure, not
off `email`, so it covers the identical hazard on any feed (both banks' OFX exports carry distinct
FITIDs for one movement) and leaves every other F03 case standing.

Invariant 1 is enforced in **three** places, and all three will read one predicate,
`matching._same_source_event_veto`, never the bare `_distinct_same_source_event` fact:

| where | what it gates |
|---|---|
| `matching._classify` | whether the pair is scored at all |
| `ledger.merge_transactions` F23(a) | whether a merge is allowed without `override=True` |
| `match_review._judge` | the post-LLM re-check before an auto-merge |

They drifted during this very change: the engine exemption landed first, and `POST
/api/matches/merge` still refused the live pair with *"they are two real events"*. A veto that
disagrees with itself across call sites surfaces a duplicate the rest of the system won't act on,
so a test sweeps `backend/` for any direct read of the bare fact.

## Consequences

### Positive

- The live pair now classifies `NEAR_DUPLICATE` at 0.90 with reason "same transfer route, same
  amount, near date". At ingest `add_transactions` aborts the batch and requires an explicit
  `decisions` entry rather than writing silently.
- F14d does the job it was written for. The 2026-08-09 repro is genuinely closed rather than
  closed-except-when-both-rows-came-from-email — which was every real instance of it.
- No new tuning knob, no new class, no data migration. One condition.

### Negative

- Two genuinely distinct identical transfers on the same route within the F14d window are now
  surfaced as a probable duplicate. Under this book's `dedup_policy = "auto_high"` with
  `l3_enabled`, a strong near-duplicate that L3 agrees is "same" **auto-merges** — so this is a
  real, if narrow, exposure. It is bounded by the route + amount + date requirements, and F14d's
  own >2-day cap already routes the looser half of that window to review only. Accepted on the
  evidence that the route's four-month history contains no such pair.
- F03 is no longer uniform across kinds; a reader must now know that transfers are special. The
  test file and this ADR carry that.

### Follow-ups

- ~~Merge `txn_b211fd800000` into `txn_606692ee0000`~~ — done; the canonical carries both
  msgids, so L0 now catches either email on a re-run.
- `matching.py`'s module docstring carries the qualifier on invariant 1 (done in this
  change); `docs/INVARIANTS.md` has no invariant-1 entry to amend.

## Alternatives considered

- **Drop `email` from `AUTHORITATIVE_ID_NAMESPACES`** — verified to score the live pair 0.90, and
  the allowlist's own comment argues that a false negative there costs only noise. Rejected on the
  live config: with `dedup_policy = "auto_high"` and `l3_enabled = true`, a strong near-duplicate
  with L3 agreement auto-merges, so this would expose Scenario 19 (two identical SaaS-vendor charges, separate
  receipt emails, identical amount and day) to a **silent merge of two real charges** — the
  opposite failure, and the worse one. The allowlist comment's cost analysis predates the
  `auto_high` path.
- **Sub-namespace email by sender domain** so Beacon Bank-email and Meridian Bank-email are different issuers.
  Correct in principle and strictly more precise. Rejected as disproportionate: the sender is not
  on the row (only the msgid is), so it needs either a store read inside `_classify` — which is
  pure compute over dicts in an O(rows × pool) loop — or a backfill of every email-sourced row
  from `email_snapshots`. The shape test gets the same answer for this hazard with neither.
- **Cap the exempted pair's score below `SCORE_STRONG`** so it can only ever reach review.
  Rejected as second-guessing a settled decision: F14d already chose where that cap belongs
  (>2 days), and re-capping inside the exemption would make one class score two different ways
  depending on which veto it happened to route around.

## Agent provenance

- **Decided by:** orchestrating Claude agent + repo owner
- **Lane / branch:** `chore/remove-tax-features`
- **Alternatives weighed:** drop `email` from the authoritative allowlist; sub-namespace email by
  sender domain; cap the exempted score below STRONG
- **Driving artifacts:** a reported bug, 2026-08-26 (`txn_606692ee0000` / `txn_b211fd800000`);
  F14d, `backend/services/matching.py` (a reported bug, 2026-08-09); RFC transaction-dedup-engine
  invariant 1
- **Why this way (one line):** the hazard is a property of the transfer's shape — two
  counterparties, two notifications — so the exemption belongs on the shape, not on the namespace
  that happened to expose it.
