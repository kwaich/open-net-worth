---
title: Dedup engine hardening — merge field preservation, scan_duplicates 1:1 exclusivity, grounded confidence threshold
status: Accepted
date: 2026-07-19
deciders: reliability-program agent (Claude) + repo owner (harness reliability sweep, 2026-07-19)
---

# Dedup engine hardening — merge preservation, scan exclusivity, grounded threshold

## Context

A harness audit of the AI tool surface (`.claude/rfcs/harness-audit-2026-07-18/HANDOFF.md`,
`docs/launch/BOARD.md` tools section) flagged three related problems in the dedup/match
engine (`services/matching.py`, `services/match_review.py`, `ai/tools/matching.py`):

1. **`merge_transactions` silently dropped data.** The merge choke
   (`ledger_service.merge_transactions`, out of scope for this lane) only ever carried
   `external_ids`/`merged_from` forward, plus a narrow F16(b)/F16(c) inheritance
   (cleared/reconciled_at/settled-amount). Everything else distinct on the absorbed row —
   memo, tags, an unlinked category or payee, splits — was discarded with no trace. Losing a
   `memo` is a real loss even though nothing downstream reads it (`description`, not `memo`,
   drives dedup/rules/payees — ADR `2026-07-17-split-note-into-description-and-memo`).

2. **`find_duplicates` had no 1:1 exclusivity.** `scan_duplicates` called `matching.check()`
   once per row, independently — no coordination across rows. The same transaction could
   come back as the CANONICAL of one returned pair and the INCOMING of a different pair,
   so acting on the set could double-count a row's amount, or attempt a second merge that
   fails once the first commits (the write choke's own already-merged/deleted guard fails
   this safely, but the returned set could no longer all be actioned as given).

3. **`resolve_match_review`'s accept/dismiss confidence cut was an invented `0.9`,** not
   grounded in anything the engine itself measures, and **`swap_review_canonical`** had a
   service function (F20) and a REST route but no AI-tool surface at all — a capability gap
   the CLAUDE.md "AI-native design test" flags directly (new capabilities ship AI-reachable
   from day one).

`backend/services/ledger.py` (home of `merge_transactions`/`update_transaction`) and
`backend/ai/tools/ledger.py` were off-limits for this lane (another lane owns them), so
every fix here had to work by composing what those files already export, never editing them.

## Decision

### 1. Merge field preservation — a wrapper, not a choke edit

`services/match_review.merge_transactions_preserving_fields(canonical_id, absorbed_id, *,
reason, become_transfer)` wraps `ledger_service.merge_transactions` with a **pre-merge**
pass, split into two policies by how safe an automatic decision is:

- **ADOPT-IF-BLANK** (extends the merge choke's own cleared/reconciled_at idiom): `memo`,
  `tags` (additive union), and — for a non-transfer merge only, since `become_transfer`
  wipes both regardless — `category`/`payee_id`. Applied via a direct
  `transactions.update_by_id` patch **before** calling `merge_transactions` (never via
  `update_transaction`, which would re-run the rules engine and the corrections recorder for
  a change the user never made). Landing the patch first — not after — means the merge
  journal's `canonical_before`/`post_canonical_snapshot` already reflect the adopted value,
  so F18 undo-safety survives byte-for-byte (verified:
  `test_merge_preservation_pre_merge_patch_stays_undo_safe`). The merge's own audit
  before/after diff can't show this change (it reads canonical AFTER the patch), so the
  wrapper records its own `"update"` audit event for it.
- **SURFACE, NEVER AUTO-PICK**: when canonical and absorbed carry a DIFFERENT non-null
  memo/category/payee_id, or the absorbed row carries splits canonical will end up without,
  it's a genuine judgment call — reported as `field_conflicts` in the tool/REST response
  instead of silently resolved either direction. Splits are always surfaced, never adopted
  (no safe way to validate them against the canonical's own leg amount without re-running
  full write-choke validation).

Every merge entry point now routes through this wrapper — `ai/tools/matching.py`'s
`merge_transactions` and `resolve_match_review` tools, `services/match_review.accept_review`,
`api/matches.py`'s `/merge` and `/reviews/{id}/accept` routes, and
`review_created_rows`'s (currently unreachable) auto-merge branch — so no path can silently
reproduce the loss this closes. `accept_review`'s return type changed from `dict` to
`tuple[dict, dict]` (merged row + preservation info); every REST/test call site was updated.

### 2. `scan_duplicates` gains global 1:1 exclusivity — without reusing `match_batch` itself

The obvious fix — rebuild `scan_duplicates` on `matching.match_batch`, the same primitive
`review_created_rows` already uses for exactly this exclusivity property — was tried first
**and rejected**: `match_batch`'s `_classify_batch_pairs` stamps every row in its `rows`
argument with ONE shared synthetic `import_batch_id` so the same-feed invariant (F23a)
applies across siblings. That's correct for `match_batch`'s actual callers (an incoming
batch that genuinely all just arrived together — one statement, one bulk-add) but WRONG for
a housekeeping scan: `scan_duplicates`'s rows can come from completely unrelated imports
spread over months, and stamping the whole scanned set as "one feed" blanket-vetoes every
legitimate cross-source duplicate pair. Verified by running it: the exact 2×2 scenario
`review_created_rows` resolves correctly returned **zero** pairs under this approach.

**Chosen fix**: classification stays per-row via `matching.check()` (unchanged — each row
keeps its own REAL `import_batch_id`, so the same-feed veto still fires correctly per pair,
never blanket). The 1:1 EXCLUSIVITY is layered on top by reusing
`matching._global_claim_assignment` directly — the shared primitive `match_batch` and
`import_anomalies._enforce_exclusive_matches` are themselves "thin adapters" over (per its
own docstring, ADR #30) — building claims from each row's own ranked `check()` candidates
(primary + same-class alternatives at/above `SCORE_FENCE`, target keyed via
`matching._claim_target({}, cand)` with an empty sibling map since every candidate here is a
real persisted ledger row, never a batch draft). A losing claim falls through to its
next-ranked free candidate, exactly like `_assign_batch_matches`. `scan_duplicates` also
gained the same `MAX_BATCH_ROWS` cost backstop `match_batch` enforces, surfaced as a
`ValidationError` naming the cap (bounded-reads philosophy — narrow date_from/date_to and
re-scan) rather than an unbounded O(rows × pool) hang.

### 3. Grounded confidence threshold + the missing swap tool

`_recommended_action`'s literal `score >= 0.9` is replaced by `SCORE_STRONG` (0.80,
`services/matching.py`) — the SAME boundary the engine's own `_band`/`match_batch` already
use to call a match "strong" (auto-merge-eligible with L3 agreement) vs. merely "review"
(every item reaching this queue already cleared `SCORE_FENCE`, the review floor, by
construction). `review_created_rows` now stamps the engine's own precomputed `band` onto
each enqueued review item (`RowMatch.band` — single source of truth); `_recommended_action`
prefers it when present and falls back to the `SCORE_STRONG` comparison for older review
items that predate the field. `swap_review_canonical` — a genuine service function + REST
route with no AI-tool wrapper — gets one, mirroring the REST route's validation exactly.

## Consequences

- `merge_transactions`/`resolve_match_review` responses gained `preserved_fields`/
  `field_conflicts` keys (additive — no existing consumer reads a fixed key set here).
- `accept_review`'s return type is now `tuple[dict, dict]`, not `dict` — a breaking change
  for any direct caller; both existing call sites (`api/matches.py`, `ai/tools/matching.py`)
  and every test asserting on its return value were updated in the same change.
- `scan_duplicates` can now raise `ValidationError` on an oversized (date-unbounded) scan;
  `find_duplicates` catches it and surfaces `{"error": ...}` rather than propagating.
- Not addressed here (out of scope — `services/ledger.py` is another lane's file): the
  ADOPT-IF-BLANK policy is a pre-merge patch composed around the choke, not inside it. If a
  future change moves `merge_transactions` itself, this wrapper's "patch before, not after"
  ordering is the one invariant that must be preserved for F18 undo-safety.
- `tests/golden/tool_input_schemas.json` gained one deliberate entry (`swap_review_canonical`)
  in the same commit, per that file's own hand-review contract.

## Agent provenance

Investigated by reading the actual merge/scan/threshold code before changing it (per the
task brief's explicit warning that some audit claims turn out false on inspection — three had,
earlier in this same program). All three claims here reproduced. `get_options` shipping
closed contracts (a fourth claim in the same audit) did NOT reproduce — already fixed on this
branch (commit `00f8dbd2`, regression tests `test_closed_contract_is_excluded_from_holdings`/
`test_closed_contract_not_counted_as_uncovered` already green) — no action taken, reported as
verified-fixed rather than re-done.
