# Cross-subject fan-out and Asset Brief consolidation

_Status: Accepted · 2026-08-28_
_Related: `2026-08-28-sourced-and-corroborated-intel-signal-updates` (the ADR this one's
Consequences section named as deferred follow-on work — "Two-facet consolidation... cross-subject
fan-out, and the background precompute sweep are NOT part of this change")_

## Context

Two real, evidenced problems, both from a design doc the owner and the previous session built
together:

**Problem A — one real story, tracked and pushed twice.** A watchlist "topic" item (e.g. "KSTL
M&A", `linked_tickers: ["KSTL.DE"]`) and the linked ticker's own asset-type Intel signal were
processed as two fully independent `pending_analysis` rows — each got its own LLM synthesis call,
each could push its own Telegram alert, for what's often the same underlying real-world
development.

**Problem B — the Asset Brief runs its own second, independent LLM call over the same evidence,
and never precomputes.** `monitoring/asset_brief.py`'s `_synthesize_asset` ran a SECOND,
independent structured-output call — same evidence, same LLM, same qualitative territory
(bull/bear key issues, portfolio impact) `monitoring/analyst.py` had just synthesized for the same
subject — and it ran SYNCHRONOUSLY on page open whenever the cached brief was stale (the
`useAssetDetail` `STALE_MS = 60*60*1000` auto-refresh), which is why opening a stale Asset Brief
could take close to a minute. The page also never referenced `analyst_consensus()` (Yahoo
rating/price-target, already rendered next to Key Issues) in its own synthesis.

## Decision

### Problem A: cross-subject fan-out, scoped to same-tick pairs only

`run_analysis_sweep` pairs a topic row with its linked ticker's own asset row **only when both
are pending in the same sweep tick** (`_find_linked_pairs`) — never reaching into an existing
signal with no active pending row, and never remembering a pairing across ticks. This is a
deliberate, narrow scope, not an oversight:

- It is exactly the incident shape (a topic and its linked asset BOTH fire from the same
  underlying news arrival — the RSS layer already tags evidence with both subjects, per
  `rss_poller_topics._augment_topic_feeds_with_linked_asset_subjects` / `_augment_with_linked_topics`).
- It is **order-safe by construction**: `_find_linked_pairs` runs once over the tick's full
  `pending` snapshot, and `run_analysis_sweep` reorders its iteration (`ordered`) so a paired
  topic row is always visited before its absorbed asset partner, regardless of which was actually
  enqueued first in `PendingAnalysisStore`.
- A looser scope (e.g. "fan out to any existing signal for a linked ticker, pending or not")
  would silently attach a claim to a subject nothing currently indicates is actively relevant,
  and would reintroduce exactly the kind of unbounded, hard-to-reason-about cross-subject write
  the prior ADR's Correction round was written to guard against.

`_process_linked_pair` runs **ONE synthesis call, framed around the ASSET side** — its own
trigger/payload/prior notes/position context — not the topic's generic watch-context framing.
This is a deliberate choice: the asset framing is strictly richer (real position sizing, P&L, its
own analysis history) and is what the user actually has money on. The result is applied to BOTH
signals' own timelines via `_apply_synthesis_result` (extracted, byte-for-byte unchanged, from
`_process_row`'s existing branch — a pure code-move, not a rewrite of tested logic), each getting
its own timeline entry `id` referencing the SAME server-built `sources`. The external push fires
**at most once for the pair** — the asset side's `_push_decision` is checked first (the more
actionable framing), the topic side's only as a fallback if the asset side itself didn't clear its
gate.

### Problem B, point 1: consolidate the LLM call

`_AnalysisOutput` (analyst.py) gains `key_issues: list[dict]` and `portfolio_narrative: str`,
both defensively coerced (`_coerce_key_issues`/`_coerce_portfolio_narrative`) the same way
`duplicate_of_id`/`contradicts_id` already are — a malformed value folds to empty rather than
failing the whole structured-output parse. Both are **only requested in the prompt** (via
`_build_prompts`'s new `analyst_consensus_block` parameter) for an asset-type subject that is
**actually held** (`_is_held_instrument`, deliberately a SEPARATE lookup from
`_gather_position_context` rather than widening that function's return shape — `_stub_analyst`'s
widely-shared test double returns it as a fixed 2-tuple, and every existing sweep test relies on
that exact arity). When not held, neither field is mentioned in either prompt at all, so an
ordinary crash/news_spike/material_news/topic call pays no extra token cost.

For a held asset, the prompt also receives an `ANALYST CONSENSUS (Yahoo)` block
(`_fetch_analyst_consensus_block`, off the event loop — it calls the same
`services.asset_detail.analyst_consensus()` the Asset Brief page already renders, 6h-cached) and
is instructed to engage with it in the bull/bear framing.

**`key_issues` sources are server-built, not model-chosen.** asset_brief.py's retired
`_synthesize_asset` asked the model to hand-pick a `{url, quote}` per bullet — but analyst.py's
evidence block never even shows the model a URL at all (the grounding stance the prior ADR
established). Rather than build a second, parallel source-citation mechanism, `key_issues` on
`_AnalysisOutput` is just `{question, bullish: "<text>", bearish: "<text>"}` — plain strings — and
`_write_asset_brief_synthesis` attaches the SAME server-built `source_dicts(evidence)` list
uniformly to both sides of every issue. This is simpler, and consistent with every other grounding
decision in this file.

`asset_brief.apply_analyst_synthesis` is the ONE writer of `key_issues`/`portfolio_narrative`
going forward — it owns AssetBriefStore's write shape (reused, not reinvented), so
`analyst._write_asset_brief_synthesis` just calls it. `asset_brief.py`'s own `_land_analysis`
**stops computing these two fields entirely** — `_synthesize_asset`, `_KeyIssuesOutput`, and
`_KEY_ISSUES_JSON_SKELETON` are deleted (no callers survive; CLAUDE.md's no-dead-code standard).
`_land_analysis` now carries `key_issues`/`portfolio_impact`/`portfolio_narrative` forward from
`prior` unconditionally, and writes that carry-forward FIRST — before the (possibly slow)
facts/peers call even starts — which makes the existing "Lucius's take is ready before the bar
disappears" progressive-write guarantee (owner ask 2026-08-11) hold even more directly than the
original `asyncio.gather`-raced design did, since there is no longer a race to win at all.
`_content_hash` drops `key_issues` from its hash (it's no longer this function's field to track).

**A known, accepted capability loss:** the Perplexity Sonar synthesized answer
(`_fetch_news_items`'s `analyst_context`) used to feed the retired `_synthesize_asset` prompt.
`backend.monitoring.analyst` has no equivalent Sonar-forwarding path today. `_fetch_news_items`
itself still captures the answer (unchanged, still tested); nothing currently threads it into a
prompt. Not silently dropped — flagged here and in the removed tests' replacement comment.

**A known, accepted behavior change:** the explicit "Refresh" button (`force=true`) no longer
regenerates `key_issues`/`portfolio_impact` — that field is now exclusively the Analyst
pipeline's, on its own event-triggered + steady-cadence schedule (point 2 below). Refresh still
does what it always did for news/facts/peers/description. Re-adding a manual key_issues
regeneration path would reintroduce exactly the "second independent LLM call on open" problem
this whole consolidation exists to remove.

### Problem B, point 2: background precompute folded into the existing sweep tick

Rather than a new `scheduler.py` job, a bounded steady-cadence check
(`_enqueue_stale_briefs`/`_key_issues_stale`, capped at `_BRIEF_PRECOMPUTE_CAP = 3`, mirroring
`_PER_SWEEP_CAP`'s own safety-net shape) runs at the END of every `run_analysis_sweep` tick —
**on BOTH exit paths**, including the common "nothing was pending this tick" early return (a quiet
tick is the steady-state case; gating precompute behind "there was real work this tick" would mean
it almost never fires). It enqueues a synthetic `pending_analysis` row (`trigger="brief_stale"`)
for a held instrument whose `key_issues_synthesized_at` has aged past the SAME fresh/aging/stale
threshold `services.asset_detail._freshness_grade` already uses for the page's own freshness
badge — reused, not reinvented — but measured against `key_issues_synthesized_at` specifically,
not the brief's overall `synthesized_at` (a brief can look fresh from a recent news/facts/peers
refresh while its key_issues have never been touched by this pipeline at all, e.g. right after a
position is opened).

The `brief_stale` trigger gets its own neutral `_event_framing` branch ("periodic check-in — no
new trigger this tick") rather than falling through to the crash-shaped default, and its
evidence read deliberately BYPASSES the per-subject watermark (`_process_row` special-cases
`trigger == "brief_stale"`) — a watermarked since-read would usually return nothing for a subject
with no new arrivals, defeating the entire point of a steady refresh. Every other gate
(materiality, decision_relevant, grounded, staleness) still applies unchanged to whether this
tick's development is worth a push or a timeline row; the AssetBriefStore write itself is
unconditional on a successful synthesis, independent of that verdict — "we ran a check and got a
result" is a different question from "was there something worth alerting on."

`_enqueue_stale_briefs` skips any instrument whose `asset:{iid}` subject already has ANY pending
row this tick, regardless of trigger — `upsert_pending`'s merge would otherwise silently
overwrite a real crash/news_spike/material_news trigger's framing with the soft "periodic
check-in" wording (neither side is `material_news`-sticky), softening a genuine alert.

### Problem B, point 3: the frontend stops waiting on-open for what's now kept warm

`useAssetDetail`'s on-open auto-refresh (`needsResearch`) is now additionally gated on
`!isHeld` (`!!eff?.position`) — a held instrument is kept warm by the background precompute
above, so it never needs the on-open POST. **Deliberately HELD-only, not "held or watched"**
(SUPERSEDED for `key_issues` — see Correction below; `portfolio_narrative` genuinely still needs
a position and remains position-aware):
point 1's consolidated key_issues synthesis is itself gated on `_is_held_instrument`, and point
2's precompute only iterates held positions (`services.positions.hydrated()`) — a
watched-but-not-held ticker's Key Issues are not produced by this pipeline AT ALL. Suppressing
its on-open refresh too (an earlier draft of this change did exactly that, gating on
`position || watchlist.following`) would have left its Key Issues card permanently empty forever
— caught before shipping by tracing through what actually keeps each case warm. A
watched-but-unheld ticker, and an ad-hoc `/quote/$symbol` lookup, both keep the PRE-EXISTING
on-open behavior unchanged. The explicit Refresh button (`force=true`) is untouched.

This is a deliberate, narrower scope than a literal reading of "held/watched" might suggest —
extending the consolidation to watched-but-unheld instruments (their own precompute pass, their
own held-gate widened to include them) is a reasonable follow-on but was judged more prudent to
scope out here than to extend three interlocking gates (prompt, precompute, frontend) to a new
case with no test coverage of its own, this late in an already-large change. (SUPERSEDED — see
Correction below: the owner asked for exactly this follow-on shortly after, with the held/watched
split refined rather than the two fields moving together.)

### Problem B, point 4: live numbers split from cached narrative at render time

`AssetDetail.portfolio_impact` (the API field — kept, for frontend compatibility) is now COMPOSED
at request time in `services.asset_detail._compose_portfolio_impact`: a live-computed lead line
(position weight % + unrealized P&L %, read from the SAME `pos_block` the page's own
`YourPositionCard` renders — never reimplemented) prepended to the CACHED, numbers-free
`portfolio_narrative` point 1 writes. A live number can never go stale between Analyst pipeline
passes; a stored number could (and, before this change, routinely did — the whole reason the
LLM was told to stop baking numbers into `portfolio_narrative` in the first place).

## Consequences

- One synthesis call, not two, for a held asset's Intel signal AND its Asset Brief key_issues —
  the core cost/latency fix.
- Opening a HELD asset's page no longer waits on a live LLM call; the brief is kept warm in the
  background. A genuinely brand-new position may show an empty Key Issues card until the next
  precompute pass (bounded by `_BRIEF_PRECOMPUTE_CAP`/the sweep's ~60s cadence) — an accepted
  trade-off, not a regression the owner didn't sign up for (this is the explicit point of the
  design). A watched-but-not-held ticker was originally OUT of this consolidation's scope (see
  point 3's own note) — SUPERSEDED, see Correction below: it now gets `key_issues` and the
  background precompute too, so its on-open refresh is ALSO suppressed like a held instrument's;
  only a subject that's neither held nor watched still pays the on-open wait.
- A watchlist topic and its linked held ticker no longer double-track and double-push the same
  real-world development, when both fire in the same sweep tick. A topic whose linked ticker's
  own pending row lands in a LATER tick still processes independently that later tick (a smaller,
  accepted residual — same-tick-only scope, see Decision).
- Two deliberate, accepted regressions from the pre-consolidation behavior, both flagged above and
  in code comments: the Perplexity Sonar answer no longer reaches the key_issues prompt, and the
  explicit Refresh button no longer regenerates key_issues on demand.
- `backend/monitoring/asset_brief.py` loses `_synthesize_asset`, `_KeyIssuesOutput`,
  `_KEY_ISSUES_JSON_SKELETON`, and `_build_holding_block` (no remaining callers). A meaningful
  slice of `tests/test_asset_brief_refresh.py` (and the fencing suite,
  `tests/test_asset_brief_fence.py`) was updated to observe the same shared mechanisms
  (`_render_news_block`, `_synthesize_facts_peers`) rather than the removed function, or replaced
  outright where the behavior itself no longer exists.

## Correction (2026-08-29, owner scoping fix)

The owner reviewed points 1 and 3 above and corrected the scope: **`key_issues` doesn't need a
position at all — it's a bull/bear debate about the STOCK, useful for anything watched, arguably
MORE useful for something you're deciding whether to buy. Only `portfolio_narrative` genuinely
needs a position** (it's framed around P&L/weight, which only exists for a real holding). The
original Decision tied BOTH fields to one gate (`_is_held_instrument`, surfaced through
`analyst_consensus_block`'s presence) — correct for `portfolio_narrative`, but an unnecessary
restriction for `key_issues` and, transitively, for how far the frontend's on-open-refresh
suppression (point 3) and the background precompute (point 2) could reach. Fixed in the same
shape as the fields themselves, not by inventing a second synthesis call or a second store write:

- **`_is_watched_instrument(instrument_id)`** (`backend/monitoring/analyst.py`) is a new,
  independent check alongside the existing `_is_held_instrument` — true for a `monitor_news`-
  enabled (default true) watchlist ticker item, false for a `type == "topic"` item regardless of
  its own `instrument_id`-shaped fields. Reuses `read_watchlist()` + the same monitor_news
  filtering every other watch-scoped path in this file already applies
  (`news_screen._watched_subjects_and_items`, `rss_poller_hot._monitor_news_entities`) — not a
  new lookup mechanism.
- **`_process_row` and `_process_linked_pair` now resolve TWO ids, not one**:
  `held_instrument_id` (unchanged — held only) and a new `brief_instrument_id` (held OR
  watched). `brief_instrument_id` is what decides whether the ANALYST CONSENSUS block gets
  fetched, whether key_issues/portfolio_narrative get asked for at ALL, and which instrument's
  `AssetBriefStore` row the result gets written to. `held_instrument_id` survives on its own
  only to select the prompt's FRAMING once the ask is already happening — passed to
  `_build_prompts` as a new `is_held: bool` parameter, deliberately not re-derived from
  `position_ctx`'s placeholder string (an explicit bool over string-sniffing).
- **`_build_prompts`'s key_issues/portfolio_narrative instruction text now branches on
  `is_held`**, split into `_key_issues_system_instruction`/`_key_issues_user_instruction` so the
  two copies sit side by side instead of one large inline conditional. `key_issues`'s own
  instructions are IDENTICAL in both branches — it never needed a position to be true. Only
  `portfolio_narrative`'s instruction changes: HELD keeps the original P&L/thesis-exposure
  framing ("why this matters for THIS position... concentration, thesis exposure"); WATCHED-not-
  held gets a new framing ("why this matters to someone TRACKING this ticker... what would need
  to be true to open a position"), explicitly told never to phrase itself as an existing
  position. Both still forbid baked-in numbers — that constraint was never held-specific.
- **`_enqueue_stale_briefs`'s candidate pool widens** from held positions only
  (`services.positions.hydrated()`) to held positions PLUS `monitor_news`-enabled watchlist
  tickers with no position (`_watched_not_held_candidates`, deduped against the held set — a
  held instrument is also auto-watchlisted per ADR 0018, so its own watchlist item must not be
  double-counted as a separate watched-only candidate and waste a cap slot on the same
  subject twice). `_BRIEF_PRECOMPUTE_CAP` stays ONE shared budget across both populations, never
  doubled — held candidates are walked FIRST, so if the cap would otherwise be exceeded, a real
  position's brief claims a slot before a watch-only ticker's (real money on it outranks a
  ticker that's merely being tracked; this ordering is a judgment call, not a hard requirement
  from the owner's correction, but the natural one given the cap's own existing "safety net, not
  an operating control" posture).
- **`useAssetDetail`'s on-open skip widens** from `!isHeld` to `!isHeld && !isWatched`, where
  `isWatched = !!eff?.watchlist?.following && eff.watchlist.monitor_news !== false` (mirroring
  the backend's own `monitor_news` default-true-when-absent convention) — a watched-not-held
  subject is now ALSO kept warm by the widened background precompute, so it no longer needs the
  on-open POST either. A subject that's neither held nor watched (an ad-hoc `/quote/$symbol`
  lookup, or a watch item with `monitor_news: false`) keeps the pre-existing on-open behavior
  unchanged — it is genuinely never touched by this pipeline.
- **`services.asset_detail._compose_portfolio_impact` needed NO code change.** It already drops
  the "Your position: ..." lead line whenever `pos_block` isn't held or has no qty, returning the
  cached `portfolio_narrative` on its own — verified against the actual behavior (not assumed)
  via the pre-existing `test_portfolio_impact_narrative_only_when_not_held` in
  `tests/test_asset_detail_service.py`, which already asserts a watched-not-held instrument's
  `portfolio_impact` is the bare narrative string with no numeric lead line. The read-time
  composition point 4 introduced was already correct for this case; only the WRITE-time prompt
  (this Correction's actual change) needed to stop asking for P&L-framed language when there's no
  position to frame it around.

Nothing else in the Decision above changes: Problem A's same-tick linked-pair scope, point 2's
cadence mechanics (the fresh/aging/stale threshold, the watermark bypass), and point 4's
live-number composition are all unaffected by this Correction — it is scoped entirely to WHO is
eligible for `key_issues`/`portfolio_narrative` and HOW `portfolio_narrative` is framed once
eligible, not to any of the surrounding machinery.

## Correction (2026-08-29, adversarial code-review + security-review round)

A code-reviewer and a security-reviewer RAN this ADR's shipped code (and the `get_asset_context`
tool built alongside it) against crafted/adversarial inputs and found real bugs the Decision
above didn't anticipate. All fixed in the same change; none change the Decision's architecture.

- **Problem B point 2's precompute could LIVELOCK forever on a candidate that can never produce
  `key_issues`** (crypto/commodity — `analyst_consensus` has no sell-side coverage for either,
  see `services.asset_detail.analyst_consensus`'s own skip condition, so the model is never even
  ASKED — `_build_prompts`'s `analyst_consensus_block` gate stays empty). A COMPLETED
  `brief_stale` pass that came back empty never stamped `key_issues_synthesized_at`, so
  `_key_issues_stale` read "never synthesized" FOREVER and `_enqueue_stale_briefs` re-enqueued
  the SAME instrument every tick — burning the whole `_DAILY_ANALYSIS_CAP` in a few hours and
  going dark for the rest of the day. Two fixes: `_write_asset_brief_synthesis` now stamps the
  clock even on an empty result (`asset_brief.stamp_key_issues_checked` — the clock means "we
  checked", not "we found something to show"), and `_enqueue_stale_briefs` now skips a
  crypto/commodity candidate outright (`_analyst_consensus_impossible`) so it never even enters
  the queue. (**Superseded 2026-08-30** — see the struck-through passage further down this same
  document: the crypto/commodity skip was removed, corrected to the same treatment as any other
  asset. Left here unstruck since this passage's OWN point — the livelock fix via unconditional
  clock-stamping — is still current; only the crypto/commodity-skip HALF of it is superseded.)
- **Problem A's fan-out pairing claimed only the TOPIC row, never the absorbed ASSET row.** The
  asset row's status stayed "pending" for the ENTIRE synthesis call (an LLM round-trip), so a
  real trigger arriving for that same asset subject mid-flight silently MERGED into it
  (`upsert_pending` targets any unclaimed pending row for a subject) and was then marked done,
  UNANALYZED, the instant the pair finished. `run_analysis_sweep` now `claim()`s BOTH rows
  before dispatching `_process_linked_pair` — a failed second claim (something else genuinely
  holds that row) falls back to processing the topic row SOLO instead of assuming the pairing
  is still safe, and the `mark_failed` path on an exception no longer touches a row this tick
  never actually claimed.
- **Problem A's pairing had no relevance check at all** — `_find_linked_pairs` paired ANY
  topic+asset row sharing a `linked_tickers` relationship, proven to merge two unrelated real
  stories into one synthesis call (the topic's own actual story silently overwritten by the
  asset's unrelated one, misattributed with the topic's own sources). Two gates now apply: a
  synthetic `brief_stale` row can never absorb or be absorbed into a pairing (it carries no real
  trigger content to compare — this is the cross-subject version of the same-subject softening
  `_enqueue_stale_briefs`'s own already-pending skip-guard exists to prevent), and every
  remaining candidate pair must show genuine evidence-content overlap
  (`_linked_pair_evidence_relevant`, reusing `analyst.py`'s own `_texts_overlap` — the SAME
  primitive the sibling ADR's corroboration/duplicate-relevance fixes already use, not a new
  heuristic) before being honored; no overlap falls back to processing both rows solo.
- **The `brief_stale` trigger's null-watermark-forces-fresh-read (Problem B point 2) also
  silently dropped the read LIMIT to the bootstrap value (8), not the full watermarked bound
  (200).** `_update_signal` still stamped the watermark as if the read had covered everything,
  so a `brief_stale` pass on a subject with >8 unread evidence rows permanently excluded
  whatever lay beyond the first 8 — the exact "evidence gone forever" failure the sibling ADR's
  own INT14b watermark fix exists to prevent, reintroduced via this new trigger type.
  `_read_and_collapse_evidence` now takes an explicit `limit` override, and `_process_row`
  passes `_WATERMARKED_READ_LIMIT` for this trigger specifically.
- **Problem B point 4's live/cached composition had a real race**: `asset_brief._land_analysis`
  captured `prior = store.get(instrument_id)` once, in `_do_refresh`, before the refresh's slow
  evidence/search work even started, and built `carried_forward`
  (key_issues/portfolio_impact/portfolio_narrative — the three fields this function never
  computes) from that STALE snapshot at write time. If the Analyst pipeline's own background
  precompute (this ADR's own Problem B point 2) landed a fresher key_issues/portfolio_narrative
  for the SAME instrument while a manual Refresh was mid-flight, the Refresh's own write
  reverted it back to the pre-refresh snapshot. `carried_forward` is now re-derived from a FRESH
  `store.get(instrument_id)` read immediately before EACH write (`_brief_carry_forward`).
- **`get_asset_context` (the AI tool this ADR's `portfolio_impact` field composition was built
  to serve) had four bugs of its own**: (1) it never surfaced `portfolio_impact` at all, despite
  being the exact field this ADR's point 4 built to answer "what should I do with this
  position" — now included in the tool's output. (2) it called `read_asset_detail`/
  `analyst_consensus` directly on the event loop (both blocking I/O) instead of via
  `asyncio.to_thread`, unlike the sibling `_fetch_analyst_consensus_block` in `analyst.py` this
  same ADR's Decision added — both calls now offload the same way. (3) its own code comment
  claimed genuine read-only status, but `read_asset_detail`'s `allow_stale=True` background-
  refresh path (`services.prices._refresh_in_background`) hardcoded `record_history=True`
  regardless of the caller's own flag — a cache entry aged between the fast TTL and the stale
  hard limit (the common case a minute or two after any page load) left a `price_history` row
  behind anyway; that path now threads `record_history` through. (4) none of its assembled
  free text (intel signal summary/action_items/timeline summaries, Asset Brief key_issues/
  facts/peers, the asset description — all LLM-synthesized or feed-derived) was routed through
  `core.untrusted.sanitize_untrusted`, the uniform posture every other tool handing the model
  free text already takes; it now is.

Separately, and NOT part of the security review — a direct owner-reported data-loss bug in
`_update_signal`'s timeline (see the sibling ADR's own Correction for the full writeup):
storage's hard `_TIMELINE_CAP` (20 entries) was removed entirely. This ADR's Problem A fan-out
and Problem B precompute both write through the SAME `_update_signal` choke, so both now
compose against a genuinely unbounded timeline — no change to either mechanism's own logic, but
worth noting here since a long-lived, actively-fanned-out signal is exactly the shape most
likely to accumulate a long history.

## Addendum (2026-08-29, owner ask): an on-demand top-up for a specific instrument

Problem B point 2's precompute is CAPPED (`_BRIEF_PRECOMPUTE_CAP = 3` per ~60s tick) — a
deliberate safety net, not an operating rate control (see that point's own docstring). The real
consequence, flagged by the owner after this ADR shipped: a specific held-or-watched instrument
can legitimately sit stale for a while when enough OTHER candidates compete for the same cap
slot each tick, with no way to force a fresher pass short of waiting.

**Decision**: `GET /api/intel/assets/{instrument_id}` (opening the Asset Detail page) now checks
whether the instrument is held-or-watched AND its `key_issues_synthesized_at` is older than a
NEW, dedicated `_KEY_ISSUES_FORCE_REFRESH_HOURS = 12` constant (`backend/monitoring/analyst.py`)
— deliberately separate from `_key_issues_stale`'s reuse of `services.asset_detail.
_freshness_grade`'s days-scale fresh/aging/stale grade, which is tuned for the background
sweep's own cadence economics, not for "stale enough to justify an immediate, out-of-band LLM
call the instant a human opens this exact page." If stale past that fuse, the route schedules
`analyst.trigger_on_demand_brief_refresh(instrument_id)` via `asyncio.create_task` — never
awaited — and returns the cached response immediately either way. The read path's own response
time is provably unaffected: `tests/test_intel_assets_api.py::
test_get_asset_detail_response_time_unaffected_by_background_refresh_trigger` stubs the
synthesis call to sleep 2s and asserts the GET still returns in well under 1s.

**Reused, not reinvented**:
- The fire-and-forget shape mirrors `price_monitor._instant_news_freshen`'s established
  "don't wait for the next cycle, trigger it right now, in the background" convention — a bare
  `asyncio.create_task(...)` with no kept reference, matching that call site exactly rather than
  inventing a different background-task idiom.
- `trigger_on_demand_brief_refresh` reuses `_process_row` — the SAME synthesis logic a
  `brief_stale` row's normal drain already runs — rather than a parallel code path. It builds
  its own `brief_stale` pending row via the existing `upsert_pending` choke and processes it
  directly instead of waiting for a future sweep tick to claim it.
- The in-flight dedup guard is the SAME one `_enqueue_stale_briefs` uses:
  `PendingAnalysisStore.has_active(subject)` (new — broadened from a pending-ONLY check to
  pending-OR-claimed, since a claimed row means a synthesis call is actively running right now,
  either the sweep's own drain or an earlier on-demand call still in flight). `_enqueue_
  stale_briefs`'s own guard was widened to call this same method, so the periodic precompute and
  the on-demand trigger can never disagree about what counts as "already being handled" for a
  subject and can never double-fire against each other.
- ~~Also skips a crypto/commodity candidate outright (`_analyst_consensus_impossible`)~~ —
  **superseded 2026-08-30** (owner correction, `analyst._build_prompts`'s `want_key_issues`
  docstring): the skip denied these assets key_issues/portfolio_narrative entirely, when analyst
  consensus was only ever meant as optional enrichment, not a requirement — the loop-prevention
  half of the sibling H1 fix (`_write_asset_brief_synthesis` stamping `key_issues_synthesized_at`
  even on an empty result) already closes the livelock on its own, so the skip was needlessly
  costing these assets a narrative for no remaining safety benefit. Crypto/commodity now fire
  the on-demand pass exactly like any other held/watched instrument.

**Idempotency is structural, not lock-based**: every guard/state-changing call in
`trigger_on_demand_brief_refresh` (`_is_held_instrument`/`_is_watched_instrument`,
`AssetBriefStore.get`, `has_active`, `upsert_pending`, `claim`) is synchronous with NO `await`
between the guard check and the claim — Python only yields to another coroutine at an `await`
point, so two overlapping calls for the same instrument (three quick tab reopens, each firing
its own `create_task`) can never both pass the guard. Proven with real concurrency in
`tests/test_analyst_sweep.py::
test_trigger_on_demand_brief_refresh_idempotent_on_concurrent_reopen` (`asyncio.gather` over two
un-awaited coroutines, not merely two sequential calls).

**The frontend indicator (owner clarification)**: a silent background pass was judged
insufficient — the owner wants a visible, but explicitly QUIET/non-blocking, indicator while an
on-demand pass runs, never the full-skeleton takeover (`ResearchingStatus` inside
`NewsSectionSkeleton`), since the page already has content to show whenever this fires. Verified
against the real components rather than assumed from their names:
`ResearchingStatus`/`NewsSectionSkeleton` render the "no content yet, searching the web…"
first-load takeover; `RefreshingBanner` is the lighter, sticky, non-blocking "updating in the
background" indicator that layers over already-rendered content — confirming the intuition, but
checked rather than trusted.

`trigger_on_demand_brief_refresh` stamps `refresh_stage` around its own pass
(`asset_brief.stamp_refresh_stage`, new) — "analysing" before `_process_row` starts, "ready" in
a `finally` regardless of success/failure — so `useAssetDetail`'s existing `RefreshingBanner`
machinery has something to react to. `stamp_refresh_stage` is a RAW (non-monotonic) write,
deliberately NOT `_do_refresh`'s own `_advance_refresh_stage` rank guard: that guard exists to
stop ONE refresh's two concurrent writers regressing each other's progress mid-flight, and reusing
it here would mean a stale "ready" left over from a long-finished PRIOR cycle silently outranks
(and swallows) this call's own "analysing" stamp, so the on-demand pass would never show as
in-flight at all. Scoped to the on-demand trigger alone — the steady-cadence precompute sweep and
a real news-triggered pass never call this, both remaining silent-by-design background work
nobody is watching in real time.

The frontend previously drove `RefreshingBanner`'s visibility (and the 2s fast-poll that feeds
it) purely off `useAssetDetail`'s own local `refreshing` flag, bounded to an in-flight POST this
hook itself issued — which the on-demand trigger never does. `useAssetDetail.ts` now ALSO
watches `data.refresh_stage` for a transition it didn't cause itself (a new
`backgroundRefreshing` state, gated off while an explicit `refreshing` POST is already in
flight, so the two never double-arm), arming the SAME fast poll and feeding the SAME
`RefreshingBanner` — never the skeleton or the Refresh button's own disabled/label state, both
of which stay keyed on `refreshing` alone. This watch is BOUNDED
(`BACKGROUND_REFRESH_WATCH_MS = 60s`), not an unconditional trust of `refresh_stage`: the
backend always stamps "ready" in a `finally`, but a wedged process (a hard crash mid-pass) could
in principle leave a non-ready value sitting forever, and an unbounded frontend watch would pin
a phantom banner on the page permanently in that case. `tests/test_analyst_sweep.py::
test_trigger_on_demand_brief_refresh_stamps_ready_even_on_synthesis_failure` proves the backend
side of this guarantee; `useAssetDetail.poll.test.tsx`'s new "on-demand background refresh"
describe block proves the frontend side, including the specific test the coordinator asked
for — that a stamped non-ready `refresh_stage` lands in the non-blocking `backgroundRefreshing`
branch (`refreshing` stays false throughout), never the blocking one.

**Known, accepted trade-off**: the on-demand trigger has no budget cap of its own beyond the
12h-per-instrument staleness gate and the in-flight dedup guard — it does not check
`_DAILY_ANALYSIS_CAP`/`_PER_SWEEP_CAP` the way `run_analysis_sweep`'s own drain does, since it
runs entirely outside that drain loop. This is judged self-limiting by construction: it only
fires once per instrument per >12h window, and only for an instrument a human is actually
looking at right now — bounded by how many distinct instruments a real user opens in a day, not
by an unbounded queue. Left uncapped deliberately rather than threading a shared budget through
a second call path for a narrow, human-gated trigger.
