---
title: check_duplicates — a pre-write duplicate check, built on match_batch not check()
status: Accepted
date: 2026-07-31
deciders: dedup-preflight lane (Claude) + repo owner (incident report, 2026-07)
---

# `check_duplicates` — the pre-write half of the dedup surface

## Context

The owner's incident: the same S$1,650.00 card payment was written **twice** by the email
agent — once as a two-leg transfer from the bank alert, once as a single-leg credit from the
Giro alert two days later. It was caught by eye, twice.

Every dedup surface was **post-hoc**:

- `match_review.review_created_rows` runs *after* the rows exist. Created-then-flagged is a
  deliberate design (a flagged row is never silently dropped) and stays — but it means an
  agent's only protection is a hook that runs after it has already written, and when that
  hook returns nothing the agent gets no signal at all.
- `find_duplicates` (`match_review.scan_duplicates`) scans the **ledger by date range**. It
  cannot answer *"here are rows I am about to write; are they already there?"*.
- `add_transactions` does run a duplicate pre-flight — but it only **aborts on the `strong`
  band**. The incident's own verdict was `TRANSFER_LEG_ECHO` at **0.75**, which is the
  `review` band: booked silently, flagged afterwards.

Owner's ask: *"are we surfacing a tool/api where it can just pass a list of potential
transactions and return duplication candidates?"* Answer at the time: no.

## Decision

A read-only tool, **`check_duplicates`**, over a new service `services/match_preflight.py`.

### 1. Build the verdict from `matching.match_batch`, not per-row `matching.check()`

The brief suggested `matching.check(row, candidates=[…], profile=SCAN)`, which does return
the right verdict for one row. We used `match_batch` instead. Three reasons, in order of
weight:

1. **It is what the WRITE computes.** `add_transactions`' own pre-flight and
   `review_created_rows` both call `match_batch`. Building the check on anything else would
   mean a caller could get one answer from the check and a different one from the write —
   the single failure mode that would make the tool worse than nothing.
2. **Rows are classified against EACH OTHER**, not only the ledger. A list that describes
   one movement twice (a debit leg and a credit leg proposed together) is caught;
   `duplicate_of_index` names the sibling. Per-row `check()` against a ledger pool is
   structurally blind to this.
3. **Global 1:1 exclusivity.** Two proposed rows never both claim the same existing
   transaction, so the returned set is actionable exactly as given.

This is the *opposite* call from `scan_duplicates`, which deliberately avoids `match_batch`
(ADR `2026-07-19-dedup-merge-preservation-and-scan-exclusivity`) — and the difference is the
point. `_classify_batch_pairs` stamps one synthetic `import_batch_id` across its `rows` so
the same-feed invariant applies to siblings. For `scan_duplicates` that is wrong: its rows
are unrelated ledger rows spread over months, and the stamp would blanket-veto every
cross-source pair. For rows handed to `check_duplicates` it is *correct*: they genuinely
arrived together — one statement, one agent turn, one bulk-add — which is exactly the claim
the stamp encodes, and exactly what `add_transactions` does with the same rows.

### 2. The candidate pool is reused, not copied

`match_batch` is called **without** `candidates=`, so the pool comes from
`matching._batch_ledger_pool` — the shared builder. Nothing is extracted and nothing is
duplicated, which means a fix to that pool (a sibling lane is landing one: it currently omits
two-leg transfers on the agent write path, so `TRANSFER_LEG_ECHO` cannot fire there) reaches
this surface for free rather than needing a second fix.

### 3. L0 IS run here, unlike in `match_batch`

`match_batch` skips the exact-external-ref floor because for its callers L0 already ran at
the create choke. For a row that has not been written, it has not run — and its verdict is
the most decisive one available: `create_transaction` **returns the existing row** rather
than creating a second one, so "booking this changes nothing" is a fact, not a probability.
An L0 hit is reported as `klass=exact`, band `strong`, action `skip`.

### 4. The inherited cost, stated rather than patched around

Two **same-direction identical rows in one call** are NOT flagged against each other — the
same-feed invariant ("the bank already de-duplicated its own statement") vetoes them, exactly
as it does inside `add_transactions`' pre-flight. We inherit that on purpose. A check that
flagged what the write then books happily would teach the caller to ignore it, and the
incident this exists for is cross-*call* anyway: two alerts, two days apart, two agent turns.
Pinned by `test_two_identical_same_direction_rows_are_NOT_flagged_against_each_other`.

### 5. Vocabulary: the engine's, plus three actions in the write's own words

Per candidate: `klass`, `score`, `become_transfer`, `reasons`, `signature`, and the matched
transaction — the same fields `find_duplicates` returns, so there is one dialect. Per row:
the engine's own `band` (`matching._band`, the same `SCORE_STRONG`/`SCORE_FENCE` boundary
`list_match_reviews` reads — never a second threshold) plus `recommended_action`:

| action | when | why that word |
|---|---|---|
| `skip` | L0 hit, or band `strong` | one of `add_transactions`' three `decisions` actions |
| `confirm` | band `review` | ambiguous money is a human's decision; no write-side equivalent, on purpose |
| `create` | no evidence | the third `decisions` action |

### 6. Bounded, and read-only by construction

Input is capped at `MAX_PROPOSED_ROWS = 500` — *equal to* `ai/tools/ledger._MAX_BULK_ADD_ROWS`,
pinned by a test, because a check that refuses rows the write accepts is useless at the only
boundary that matters. Over the cap is a clear error naming it, having checked nothing —
never a silent truncation. Candidates are capped per row (default 3, `max_candidates` up to
10) with the true pre-cap `candidates_total` always reported. There is **no caller-chosen
field/projection parameter**: this is a money-bearing read and a model that could select
`amount` over `base_amount` would defeat the historical-rate lock.

Classified `READ_ONLY_TOOLS`. `test_a_flagged_check_writes_nothing` byte-compares
`transactions.json`, `match_reviews.json` and `match_decisions.json` across a call that
returns a flagged verdict.

## Consequences

- Per-row normalisation errors are reported **per row** rather than aborting the call — the
  opposite of `add_transactions`, where batch atomicity of the *write* is the point. This
  call writes nothing, so there is no partial-batch hazard, and one bad date should not cost
  a caller nineteen other answers.
- `add_transactions`' description gains one clause pointing at this tool. That is the only
  CORE-offer byte this lane spends; `check_duplicates` itself is NOT in `CORE_TOOL_NAMES`.
- The full-registry schema budget was raised 380,000 → 388,000 (tenth raise; the reasoning,
  the measurement and what was reclaimed first are recorded inline in
  `tests/test_tool_schema_budget.py`).
- **Deliberately out of scope:** making `add_transactions` *surface* review-band pre-flight
  hits instead of only aborting on `strong`. That is the change that would have caught this
  incident with no extra tool call at all, and it is a behavior change to a write choke
  another lane owns.

## Alternatives rejected

- **Per-row `matching.check()` with a hand-built pool.** Rejected: loses intra-batch matching
  and 1:1 exclusivity, and — decisively — would answer differently from the write.
- **A `dry_run` flag on `add_transactions`.** Rejected for the reason `resolve_transaction_row`
  exists as its own function rather than a flag on the writer (ADR
  `2026-07-18-previews-must-not-write`): a preview that shares a code path with a writer can
  commit by taking a wrong branch. A separate read-only module cannot.
- **Adding it to `match_review.py`.** Rejected: that module is already 2.4× the file-size
  guideline, a sibling lane is editing its `review_created_rows`, and keeping a
  "writes nothing, ever" module physically apart from the one holding the merge primitive,
  the review queue and the verdict cache is itself part of the guarantee.
