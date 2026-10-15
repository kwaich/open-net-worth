# Acceptance — News sources panel [INT9 Phase C]

`NewsSourcesPanel.tsx` is the second Settings rail item in the Monitors workspace
(`MonitorsWorkspace.tsx`, alongside "Monitoring & alerts"), reading/writing INT9 Phase B's
`GET/PATCH/POST/DELETE /api/intel/sources` surface. These 19 criteria (SRCP-1…SRCP-19) are the
QA contract for that UI.

## SRCP-1 — Rail placement
- **GIVEN** the user opens Watchlists & monitors (the Monitors workspace)
- **WHEN** they look at the SETTINGS section of the left rail
- **THEN** a "News sources" item appears directly under "Monitoring & alerts"
- **AND** clicking it renders `<NewsSourcesPanel />` inside `.intel-mm-main`, wrapped in a new
  `.intel-mm-sources` div with an `.intel-mm-list-title` "News sources" heading + an
  `.intel-mm-settings-sub` subtitle (mirrors the "Monitoring & alerts" sub-view exactly)
- **AND** no new route/tab/App.tsx change was needed — it is purely a rail item + view branch

## SRCP-2 — Loading state
- **GIVEN** the panel mounts and `GET /api/intel/sources` has not yet resolved
- **WHEN** it renders
- **THEN** the shared `Loading` primitive shows with the label "Loading news sources…"

## SRCP-3 — Error state + Retry
- **GIVEN** `GET /api/intel/sources` fails (non-2xx or network error)
- **WHEN** the panel renders
- **THEN** the shared `ErrorState` shows "Could not load news sources." with a Retry button
- **AND** clicking Retry re-fetches; a subsequent success renders the normal table

## SRCP-4 — Defensive empty state
- **GIVEN** the fetch succeeds but returns `{sources: []}` (the built-in seed never ran)
- **WHEN** the panel renders
- **THEN** the shared `EmptyState` shows title "No sources found" and body "Lucius couldn't find
  its source registry — this usually means the built-in seed didn't run. Try reloading, or check
  the server logs." with a Retry action

## SRCP-5 — Two sections with live counts
- **GIVEN** the registry has both builtin and user rows
- **WHEN** the table renders
- **THEN** a divider row reads "Built-in sources · {n}" before every `provenance=builtin` row,
  and a second divider reads "Your feeds · {n}" before every `provenance=user` row
- **AND** when there are zero user rows, a muted line reads "You haven't added any feeds yet."
  in place of any row (never shown when at least one user row exists)

## SRCP-6 — Health bucketing: Fast / Slow / Stalled
- **GIVEN** an enabled row with no failure streak and non-null `latency`
- **WHEN** `classifySourceHealth` runs
- **THEN** `median_hours <= 1` → pos "Fast"; `<= 6` → warn "Slow"; otherwise → neg "Stalled"
- **AND** every case carries a detail tooltip "Median {formatLag(median)}, p75 {formatLag(p75)}"

## SRCP-7 — No-data-yet (muted) vs No-data-in-7d (red) by scope/kind
- **GIVEN** an enabled row with `latency: null`
- **WHEN** its `scope` is `firehose` AND its `kind` is NOT one of ticker/search/topic_engine AND
  it was added more than 24h ago
- **THEN** the chip reads neg "No data in 7d" / "Nothing from this feed in the last week — it may
  be down."
- **AND** every other null-latency case (added <24h ago, OR `scope=per_entity`, OR
  `kind` ∈ {ticker, search, topic_engine} regardless of firehose scope) reads muted "No data yet"
  / "No stories logged from this source in the last 7 days." — per-entity/topic-engine rows
  NEVER escalate to red

## SRCP-8 — Disabled and Failing override health
- **GIVEN** a row with `enabled: false`
- **WHEN** the chip renders, regardless of latency or failure_streak
- **THEN** it reads muted "Disabled" / "You turned this off." (checked first, before anything else)
- **AND GIVEN** an enabled row whose `failure_streak.backing_off` is true OR
  `consecutive_failures >= 3`, the chip reads neg "Failing" / "Recent fetches are failing —
  Lucius is backing off." even when latency looks healthy

## SRCP-9 — Toggle: optimistic update + revert on failure
- **GIVEN** a row's On switch
- **WHEN** the user clicks it
- **THEN** the switch flips immediately (optimistic) and `PATCH /api/intel/sources/{id}
  {enabled}` fires
- **AND** on a failed PATCH the switch reverts to its prior state and a banner reads "Could not
  update — try again." (reusing `.intel-wlc-add-err`)
- **AND** the switch's title always states the pending action: "Turn off — Lucius stops fetching
  this source right away." when on, "Turn on — Lucius starts fetching this source on its next
  check." when off

## SRCP-10 — Cadence change updates the interval text
- **GIVEN** a row's Cadence `<Select>` (Hot/Fast/Slow)
- **WHEN** the user picks a different cadence
- **THEN** `PATCH /api/intel/sources/{id} {cadence}` fires and, on success, the row is replaced
  with the server's response — the secondary text "no more than every {fDuration(min_interval_sec)}"
  re-renders from that fresh row (never a stale cached value)

## SRCP-11 — Add feed: happy path
- **GIVEN** the Add-feed form (Feed name + Feed URL) below the table
- **WHEN** the user fills both fields and clicks "Add feed"
- **THEN** `POST /api/intel/sources {label, url}` fires; on success the fields clear and the
  panel re-fetches the list (the new row appears under "Your feeds")
- **AND** "Add feed" stays disabled until BOTH fields are non-empty

## SRCP-12 — 422 on label routes inline
- **GIVEN** the POST returns 422 with a `label: ...`-prefixed detail
- **WHEN** the response is handled
- **THEN** the verbatim message shows as the Feed-name field's own error (no generic banner),
  and the form stays populated

## SRCP-13 — 422 on url routes inline + red border
- **GIVEN** the POST returns 422 with a `url: '<value>' is not a safe http(s) URL (...)` detail
- **WHEN** the response is handled
- **THEN** the verbatim message shows as the Feed-URL field's own error AND that TextField gets
  `invalid`/`aria-invalid="true"` (the red-border treatment)

## SRCP-14 — Generic (non-field) failure
- **GIVEN** the POST fails with a non-422 status (500) or the fetch throws
- **WHEN** the response is handled
- **THEN** a generic "Could not add — try again." banner shows (reusing `.intel-wlc-add-err`)
  and both fields keep their typed values

## SRCP-15 — 20-feed cap reached
- **GIVEN** the current user-row count is already 20 (`MAX_USER_SOURCES`)
- **WHEN** the Add-feed section renders
- **THEN** the label/URL fields and Add button are replaced entirely by the muted note "You've
  reached the limit of 20 feeds — remove one to add another."

## SRCP-16 — Delete confirm flow
- **GIVEN** a user-provenance row
- **WHEN** the user clicks its delete icon (title "Remove this feed")
- **THEN** a `ConfirmDialog` opens with title `Delete "{label}"?`, body "Lucius stops fetching
  this feed immediately. This can't be undone — you'll need to re-add the URL to bring it back.",
  and confirm label "Delete feed"
- **AND** confirming fires `DELETE /api/intel/sources/{id}`; on success the row disappears from
  the table immediately; on failure the "Could not update — try again." banner shows and the row
  stays

## SRCP-17 — Builtins never show a delete affordance
- **GIVEN** a `provenance=builtin` row
- **WHEN** it renders in either section
- **THEN** its actions cell is empty — no delete icon, no way to remove it (disable it instead)

## SRCP-18 — Mobile: horizontal scroll, no reflow break
- **GIVEN** a narrow viewport (≤640px, mirroring the modal's existing responsive breakpoint)
- **WHEN** the sources table is wider than the viewport
- **THEN** `.intel-src-table-scroll` (`overflow-x: auto`) lets the table scroll horizontally
  inside its own container — the page/modal itself never scrolls sideways, and no column wraps
  or clips

## SRCP-19 — Copy-rule compliance
- **GIVEN** every control on the panel (switches, delete icon, cap note, error banners, empty/
  loading/error states)
- **WHEN** its exact text is read
- **THEN** it matches the spec's binding copy verbatim (see `NewsSourceRow.tsx`,
  `AddNewsSourceForm.tsx`, `NewsSourcesPanel.tsx`) — every control states WHO decides (Lucius)
  and WHAT happens, no "optional"/"lane" jargon, no second page heading, no raw px/hex outside
  the two new CSS rules

## Verification status
- Backend: unchanged in this lane (INT9 Phase B's `backend/api/news_sources.py` /
  `services/news_sources.py` / `db/news_source_store.py` already shipped and tested);
  `lint-imports` 5/5 kept, full `pytest -m "not integration"` green (unchanged from Phase B).
- Frontend (vitest): `NewsSourceRow.test.tsx` (24 tests — `classifySourceHealth`/`formatLag` full
  branch coverage + row interaction), `AddNewsSourceForm.test.tsx` (10 tests —
  `sourceFieldError` + all 4 panel-adjacent states: happy path, 422 label, 422 url+red border,
  generic failure, cap), `NewsSourcesPanel.test.tsx` (9 tests — loading/error+retry/empty, two
  sections+counts, toggle optimistic+revert, cadence PATCH, delete confirm flow, builtin-no-
  delete), `MonitorsWorkspace.test.tsx` unaffected. Full suite: 2100/2100 passing. `tsc` clean
  (40 pre-existing baseline errors, identical set before/after, none in touched files),
  `lint:classes` clean, `npm run build` clean.
- `make ui-gate` / `make qa-e2e` / screenshots: see the task's final report for what could and
  could not be executed in this environment (no ANTHROPIC_API_KEY for the LLM-graded browser-use
  run; structural validation only).
