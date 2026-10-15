---
title: Refuse strong duplicates at the write choke
status: Accepted
date: 2026-08-01
deciders: Claude (senior-engineer lane) + owner
---

## Context

Every duplicate defence was **post-write**. `bulk_create_transactions` inserted, then
`match_review.review_created_rows` classified and enqueued. The one hard block —
`dedup_by_external_ref` — is opt-in, matches only an exact `(source, ref)` pair, and has exactly
one caller (`services/ingestion/orchestrator.py:152`). The statement-import path
(`statements/decisions.py`) does **not** pass it, so imports had no write-time block at all.

"Created-then-flagged" only protects a book whose review queue is READ. The owner's queue held
16 pending items, oldest 2026-06-15. Rows reach this choke from scheduled agents, connectors,
email ingestion, the AI tool surface and hand entry — most with no reader at all. The owner's
report: *"if we keep leaving them duplicates, it's not good! i dont' want users to keep having to
review and then remove and stuff."*

The engine could not have been configured around this. Every `MatchPolicy` field is
monotone-**tightening**: policy controls what the engine *proposes*, and no value refuses a write.
`services/ledger.py` had never imported `matching` at all.

## Decision

We will **refuse the write** at the ledger choke when the match engine identifies an existing row
as the same real-world event, and return the evidence needed to act on the refusal.

- Refused classes: `EXACT`, `NEAR_DUPLICATE`, `TRANSFER_LEG_ECHO` at `>= SCORE_STRONG` (0.80).
  `TRANSFER_PAIR` is **never** refused — it means "two legs of one movement", so both rows
  *should* exist and the correct outcome is to link them.
- `force=True` is the escape hatch. It already existed on both chokes as a **dead parameter**,
  and `add_transaction`'s own schema already promised *"set to true to skip duplicate
  detection"* — this makes an existing, advertised contract true rather than adding a knob.
- **Single-row raises** `DuplicateTransactionError` (409); **bulk reports** into `skipped` with
  `_duplicate_of` / `_duplicate_reason` / `_batch_index`. One duplicate in a 300-row statement
  must not cost the caller the other 299.
- The refusal carries the row it collided with (id + date + amount + description + account) so a
  caller can decide without a second round trip, per the `recovery_hint` house style
  (TOOL_CONVENTIONS S2 layer 3: *errors carry the recovery path*).

### Caps: evidence vs safety

`min(score, SCORE_STRONG - 0.05)` appears at five sites and does **not** mean one thing:

| site | cap means |
|---|---|
| F14b day-gap > 2 · fee drift 1–3% | the **evidence** is weaker |
| transfer-leg echo (unconditional) · categorized leg | **auto-merge is unsafe** |

Refusal bypasses only a *safety* cap, via `Candidate.raw_score` / `.evidence`, set solely on the
leg-echo tier. Bypassing evidence caps too was implemented first and was **wrong**: it refused
every review-band pair the queue exists to ask about (caught by
`test_bulk_add_tool_feeds_review_queue`). This matters because the motivating incident — the
owner's double-booked S$1,650.00 card payment — was a leg echo, pinned at 0.75 forever despite
raw evidence of 0.94. Scoring alone could never have blocked it.

## Consequences

**Positive.** Every write path inherits the block at once. A refusal is loud and actionable
rather than a queue item nobody opens.

**Negative.**
- A behavioural change for every caller: the ledger can now refuse a well-formed row on
  *semantic* grounds. ~60 tests encoded the old contract; most were arrangement (a duplicate
  created as *setup*), now routed through an explicit `_arrange_create(..., force=True)` shim.
- Two live statement merges failed before this was caught: `update_transaction` rebuilds a row
  via `create_transaction`, so the guard compared the rebuild against **its own original**.
  Excluded via the existing `_suppress_create_audit` ContextVar — a rebuild is not a booking.
- Cost: the engine runs per row pre-write. Naively this re-read the whole ledger per row
  (`known_transfer_routes` → `transactions.all()`), turning a 4-row import into a 15-second
  request. The batch path now hoists the candidate pool, routes and FX rates out of the loop.

**Follow-ups.**
- N:1 aggregation is still unhandled: one statement line vs several ledger rows never becomes a
  candidate pair, so it is invisible to both the guard and the diff. `ledger_diff` already
  computes `uncovered_net`; comparing it to an unmatched statement row would catch the common
  case. This is not hypothetical — it caused a real deletion of S$1,275.00 of real charges.
- The frontend does not yet render `refused_duplicates` from the commit outcome.

## Alternatives considered

- **Keep created-then-flagged, make the queue visible.** Rejected: it still requires a reader,
  and the headless paths have none. Worth doing anyway, but not instead.
- **Return the existing row (silent idempotency), as the L0 floor does.** Rejected: L0 is
  *proof* (an exact `(source, ref)` re-presentation); a 0.94 match is *inference*. Handing back
  someone else's id on a wrong guess loses a real transaction silently — worse than any error.
- **A per-call `policy` parameter to tune blocking.** Rejected: a per-call *loosening* vector,
  and it bypasses the `_EXTRACTABLE_FIELDS` allowlist and provenance that make policy auditable.
- **Block on the capped score.** Rejected: every cap flattens to exactly 0.75, so a 0.94-certain
  leg echo is indistinguishable from a marginal pair — the exact information loss
  `_amount_fidelity` was already bolted on to work around.

## Agent provenance

- **Decided by:** Claude (senior-engineer lane), driven directly by the owner during
  testing.
- **Driving artifacts:** owner incident (double-booked S$1,650.00 card payment, caught by eye
  twice); `tests/dedup/test_check_duplicates.py`'s own docstring recording that every dedup
  surface was post-hoc; RFC `2026-07-29-one-match-engine-instructions-as-policy`.
- **Why this way:** the owner's constraint was that duplicates must not reach the book at all,
  and that a refusal must tell the caller what it hit and how to proceed — *"return that
  duplicated one in the response error so agent know if they need to force"*. Refusal is
  non-destructive and reversible (`force`), which is what makes it safe in exactly the cases
  where auto-merge is not.
