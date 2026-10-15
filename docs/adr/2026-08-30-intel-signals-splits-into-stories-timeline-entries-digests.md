# `intel_signals` splits into `intel_stories` + `signal_timeline_entries` + `daily_digests`

_Status: Accepted (Phase 1 of a phased plan — see Consequences for what's landed vs. deferred)
· 2026-08-30_
_Related: `docs/rfc/intel-signals-data-model.md` (the RFC this ADR records), migration
`202608241937_session_messages` (the structural precedent for the child-table split), ADR
`0004-intel-deterministic-signals-and-citations`, ADR
`2026-08-28-cross-subject-fan-out-and-asset-brief-consolidation`, ADR
`2026-08-28-sourced-and-corroborated-intel-signal-updates`_

## Context

`intel_signals` was one multi-writer payload table backing three genuinely different shapes:
7 story-arc types (`asset`/`topic`/`risk`/`opportunity`/`macro`/`monitoring`/`price_alert`)
sharing one shape, plus `daily_digest` rows that were structurally foreign to it (no
`ticker`/`timeline`, a `daily-digest-<date>` id, always upserted never a list). Six independent
call sites (`analyst.py`, `intel_agent.py`, `price_monitor.py`, `crash_watch.py`,
`topic_analyst.py`, `risk_signals.py`) built a full story dict — WITH an inline `timeline`
list — and called `IntelSignalsStore().upsert()`/`.insert()` directly, no shared choke. Two
concrete, load-bearing pains: a `summary` key meant two different things at two different
scopes (a story's running state vs. one timeline entry's own dated development, both written
by the same `analyst._update_signal` call), and `timeline` was an unbounded JSON list (F11
removed the storage-side truncation) nothing indexed into — every read paid to deserialize the
whole array even when it only needed the newest 20.

Every id-construction site that embedded a ticker (`crash-watch-{ticker}`,
`price-alert-{ticker}-{key}`, a `news-{ticker}` fallback) meant a renamed or delisted ticker
could silently orphan a story's own history, and gave any future cross-subject work (see
Claims below) nothing stable to key off.

The solution-architect's RFC (`docs/rfc/intel-signals-data-model.md`) verified every claim
against this worktree's real code before proposing a design, and recommended NO-GO on a
`claims`/`group_key` cross-subject dedup table, citing ADR `2026-08-28-cross-subject-fan-out`'s
twice-deliberate narrow scoping and the risk that "10+ tickers with fragmented signals" partly
reflects legitimate separate concerns (multiple price alerts, a linked topic) rather than
duplicate pushes.

## Decision

**Split the schema — build now, in one big-bang Alembic revision** (`202608301500`, modeled
directly on the `session_messages` migration's own create+backfill+verify+drop discipline: no
dual-read transition window left to rot):

- **`intel_stories`** — promoted query-surface columns (`type`/`instrument_id`/`ticker`/
  `label`/`status`/lifecycle flags) + a verbatim `payload`, the `accounts`/`evidence`
  precedent (real per-type extra fields exist — e.g. `risk` carries `status_reason` — so
  promoting every field would be guessing past what's used). `summary` renamed
  `current_summary` (collision-avoidance against a timeline entry's own `summary`).
- **`signal_timeline_entries`** — a direct structural copy of `session_messages`, with one
  deliberate difference: it carries BOTH a DB-assigned `seq` (ordering) AND the pre-existing
  `uuid4().hex` business `id` (`duplicate_of_id`/`contradicts`/`supersedes` resolve against
  it — ADR `2026-08-28-sourced-and-corroborated-intel-signal-updates`), because unlike a chat
  message, a timeline entry's identity IS referenced across turns.
- **`daily_digests`** — `date` as the literal primary key (not a synthetic id), preserving the
  existing same-day EOD-overwrites-premarket behavior exactly rather than silently fixing it.
  Deliberately WIPE-ONLY, not bridged into `portable_bridge.BRIDGED` (a delivered-digest record
  is regenerable notification history, not a book fact) — checkpoint inclusion is unchanged.

**Identity fix, in the SAME migration**: every story row's id gets a new `instrument_id`
column, resolved and — for the three known ticker-embedded id shapes — rewritten, verified
against a real production-database backup (256 rows) rather than assumed. This directly
contradicted one of the RFC's own sub-claims (see Amendments below).

**Write choke**: `services.intel_stories.apply_story_change` wraps
`db.engine.locked_unit_of_work(intel_stories, signal_timeline_entries)` — one story upsert +
one optional new entry + one optional supersede flip as ONE transaction, matching
`positions_service.apply_position_change`'s shape. This dispatch builds and proves the choke;
wiring the six writers onto it is separate (Consequences).

**`core/news.py` relocation** (RFC task #2, done in this same dispatch — zero DB dependency,
safe to parallelize): `cluster_by_content_overlap` (from `monitoring/analyst_prompt.py`) and
six news-list quality filters (from `monitoring/asset_brief.py`) moved to `core/news.py` so
`services/asset_detail.py`'s future live-query news read (RFC §5, not built yet) can call them
without an illegal `services -> monitoring` import — the same reason ADR
`0004-intel-deterministic-signals-and-citations` moved `_hostname` to `core/urls.py`.

## Amendment (owner override, before implementation started)

**The RFC's `claims`/`group_key` NO-GO is overridden: GO, sequenced after this identity fix.**
The RFC's reasoning for deferring is sound in general (a version of this already shipped
narrow, twice, deliberately — ADR `2026-08-28-cross-subject-fan-out`; some apparent
fragmentation is legitimate, not duplicate) — but there is now DIRECT, PROVEN evidence of the
failure mode the RFC treated as unmeasured: on the same night this investigation happened,
KSTL's topic-side ("M&A") signal and its asset-side signal both independently pushed about the
same real Uber tender-offer development, ~10 hours apart, found directly in the production DB.
This is exactly the cross-subject SAME-FACT duplication case a `claims` registry exists to
catch — not the same thing as a user's multiple price alerts on one ticker (RFC's own
"7-of-8-types share one shape" finding already keeps `price_alert` correctly distinct from a
news-driven story signal; this override does not blur that line). The RFC's OWN sequencing
logic for when to build it is kept: `group_key_for` needs a stable `instrument_id`-based
identity, not a ticker string, which is why this happens AFTER the identity fix, not folded
into it. This schema deliberately bakes no ticker strings into anything a future claims
registry would need to key off (`instrument_id` is the promoted identity column throughout).

## Consequences

**Done and tested in this dispatch**: the three-table schema (`backend/db/tables.py`), the
migration (`202608301500_intel_stories_split.py` — tested against a real production-database
backup, 256 real rows, up→down→up round-tripped, AND against a from-scratch empty database),
`IntelStoriesStore`/`DailyDigestStore` (`backend/db/`), `services.intel_stories`
(`list_signals`/`get_signal`/`set_state`/`mark_all_read`/`delete_signal`, ported onto the new
schema, same wire contract) and the new choke `apply_story_change` (own test suite, including a
forced-mid-transaction-failure atomicity proof), and the `core/news.py` relocation.

**Also done and tested, in a follow-up same-branch dispatch (RFC task #4)**: all six scattered
writers (`monitoring/{analyst,intel_agent,price_monitor,crash_watch,topic_analyst}.py`,
`services/risk_signals.py`) now route every story mutation through `apply_story_change` — no
caller touches `IntelStoriesStore` directly for a write anymore. `apply_story_change` gained a
`touch_entry_id` kwarg (bumps one entry's own `as_of` without inserting/superseding — needed by
`analyst._update_signal`'s duplicate-match branch, which is otherwise unchanged: it stays a pure
function, now taking `timeline` as an explicit parameter instead of reading it off `signal`).
`IntelStoriesStore` gained `find_story_by_ticker`/`find_story_by_label`/`touch_entry`. The
identity fix's runtime half landed too: `crash_watch.build_signal`, `price_monitor._fallback_
signal`/`_ai_signal` (via `_run_alert_lane`'s `price-alert-` id), and `analyst._create_news_spike_
signal`'s `news-` id all embed `instrument_id` now, falling back to the legacy ticker-keyed id
(logged) only when one isn't resolvable — the same defensive posture the migration itself used.
`services/inbox.py`, `api/intel_signals.py`, `ai/tools/{intel,asset_context}.py` are re-pointed
at `IntelStoriesStore`/`services.intel_stories`; `monitoring/digest.py` writes via
`DailyDigestStore` and now forwards its own `trigger` argument (the pre-existing bug this ADR's
own migration section found — see `docs/DATA_MODEL.md`). `services.intel_stories.list_signals`/
`get_signal`/`set_state` alias the DB-internal `current_summary` column back to the `summary` key
every existing HTTP/frontend caller already reads — the RFC §2.1.1 rename is DB-internal only.
This wire-alias was a genuine GAP in the schema dispatch above's own "same wire contract" claim
(its `list_signals`/`get_signal` tests seeded `current_summary` directly and never asserted the
actual `summary` key a response carries) — found and closed in this dispatch, alongside
`price_monitor._current_asset_signal`'s crash-watch READ lookup, which keeps a symmetric
ticker-keyed fallback (`crash-watch-{iid or ticker.lower()}`) since a holding whose instrument_id
wasn't resolvable at WRITE time still lands under the legacy id. The app boots again as of this
dispatch.

**Still deliberately NOT done — separate, later dispatches, per the RFC's own phased plan**:

- The portable bridge for the three new tables (`backend/db/intel_signals_io.py`, RFC task #5)
  — `portable_bridge.py`'s `"intel_signals.json"` entry was REMOVED outright (not migrated),
  since the table it bridged no longer exists under that name; a bespoke bridge mirroring
  `money_io.py`'s composed-child-table shape is unbuilt. Until it lands, intel story/digest data
  is absent from the exportable bundle and `wipe_all`'s reach entirely.
- `asset_brief.news[]` → live query over `evidence` (RFC §5, task #6) — `core/news.py` is ready
  for it, but `services/asset_detail.py`'s read side is unchanged.
- The `claims`/`group_key` registry itself (this ADR's own Amendment) — sequenced next, now that
  task #4's identity fix has landed in the writer paths that mint new rows.

`analyst._update_signal` stays UNCHANGED in body through this — it gained an optional
`timeline=` kwarg that defaults to `signal.get("timeline")` when omitted, so its own extensive
characterization test suite (duplicate/contradict/supersede branches, security-review fixes
F1-F5) needed zero changes; production callers now fetch the story's real timeline via
`IntelStoriesStore.entries_for_signal` and pass it explicitly, then derive `apply_story_change`'s
`new_entry`/`supersede_entry_id`/`touch_entry_id` from `_update_signal`'s own return values
(`_signal_write_ops`) rather than diffing before/after timelines. `risk_signals._synthetic_key`
and `intel_agent._persist_story`'s ids needed no identity fix (already instrument_id-keyed /
already random UUIDs, respectively).
