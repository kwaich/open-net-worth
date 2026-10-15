---
title: Per-subject analysis coalescing — collapse per-trigger queue rows, watermark evidence reads, dedup at read
status: Accepted
date: 2026-07-18
deciders: orchestrating agent (Claude) + owner-diagnosed backlog (INT14b, owner AFK dispatch)
---

# Per-subject analysis coalescing — collapse per-trigger queue rows, watermark evidence reads, dedup at read

## Context

The Intel Analyst pipeline's pending-analysis queue (`pending_analysis.json`,
`PendingAnalysisStore`) is enqueued **per TRIGGER**, not per SUBJECT. Live data showed a single
hot story (the Uber/Kestrel Dash acquisition saga) produced 230 `news_spike` rows + 107
`material_news` rows — ~70 enqueues/day at the peak — each one an independent pending-analysis
row that, once drained, re-reads the SAME cached evidence (`services.evidence.read_evidence`'s
blind freshest-8 slice) and runs its own LLM synthesis producing a near-identical write-up.

Three compounding root causes:

1. **Per-trigger rows for the same subject.** `crash_watch`/`price_monitor` (crash), `rss_poller`/
   `rss_poller_topics` (`news_spike`), and `news_screen._enqueue_material` (`material_news`) each
   called `PendingAnalysisStore.add()` unconditionally — every qualifying trigger, regardless of
   whether the subject already had an unprocessed row, got its own row.
2. **A blind recency slice with no watermark.** `analyst._process_row` always read
   `read_evidence(subjects=[subject], fresh_only=True, limit=8)` — the freshest 8 cached headlines,
   with no memory of what a PRIOR analysis of this subject already covered. A syndication flood
   (8 near-duplicate wire copies of one story) could crowd out a genuinely new, older-arriving
   development, and every re-analysis of the same subject re-read largely the same 8 items.
3. **An operating-rate cap that no longer matched cost.** `_DAILY_ANALYSIS_CAP=50` and
   `_PER_SWEEP_CAP=5` were tuned against a NORMAL enqueue rate, not the redundant, per-trigger
   volume above. Drained at exactly the daily cap, the queue never caught up: a measured 13-hour
   median drain lag left 38 rows perpetually pending, so the analyst spent its whole daily budget
   re-chewing stale news oldest-first instead of reaching what just happened. Real per-analysis
   cost is ~$0.0012 (one bounded LLM call) — the cap was protecting a few dollars a month at the
   cost of a permanently stale Intel feed.

INT14a (merged just before this lane) already fixed a related but distinct problem — SENDING a
push for a `no_change`/stale verdict — via a materiality gate and a staleness guard on the
EXTERNAL push. It explicitly left "the drain-throughput backlog itself" out of scope, tracked as
a follow-up. This ADR is that follow-up.

## Decision

Four coupled changes, landed together because they interlock (coalescing changes what the queue
looks like; the watermark and cluster-dedup changes only make sense once the queue is coalesced;
the cap only becomes safe to loosen once the queue is small).

### 1. Per-subject coalescing at enqueue (`PendingAnalysisStore.upsert_pending`)

Every enqueue call site now routes through a new `upsert_pending(subject, ticker, signal_id,
trigger, payload)` method instead of the old unconditional `add()`. It finds the FIRST row with a
matching `subject` AND `status=="pending"` and **merges** into it instead of appending a new row.
A `claimed`/`done` row is never a merge target — the next trigger for that subject opens a fresh
row, because an in-flight or completed analysis is a settled fact the next trigger should analyze
against, not silently absorb into.

Merge rules:

- `created_at` stays the **earliest** arrival. INT14a's staleness guard
  (`analyst._row_age_hours` vs. `_STALE_PUSH_HOURS`) measures how long the subject has been
  WAITING for an analysis — updating it on every merge would make a subject that's been
  re-triggered for hours look perpetually fresh, defeating that guard.
- `triggers` accumulates every distinct trigger kind that has contributed (informational).
- `trigger`/payload go **"material_news"-sticky**: once any merge carries a `material_news`
  trigger — the only kind with an actual headline, never a bare price/volume ping — the row's
  `trigger` becomes (and stays) `"material_news"`. This was a deliberate design choice to avoid
  touching `analyst._event_framing`/`_update_signal`/`analyst_prompt.render_evidence_block`,
  which all branch on the literal string `row["trigger"] == "material_news"` to decide whether to
  render the `>>> NEW` trigger marker. Making the sticky trigger the SAME literal value those
  call sites already check means **zero changes** were needed to any of that INT12 marker logic.
  A later `material_news` merge overwrites the marker fields (`headline`/`published_at`/
  `first_seen`/`cluster_tokens`/`reason`) with its own — "the row leads with the MOST RECENT real
  development" — while a non-material merge (crash/news_spike) can't clobber them, since its own
  payload simply carries no such keys (a plain `{**old, **new}` shallow merge leaves them
  untouched).
- Each `material_news` merge additionally appends `{at, cluster_tokens}` onto
  `payload["material_events"]` — see the INT12 interaction below.

### 2. Per-subject watermark, not a blind freshest-N (`services.evidence.read_evidence(since=...)`)

A new `since` parameter on `read_evidence` filters to rows whose `first_seen` (cache-arrival time
— chosen over `published_at` since that's an untrusted, sometimes-relative string, and because
"has our cache seen this yet" is the actually-relevant question) is strictly newer than `since`.
`analyst._process_row` now fetches the row's linked Intel signal BEFORE reading evidence (moved up
from after, where INT12 had put it for prompt delta-context) and passes the signal's
**`evidence_watermark`** — a field DEDICATED to this purpose, distinct from `updated_at` (see the
Security hardening section below for why they can't be the same field) — as the watermark. No
prior signal (bootstrap — first-ever analysis of this subject) → `since=None`, `limit=8`, i.e. the
exact pre-existing behavior. A watermark present → `limit=_WATERMARKED_READ_LIMIT` (200 — a
BOUNDED, not unbounded, since-read; see Security hardening).

### 3. Cluster-dedup at read (`analyst_prompt.collapse_to_distinct_stories`)

Before the prompt is built, the (bounded but still potentially large) since-watermark evidence read
is collapsed into at most `_MAX_DISTINCT_STORIES=8` DISTINCT story clusters, keeping the newest
representative of each. Clustering reuses `news_screen`'s existing normalized-token-overlap
algorithm — pulled out into `analyst_prompt.cluster_by_content_overlap` so there's one
implementation, not two drifting copies (`news_screen._cluster_rows` now delegates to it at its own
`_CLUSTER_OVERLAP` threshold). INT12's trigger-marker/newest-first/>24h-background-cap logic
(`analyst_prompt.render_evidence_block`) is unchanged and runs on TOP of this smaller, deduped set.
The read AND the collapse now run TOGETHER inside the same `asyncio.to_thread` offload
(`analyst._read_and_collapse_evidence`) — see Security hardening.

### 4. Caps become a safety net, not an operating limit

`_PER_SWEEP_CAP` 5→10, `_DAILY_ANALYSIS_CAP` 50→500. Documented in the constants' own comments and
the module docstring as a runaway backstop (never expected to be hit in normal operation), not a
rate control — pushes are already materiality-gated (INT14a), and coalescing keeps the queue small
regardless of trigger volume.

## The INT12 interaction (why `recent_material_events` exists)

`news_screen.py`'s significance screen has its own anti-spam mechanism (INT12): a subject-level
floor (`_SUBJECT_SCREEN_FLOOR=3`) capping how many DISTINCT clusters may screen per subject per 6h
window, plus a cluster-fingerprint check (`_cluster_already_screened`) that suppresses a
re-syndicated copy of an already-screened story. Both read
`PendingAnalysisStore.recent_rows(subject, "material_news", since_iso)` pre-INT14b — implicitly "1
ROW = 1 screened event, timestamped by the row's `created_at`."

Coalescing breaks that implicit equivalence: 3 distinct `material_news` triggers for a subject
whose row is STILL PENDING now coalesce onto ONE row, not three. A naive port that kept counting
ROWS would see 1 event, not 3 — silently **undercounting** the floor and letting a 4th, 5th, ...
distinct cluster past a limit that's supposed to cap at 3. This is exactly the failure mode the
task brief warned about ("a coalesced pending row must not double-count toward the subject floor
incorrectly").

The fix: `PendingAnalysisStore.recent_material_events(subject, since_iso)` replaces `recent_rows`
and flattens `payload["material_events"]` **across every row**, not counting rows — so 3 distinct
merged events on ONE coalesced row still correctly present as 3 events to the floor/fingerprint
check. A row that predates this field (a legacy row, or one seeded directly via the raw `add()` —
every pre-INT14b test fixture) falls back to exactly one synthesized event, timestamped by the
row's own `created_at` and fingerprinted by `payload.cluster_tokens` if present — the EXACT
pre-coalescing behavior, so an un-migrated `pending_analysis.json` (no data migration ships with
this change) keeps working unchanged. `_merge_pending_row` mirrors this same fallback on the WRITE
side too: merging a second `material_news` trigger into a legacy row (no `material_events` key
yet) first synthesizes the legacy row's own implicit event before appending the new one, so a
later floor check doesn't undercount by exactly one due to the migration boundary itself.

One accepted, documented residual: the floor/fingerprint window is keyed off each EVENT's own `at`
timestamp (stamped at merge time), which is independent of the row's overall `created_at` — so
this generalization is strictly MORE correct than a row-count approach, not merely
equivalent-by-luck.

## Security hardening (post-QA gate, same lane, 2026-07-18)

The QA/security/code-review gate on the first cut of this lane surfaced four issues, all fixed in
the same commit:

**1. Unbounded watermarked read + synchronous O(n²) clustering on the event loop (MEDIUM).** The
first cut passed `limit=None` whenever a watermark was present, reasoning that
`collapse_to_distinct_stories`'s cap would bound the OUTPUT regardless of how large the input got.
That's true for output size, but the CLUSTERING ITSELF (`cluster_by_content_overlap`) is O(n²) over
the READ's size, and only the read (not the clustering) was wrapped in `asyncio.to_thread` — a
feed-flood on a subject with an old/backed-up watermark could read thousands of rows and then
stall the event loop for a noticeable moment while clustering them synchronously, up to
`_PER_SWEEP_CAP` times per sweep tick. Fixed two ways together: (a) `_WATERMARKED_READ_LIMIT=200`
caps the since-read itself — still far above the bootstrap freshest-8, so the "don't let dupes
crowd out real developments" goal is intact, but the worst case is bounded; (b) `analyst.
_read_and_collapse_evidence` bundles the read AND the collapse into ONE function, and `_process_row`
offloads that single function via `asyncio.to_thread` — clustering never touches the loop.

**2. Evidence-arrival race — a data-loss bug (code-review should-fix).** The first cut used the
signal's `updated_at` (stamped AFTER the LLM call completes) as the next read's watermark.
Evidence whose `first_seen` landed WHILE that LLM call was in flight — after the read that fed it,
before its completion — was invisible to THAT analysis (it arrived too late) and would then be
EXCLUDED FOREVER from every subsequent analysis too, because the watermark had already advanced
past its arrival time. Fixed by decoupling the read cutoff from the completion stamp: `analyst.
_process_row` now captures `read_time = datetime.now(timezone.utc).isoformat()` immediately BEFORE
the (offloaded) evidence read, and a NEW dedicated signal field, `evidence_watermark`, is stamped
with THAT pre-read time (not `updated_at`, which keeps its original meaning — "when this signal's
synthesis last completed," read/displayed elsewhere) by `_update_signal`/`_create_news_spike_signal`
/`topic_analyst.create_topic_spike_signal`. Evidence arriving during the LLM call now has
`first_seen > evidence_watermark` and correctly rides the NEXT analysis instead of vanishing.
Regression-tested end to end (`test_evidence_arrival_during_analysis_survives_to_next_sweep`) using
the REAL `EvidenceStore`/`read_evidence` across two sweeps, not a fixed-list stub.

**3. Unbounded `material_events` growth (LOW, defensive).** `payload["material_events"]` — the
INT12-interaction fix above — is now capped at `_MAX_MATERIAL_EVENTS=20` (most-recent-first) in
`_merge_pending_row`. The INT12 subject floor (`_SUBJECT_SCREEN_FLOOR=3`) already bounds this in
NORMAL operation, but a row that's never drained (a stuck sweep, a disabled analyst) shouldn't
accumulate this list unboundedly regardless.

**4. Future-dated `published_at` gaming cluster selection/ordering (LOW).** A feed-controlled
future `published_at` could pin an attacker's copy as a cluster's kept representative, and sort it
ahead of genuinely fresher legitimate items, since `recency_hours` clamps a future date to `0.0`
("just happened") rather than rejecting it. Fixed in `analyst_prompt._clamped_recency_hours`: for
ordering/representative-selection purposes ONLY, recency is `max(recency(published_at),
recency(first_seen))` — i.e. `published_at` is effectively clamped to `min(published_at,
first_seen)` as a calendar date — so an article can never be treated as fresher than the moment we
OURSELVES first saw it (`first_seen` is our own stamp; untrusted content can't set it). Falls back
to `published_at` alone when `first_seen` is absent. **Accepted residual, NOT fixed in this pass**:
the pre-existing `render_evidence_block`/`order_and_cap_background`/`age_label` path (INT12, not
introduced by this ADR) still trusts raw `published_at` for its own newest-first sort, >24h-cap,
and the age text shown to the model — extending the same clamp there would require also changing
what age is DISPLAYED (to avoid a "sorted as old but labeled as fresh" inconsistency), which is a
larger, pre-existing-behavior change out of scope for this security round. The `_compute_expires`
TTL-extension angle (a future `published_at` also extends how long a row survives before GC) is
similarly a pre-existing property of `services.evidence`, not introduced here, and is left
undocumented-but-acknowledged rather than fixed.

## Consequences

- The queue shrinks dramatically for a hot subject: one still-open analysis absorbs every
  subsequent trigger instead of spawning a parallel one, cutting both queue depth and redundant
  LLM calls for the same underlying story.
- A subject's PENDING row, once it exists, keeps getting cheaper to update (a fast subject/status
  scan + in-place merge) rather than the store growing unboundedly for the duration of a news
  cycle.
- `PendingAnalysisStore.add()` is kept as a low-level primitive (every test fixture uses it to seed
  a raw row) but is no longer called by any production enqueue path — `upsert_pending` is the one
  choke point now.
- **Existing `news_screen` tests that asserted per-trigger row counts were updated**
  (`tests/test_news_screen.py`): `test_distinct_cluster_screens_within_cooldown_window`,
  `test_subject_floor_suppresses_fourth_distinct_cluster`,
  `test_stopword_and_subject_name_exclusion_prevents_false_suppression`, and
  `test_legacy_row_without_cluster_tokens_does_not_crash_or_suppress` all previously asserted
  `len(pa_store.pending()) == N` for N screened triggers on the same subject — this is precisely
  the behavior INT14b changes, so these assertions now check `len(pa_store.pending()) == 1` (or
  the flattened `material_events` count) while their ORIGINAL intent (the LLM call count / floor
  enforcement) is preserved and still asserted.
- `test_material_news_background_newest_first_and_capped` (`tests/test_analyst_sweep.py`) had its
  synthetic evidence titles ("Old item A/B/C/D") changed to genuinely distinct content — the bare
  "Old item X" labels shared enough tokens (`{old, item}`) to legitimately cluster together under
  `collapse_to_distinct_stories`, which wasn't what that test was exercising (it tests
  `order_and_cap_background`'s ordering/cap logic, layered ON TOP of dedup).
- A signal now carries an `evidence_watermark` field distinct from `updated_at` (Security hardening
  #2 above) — `_seed_signal` (`tests/test_analyst_sweep.py`) stamps one, and
  `test_watermark_used_when_prior_signal_exists` asserts against `evidence_watermark`, not
  `updated_at`.

## Deferred / out of scope

- **No data migration for the live `pending_analysis.json`.** Existing per-trigger duplicate rows
  already queued predate `upsert_pending` and won't retro-coalesce — they will simply drain (fast,
  under the raised cap) and are individually still subject to INT14a's materiality/staleness gates,
  so the practical impact is a one-time transient, not an ongoing problem. `pending_analysis.json`
  is checkpoint-EXCLUDED runtime scheduler state, not durable user data, so this is consistent with
  how the store already treats its own contents (no GC, no versioned schema).
- **`_MAX_DISTINCT_STORIES=8`** is a first cut, not empirically tuned against a large real backlog
  post-coalescing — if a subject legitimately develops more than 8 distinct angles inside one
  analysis window, the 9th+ oldest distinct cluster is silently dropped. Acceptable for now since
  INT12's background cap (3 items >24h old) already accepted a similar bound.
- **The "material_news"-sticky trigger** means a row's top-level `trigger` field is no longer a
  reliable "what was the LAST thing that happened" signal once any material_news trigger has ever
  touched it — it answers "does this row have a headline marker," not "what fired most recently."
  `triggers` (the accumulated list) is the source of truth for the latter if ever needed; nothing
  currently reads it besides tests/debugging.

## Agent provenance

- **Decided by:** Claude, dispatched to lane `lane/int14b-per-subject-analysis-coalescing`
  (branched from `feat/batch-aware-dedup`'s tip, which carries INT14a) — owner AFK, ship e2e.
- **Driving artifacts:** owner-diagnosed live-data backlog (230 news_spike + 107 material_news
  rows for one subject; 38 pending, 13h-median drain lag @ the old `_DAILY_ANALYSIS_CAP=50`);
  `docs/launch/BOARD.md`'s INT14a entry (materiality-gated push + staleness guard, merged
  `9c54f087` — no dedicated ADR; this lane is its explicitly-deferred Part B.5 follow-up:
  "the 13h drain-throughput backlog itself is a separate capacity follow-up"); `tests/
  test_pending_analysis_store.py`, `tests/test_news_screen.py`, `tests/test_analyst_sweep.py`,
  `tests/test_evidence_service.py`, `tests/test_analyst_prompt.py`. Second round: the QA/security/
  code-review gate on the first cut (consolidated findings: 1 MEDIUM unbounded-read/loop-stall,
  1 code-review should-fix data-loss race, 2 LOW defensive hardenings) — see the Security
  hardening section above.
- **Alternatives weighed:**
  - *Keep per-trigger rows, just raise the cap.* Rejected — doesn't fix the redundant re-analysis
    of the same story, only lets the backlog drain faster; still N synthesis calls for one story.
  - *Coalesce by subject+trigger (keep `news_spike`/`material_news`/`crash` as separate coalescing
    buckets per subject).* Rejected — the actual goal is ONE analysis per subject's current
    situation, not one per trigger family; a hot subject would still get up to 3 parallel rows.
  - *A row-count-based floor after coalescing (i.e., leave `recent_rows` as-is).* Rejected once
    traced through — it silently undercounts INT12's anti-spam floor once triggers coalesce onto
    one row; replaced with event-flattening (`recent_material_events`) instead.
  - *Rekey the sticky trigger by "most recent" instead of "material_news wins."* Rejected — would
    require rewriting `analyst._event_framing`/`_update_signal`/`analyst_prompt.
    render_evidence_block`'s existing `trigger == "material_news"` checks; the sticky-priority
    design needed zero changes to any of that INT12 logic.
