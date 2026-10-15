# Acceptance — Intel signal timeline is meaningful (INT15)

Owner-found bug (screenshots): every entry in a signal card's expanded timeline rendered as a raw
UTC ISO timestamp (`2026-07-17T20:16:48.256429+00:00`) plus the literal word "Analysis", with the
model's actual "why" (`summary`) never shown. Root causes and fix are split backend (timeline
semantics) + frontend (rendering) — these criteria are the QA contract for both halves.

## INT15-1 — A timeline entry's date renders formatted, not raw ISO
- **GIVEN** a signal's `timeline` entry carries a raw ISO8601 `date` (e.g.
  `2026-07-17T20:16:48.256429+00:00`)
- **WHEN** the user expands that signal card's timeline in the Intel feed
- **THEN** the entry shows a localized/relative label ("7h ago", "yesterday", …) — the raw ISO
  string is never rendered verbatim
- **AND** hovering the date shows the precise local date/time (title tooltip)

## INT15-2 — The entry's real content (summary) renders, not a generic title
- **GIVEN** a timeline entry carries `title: "Update"` (or "New development") and a real
  `summary` (the analysis "why")
- **WHEN** the timeline is expanded
- **THEN** the `summary` text renders as the entry's body
- **AND** the meaningful `title` renders as a small heading above it (not the old hardcoded
  "Analysis")
- **AND** if `summary` is empty, the entry falls back to showing `title` alone (no blank body)

## INT15-3 — A `no_change` analysis does not add a timeline row
- **GIVEN** a signal already has N timeline entries
- **WHEN** the analyst sweep re-analyzes that signal's subject and the model's own materiality
  verdict is `no_change`
- **THEN** the signal's `summary`/`updated_at` still refresh (the Intel feed stays current)
- **AND** the timeline stays at exactly N entries — no "Analysis" (or any) row is appended

## INT15-4 — `new`/`update` analyses append a meaningful entry
- **GIVEN** a signal is re-analyzed with materiality `new` or `update`
- **WHEN** the sweep completes
- **THEN** exactly one new timeline entry is prepended, titled "New development" (for `new`) or
  "Update" (for `update`), carrying the real `why` as `summary`

## INT15-5 — Storage keeps a signal's FULL history, forever (SUPERSEDED — see F11 below)
**SUPERSEDED 2026-08-29 (owner-reported, security-review round).** This criterion originally
read "the timeline is capped at 20 entries — oldest dropped, newest at index 0". That cap was a
real, evidenced data-loss bug: a live KSTL signal's stored timeline held only its most recent 20
entries (spanning ~26 days), silently evicting a genuine months-old pushed alert (a hallucinated
"Uber terminates Taiwan deal" claim) with no way to ever produce a record of it again —
contradicting this app's "never silently degrade/discard data" invariant. The owner's direct
instruction: remove the cap entirely. The corrected criterion:
- **GIVEN** a signal's timeline already has N entries (N unbounded — no cap)
- **WHEN** another materially-new analysis appends an entry
- **THEN** the timeline has exactly N+1 entries afterward — nothing is ever dropped, and the
  newest entry lands at index 0

## INT15-5b — Reads of the timeline stay bounded even though storage does not (F11)
Unbounded STORAGE does not mean every READ may be — an old, hot signal can now genuinely
accumulate hundreds of entries over months/years.
- **GIVEN** a signal's stored timeline has more than 20 entries
- **WHEN** `GET /api/intel/signals` (the live feed) returns it
- **THEN** the embedded `timeline` is bounded to the most recent 20, with `showing_timeline`/
  `total_timeline` reported so the frontend knows more exist
- **AND** `GET /api/intel/signals/{id}` returns that SAME signal with its FULL, unbounded
  timeline — the "show all" fetch
- **AND** `get_asset_context` (the AI tool surface) is unaffected — its own pre-existing
  `timeline_limit`/showing/total bounding already handled this correctly against an unbounded
  source, no change needed there
- **AND** `StoryArcCard`, once expanded, offers a "Show N earlier" control that fetches and
  reveals the rest on demand — never fetched up front

## INT15-6 — The create-path first entry is meaningful too
- **GIVEN** a news-spike/material-news row fires for a subject with NO prior signal
- **WHEN** the analyst creates the new signal
- **THEN** its first (only) timeline entry is titled "New development", not "Analysis"

## Note on existing polluted data
A live signal accumulated 30+ generic "Analysis" entries before the original INT15 fix (one per
~60s sweep tick, including every `no_change` re-analysis). INT15-3 stops any further pollution
at the SOURCE (no `no_change` row is ever appended); the original INT15-5 cap this file
documented previously is GONE (see above) — a signal's history, polluted or not, is never
trimmed by this pipeline again. Live data was not touched to produce this evidence
(`~/.open-net-worth/**` is off limits per policy).

## Verification status
- Backend: `tests/test_analyst_sweep.py` — `test_materiality_no_change_suppresses_push` (updated
  to assert `timeline == []`), `test_materiality_no_change_does_not_append_timeline_entry`,
  `test_materiality_new_appends_meaningful_timeline_entry`,
  `test_materiality_update_appends_meaningful_timeline_entry`,
  `test_timeline_storage_is_never_truncated` (F11 — supersedes the old
  `test_timeline_capped_at_twenty_entries`),
  `test_news_spike_create_path_first_entry_has_meaningful_title` — all green;
  `tests/test_intel_signals_api.py` — `test_list_feed_bounds_a_long_timeline_and_reports_showing_total`,
  `test_get_one_signal_returns_full_unbounded_timeline` (F11's read-bounding split) — all green;
  `ruff check`/`ruff format --check` clean; `lint-imports` 5/5 contracts kept.
- Frontend: `StoryArcCard.test.tsx` — `StoryArcCard timeline formatting (INT15)` describe block
  (raw-ISO-not-rendered-verbatim + summary-shown; "Analysis" placeholder never rendered) — green
  (vitest, 2358/2363 suite-wide passing; the 5 pre-existing `ModelEffortRow.test.tsx` failures are
  unrelated baseline breakage, confirmed present before this change); `tsc --noEmit`,
  `npm run lint:classes`, `npm run build` all clean.
- Remaining for full UI Quality Gate: `make qa-e2e` for INT15-1/INT15-2 + `make ui-gate` + screenshot
  evidence (isolated QA env, port 8090–8099, `ONW_QA_MODE=1`) — see the lane report for the
  Playwright-driven screenshot capture used in lieu of a live `make qa-e2e` run.
