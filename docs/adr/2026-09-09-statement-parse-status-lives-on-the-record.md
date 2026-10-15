---
title: Statement parse status lives on the record — no generic job framework, no cancellation
status: Accepted
date: 2026-09-09
deciders: implementing agent (lane/B20-import-progress) + repo owner
---

# 2026-09-09. Statement parse status lives on the record — no generic job framework, no cancellation

## Context

B20 phase 1 gave the statement-preview modal LIVE progress: `services.statements.parse_progress`
is an in-process, bounded, TTL-swept registry (mirroring `mcp.probe_progress`) bound as a
ContextVar, polled via `GET /api/statements/preview-progress/{client_token}` while the preview
POST is still in flight. That registry is ephemeral by design — a process restart, or the
browser closing the tab, loses it entirely.

Phase 2 needed the parse to survive both: the owner wants to close the modal ("Continue in
background") and come back later, and a server restart mid-parse must not leave a permanently
ambiguous state. Two shapes were on the table:

1. A generic **job/task table** — id, kind, status, payload, worker claim — that any
   long-running server operation could ride, with statement-parse as its first tenant.
2. **Statement-specific status on the `statements_meta` row itself** — the parse-progress
   registry's own snapshot, persisted onto the record it already describes, polled by the
   record's own id.

`statements_meta.status` already existed, reserved: `tables.py`'s own comment on it (written at
the table's birth) reads *"kept as a column because the brief asks for it, reserved for a
derived/summary value a future store rewrite computes — nullable, unpopulated by this
revision."* Nothing else on this table, or in the codebase, has ever populated it.

## Decision

We use shape 2, and reuse the existing `status` column rather than adding a parallel one.

- `statements_meta.status` ∈ `parsing` | `ready` | `failed` | `interrupted` | `NULL`
  (legacy/pre-B20 — treated as `ready` when `has_parsed_rows`, else unknown). No CheckConstraint,
  matching `import_batches.status`'s own precedent immediately above it in `tables.py` (a
  comment-documented enum, no CHECK) — a NULL here is meaningful, not a violation.
- `statements_meta.parse_progress` (new column, migration `202609091200`) holds the registry's
  own snapshot dict verbatim — `{stages, started_at, elapsed_s, done, error}`, the exact shape a
  live poll already returns — so a browser reopening a statement by id, or the boot sweep,
  reads the identical payload.
- `parse_progress.bind_statement(statement_id, on_update=...)` — called unconditionally inside
  `decisions.decision_preview`, sync or async — attaches a persistence hook that calls
  `StatementStore.set_parse_status` at every stage boundary and on done/error. A handful of
  small writes per parse; the durable copy is never more than one stage behind the live one.
  `GET /api/statements/{id}/parse-status` reads the in-memory registry first (this process, this
  parse, right now) and falls back to the record's own persisted copy — so it answers correctly
  whether the parse is live, finished moments ago, or survived a restart.
- Async scheduling (`POST /api/statements/preview` with `mode=async`, and the same on the
  reopen-by-id route) is a fixed 2-worker `ThreadPoolExecutor`
  (`services.statements.parse_jobs`) — no queue table, no worker-claim protocol. A statement
  found already `parsing` (by content hash) attaches to the existing record instead of
  scheduling a second job; a boot sweep flips any row still `parsing` at startup to
  `interrupted` (the in-process registry that would have updated it is gone with the old
  process, so it can never finish on its own).
- **No cancellation.** A parse that's running, runs to completion (or failure) — "Continue in
  background" only stops the client from watching, never the server from working. Out of scope
  by owner decision, not a deferred TODO.

## Consequences

### Positive

- Zero new tables, zero new store class, one column added. The 4-state enum lives exactly where
  every other reader of a statement record already looks.
- `parse_progress` (JSON) is schema-free enough that phase 1's registry shape and phase 2's
  persisted shape can never drift into two different "what does progress look like" answers —
  there is only one shape, defined once, in `parse_progress.py`.
- Reopening a statement by id, resuming after a restart, and a live poll all answer through the
  SAME `status_for()`/snapshot logic — no second interpretation of "done" to keep in sync.

### Negative

- `status`'s pre-existing (unused) reservation is now spent on this one purpose — a future
  "derived/summary value" the original brief may have had in mind would need its own column.
  Judged acceptable: nothing else ever claimed it in the ~2 months since, and the comment
  explicitly invited exactly this kind of rewrite.
- No cancellation means a parse against a huge/pathological file runs to its natural
  cost/timeout ceiling even if the owner no longer wants it — bounded by the existing
  `_VISION_PAGE_CAP`/per-call token caps, not by this feature.
- The persistence hook's DB write happens synchronously inside the parse thread (not
  fire-and-forget) — a slow write briefly delays the next stage transition. Accepted: a
  `statements_meta` row update is small and local: this is not a page-image or LLM-call cost.

### Follow-ups

- None planned. A cancellation affordance, if ever requested, would need its own decision (what
  "cancelled" means for a call already sent to the model, whether the partial parse is kept).

## Alternatives considered

- **Generic job table** (id/kind/status/payload/worker-claim) — rejected: exactly one job KIND
  exists today (a statement parse), it already has a natural home (the statement record), and a
  generic table buys polymorphism nothing here needs at the cost of a second status vocabulary
  to keep synchronized with the registry's own.
- **A new `parse_status` column instead of reusing `status`** — rejected: `status` was reserved
  for precisely this ("a derived/summary value a future store rewrite computes"); adding a
  second status-shaped column beside an unused reserved one would be redundant from day one.
- **Cancellation via a `cancel_requested` flag the worker polls** — rejected for this revision
  by owner decision: no user-visible use case yet, and the polling/interrupt-window design isn't
  free (where does a vision call mid-flight actually stop).

## Agent provenance

- **Decided by:** implementing agent, lane/B20-import-progress
- **Lane / branch:** `lane/B20-import-progress`
- **Alternatives weighed:** generic job/task table · a new dedicated `parse_status` column ·
  worker-side cancellation
- **Driving artifacts:** `backend/db/tables.py`'s own `statements_meta.status` reservation
  comment; `backend/services/statements/parse_progress.py` (phase 1); owner's phase-2 approval
  ("build it... statement-specific job state on the statement record, polling, no generic job
  framework, no cancellation")
- **Why this way (one line):** the table already reserved a column for exactly this, so the
  async lifecycle is one more nullable column and a persistence hook on the registry that
  already existed — not a second subsystem.

## Amendment (2026-09-09, security review — before merge, same lane)

One correction to the mechanism described above, caught before this lane merged (so the
migration itself — `202609091200` — was rewritten in place rather than superseded by a second
revision; nothing shipped under the original shape):

**`parse_progress` does NOT live on `statements_meta` any more — it moved to a new narrow side
table, `statement_parse_progress` (`statement_id` PK/FK `ON DELETE CASCADE`, `progress` JSON,
`updated_at`).** The persistence hook fires roughly once per stage/page — ~10 times for a
typical multi-page scan — and `StatementStore.update_by_id` is a full
read/reinline/re-extract-parsed-rows/write cycle on `statements_meta`'s own row; paying that
ten times per parse for a progress blob nothing else on the row reads was the cost this
amendment removes. `statements_meta.status` still carries the 4-state enum exactly as decided
above, but now changes via a narrow single-column UPDATE, fired only on an actual TRANSITION
(most calls during one parse re-stamp the SAME `parsing` status as later pages report in, and
those touch only the side table). `StatementStore.find_by_id`/`all()` transparently merge the
side table's row back onto the returned dict under the same `parse_progress` key, so every
caller above the store — the API layer, the tests — sees the identical shape this ADR's
original text described; only the storage mechanics changed.

`statement_parse_progress` is checkpoint-EXCLUDED (`ai.checkpoint._EXCLUDED_TABLES`) and absent
from `db.portable_bridge.BRIDGED`, mirroring `price_points`/`evidence`/`asset_brief`'s own
"derived, rebuildable, never snapshotted" treatment — a rewind must not resurrect stale
read-progress for a parse that has since moved on, and there is no export/import role for an
ephemeral cache.

The boot sweep (`sweep_interrupted_parses`) also gained a documented single-worker assumption
and a guard for the multi-worker case this ADR's own "no cancellation" scope never
considered: under `--workers N`, every worker's boot runs the sweep, and a row genuinely being
read by a sibling worker that never restarted looks identical to an orphan from THIS worker's
own prior life. A row whose progress was updated after `boot_time - grace_seconds` (default 5s)
is left alone rather than flipped to `interrupted`.

## Amendment (2026-09-09, live QA — before merge, same lane)

The grace-window guard the amendment above describes (a row updated after `boot_time -
grace_seconds` left alone) is REMOVED. It was multi-worker insurance for a mode the shipped
server does not run in — the Makefile's uvicorn has no `--workers` flag — and its actual,
measured effect on the single-worker server this ships as was to leave a genuinely-dead
`parsing` row (killed mid-parse, then the process restarted) stuck `parsing` forever: the sweep
was that row's ONLY path out, and the guard made it skip the row precisely because it had been
updated recently — which is true of EVERY real orphan, not just a hypothetical live sibling.
`sweep_interrupted_parses()` now takes no arguments and flips every `parsing` row
unconditionally, once, at boot. An operator-chosen `--workers N` would need each worker to own
(and sweep) only its own rows via a per-worker identity — out of scope until multi-worker is
actually supported, same as this ADR's original "no cancellation" scope.

The attach gates (`_async_accepted` and the three sync upload/reopen/one-shot-commit gates in
`import_api.py`) also stopped trusting the DB `status` column alone to mean "a job is running
for this id" — they now check `parse_jobs.is_running(id)`, the actual in-process bookkeeping. A
stale `parsing` row with no running job (the exact shape the sweep above exists to fix, caught
between a crash and the next boot) self-heals to `interrupted` and the gate schedules/runs a
fresh parse with the caller's own arguments (`force_reparse` included), instead of returning a
202 that attaches to a job that doesn't exist and never resolves.
