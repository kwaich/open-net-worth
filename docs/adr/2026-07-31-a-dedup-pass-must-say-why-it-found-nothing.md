# 2026-07-31 — A best-effort dedup pass must distinguish "found nothing" from "already flagged"

**Status:** accepted (owner incident — duplicate card payment reached the ledger twice)
**Where:** `backend/services/match_review.py` (`review_created_rows`),
`backend/ai/tools/ledger.py` (both `add_transactions` call sites),
`backend/api/ledger.py` (`POST /api/transactions`)
**Tests:** `tests/dedup/test_transfer_leg_echo_post_commit.py` (3) ·
`tests/dedup/test_dedup_surfacing_never_silent.py` (3) ·
`tests/dedup/test_char_statement_match_surface.py` (empty-batch pin updated, additive keys only)

## Decision

`review_created_rows` returns `already_pending` (a count) and `pending_reviews` (the live review
item ids) alongside `queued`/`reviews`. Every swallow at a call site into it now carries a
`logger.exception`. The candidate pool, the classifier and the enqueue rule are **unchanged** —
no scoring, threshold, class or selection logic moved.

## Why

An owner-reported duplicate: a two-leg `kind=transfer` card payment booked from the paying bank's
alert, then the same payment seen two days later from the receiving card's own Giro alert and
written as a fresh single-leg `income` row. It inflated the card balance, and the owner caught it
by eye — twice.

The working hypothesis was that the post-commit pool excludes two-leg `transfer` rows, which
would make `TRANSFER_LEG_ECHO` structurally unreachable on that path. **It is not true.** The
evidence:

- The pool is kind-agnostic (`matching._batch_ledger_pool` filters only `deleted`,
  `merged_into` and the caller's own `exclude_ids`), the transfer was in it, and replaying the
  incident row against a copy of the real book produced `queued: 1`, klass `transfer_leg_echo`,
  canonical = the transfer.
- The live `match_reviews.json` **already held that exact review item**, created at the same
  second as the duplicate row, still `pending`. The net fired at the moment of the write.

What made the net look dead was the diagnostic: re-running `review_created_rows` over the row
returned `{queued: 0}` — because the F11 pair dedup correctly declines to enqueue a *second*
review for a pair a live item already covers. `queued: 0` therefore meant three different things
(nothing matched · already flagged · a remembered "different" verdict), and the two that matter
most were the ones it hid. An investigation that starts from "the engine returned zero" reaches
for the engine, and the engine was never the problem.

The same shape had already bitten this chain once at the AI-add call site, where the swallow was
a bare `except: pass` with no logger: a raising engine and a clean ledger produced byte-identical
tool output. Best-effort is right — the row is committed and a dedup pass must never unwrite it —
but best-effort must still be *legible*.

**The rule extracted: a best-effort pass over money must never report its silence and its
suppression with the same value.** Zero findings and zero *new* findings are different facts.

## Blast radius (measured, not argued)

Replaying every live row of the owner's book (1522 rows, 480 import-batch groups) through
`review_created_rows` into a fresh queue: **113 review items before this change, 113 after** —
identical, because candidate selection is untouched. The 30 `already_pending` hits in that replay
are skips that were already happening, now reported instead of silently folded into `queued: 0`.

## What we did NOT do

**We did not widen the pool.** The hypothesis was refuted before any code moved, and forcing it
would have added review items to every ingest surface to fix a bug that was not there.

**We did not fix the surfacing gap this incident actually exposes.** The queue is pull-only: it
renders in one panel inside the Inbox tab, behind a nav badge that merges the dedup count into the
inbox unread count, unlabelled and clamped at "9+". No ingest surface notifies — the REST manual
add, REST bulk add, CSV/OFX/QIF import, statement commit and connector sync all discard the
returned count. `MatchReviewStore.pending()` sorts newest-first, so an untriaged item sinks. The
owner's queue holds 16 pending items, the oldest from 2026-06-15. That is the reason a correctly
flagged duplicate still got caught by eye, and it is a UI change under the UI Quality Gate
(design spec → acceptance file → `make qa-e2e` → `make ui-gate` → screenshots), not something to
bolt onto a backend fix. Called out here so the next agent does not re-derive it.

## Agent provenance

The task named a suspected filter and asked for a failing-first test proving it. The test passed
on the first run — the strongest available evidence that the accused layer was innocent — and the
real answer came from reading the owner's live `match_reviews.json`, where the "missing" review
was sitting, pending, with the duplicate row's own creation timestamp. Kept as a regression pin
anyway: it now guards the pool's kind-agnosticism, so a future narrowing fails there instead of
costing another duplicate.
