---
title: Portable export/import bridges DB-backed reference-data stores (price_history, instruments) under their original filenames; evidence.json's bridge is wired but DORMANT; a bridged store fires no write-hook
status: Accepted
date: 2026-08-07
deciders: Claude (implementer agent, storage plan 2 task 7), ratifying spec 2026-08-05's D6 and the Task 3 review adjudication; evidence-dormancy ruling by the task 7 review controller (fix round 1) under the owner's resolve-everything directive — flagged for owner sign-off in the program's final report
---

# Portable export/import bridges DB-backed reference-data stores; evidence.json stays non-portable (dormant bridge); a bridged store fires no write-hook

## Context

`backend/core/portable.py::PORTABLE_FILES` is the single manifest `services/portability.py`
(export/import) and `/api/reset` both read. Plan 2's per-store cutover (tasks 1/3/5) moved
`evidence`, `price_history` and `instruments` off their JSON files onto real tables, and — per
the per-store recipe in `docs/superpowers/specs/2026-08-05-storage-engine-sqlite-postgres-design.md`
(D6, step 5) — renamed each source file to `<name>.json.migrated-<ts>`, never deleting it. The
manifest still names `price_history.json`/`instruments.json` by that original filename.

That created a real, present-tense gap, found and explicitly deferred by two prior tasks in this
plan rather than fixed in place (each task's brief scoped `portable.py` as out of bounds):

- **Task 3's review** (price_history cutover): `services.portability._export_bytes_for` does
  `path.read_bytes()` after checking `path.exists()`. Post-cutover, `price_history.json` no
  longer exists, so export silently **omits** it — no error, no warning, just a bundle with one
  fewer entry than before. Import is worse: an absent-from-bundle file resets via
  `portable.empty_contents_for(path.name)`, but a bundle that DOES carry `price_history.json`
  (e.g. one exported before this instance's migration ran) gets `atomic_write_text`'d into the
  dead path — a **false-success restore**: the API reports the import as successful, the file
  it wrote is never read by anything, and the DB (the thing the app actually reads) is
  untouched.
- **Task 5** (instruments cutover) hit the identical shape twice: the same export/import gap on
  `instruments.json`, plus a sibling on the write side — `POST /api/reset` iterates the same
  manifest and `path.write_text(...)`s each entry to empty; for `instruments.json` this writes an
  empty list into a file nothing reads, so **reset no longer clears the `instruments` table**
  (verified directly: seed one instrument, hit `/api/reset`, `InstrumentStore().count()` stays
  `1`).

Both tasks' briefs named `portable.py` as explicitly out of scope and deferred the fix here,
citing the design spec's own words (D6): *"The exporter in step 4 is not throwaway. It becomes
`portable.py`'s new implementation — today 91 hardcoded file constants, tomorrow a query. One
piece of code then serves migration verification, the portability promise, and the rollback
path."* That full rewrite — every one of the ~40 stores Plans 2-4 migrate, replacing the
manifest's file-constant list with something query-driven — is real, cross-cutting work,
correctly out of one task's blast radius. This task is the **narrow bridge** the spec's phrase
already implies is coming: reuse each store's cutover-verified `export_*`/`import_*` (built once,
for migration-verification, in `evidence_io.py`/`price_history_io.py`/`instruments_io.py`) as the
portability seam too, for exactly the stores migrated so far — not a rewrite of the manifest
itself.

`evidence.json` was **never** in `PORTABLE_FILES` (Task 3's review confirmed this explicitly: *"
`evidence` (Task 1) never faced this because it was never in `PORTABLE_FILES` to begin with"*) —
it is a checkpoint-excluded, instance-local, rebuildable cache (`evidence_store.py`: *"a derived
cache rebuildable from its sources"*), not user money data. So bridging it is not closing a
regression the way price_history/instruments are — including it would be a **deliberate scope
decision**, not a bug fix.

This task's first implementation made that decision and added `evidence.json` to
`PORTABLE_FILES` (reasoning preserved verbatim in Alternatives Considered below, as honest
history). The **task 7 review reverted it (fix round 1, Critical, reproduced)**: a real evidence
export measures ~108.4MB (the design spec's own Problem-section figure), which exceeds
`services.portability._MAX_ENTRY_UNCOMPRESSED` — a pre-existing 100MB per-entry zip-bomb-guard
cap that `build_export_bundle` never checks when WRITING, but `inspect_bundle`/`apply_import`
DOES check when READING. The result is a structurally broken round trip, not a hypothetical one:
export succeeds, and every subsequent import of that exact bundle — including importing the
export you just made — 422s with "entry too large." The reviewer reproduced this directly against
`inspect_bundle`. See Decision below for the ruling this produced, and Alternatives Considered for
the full weighing (the original inclusion reasoning stays intact there — it just lost).

## Decision

We will add `backend/db/portable_bridge.py`, a `db`-tier registry:

```python
class Bridge(NamedTuple):
    export_fn: Callable[[Connection], Any]
    import_fn: Callable[[Connection, Any], int]

BRIDGED: dict[str, Bridge] = {
    "evidence.json": Bridge(evidence_io.export_evidence, evidence_io.import_evidence_json),
    "price_history.json": Bridge(price_history_io.export_price_history, price_history_io.import_price_history),
    "instruments.json": Bridge(instruments_io.export_instruments, instruments_io.import_instruments),
}
```

plus `wipe(conn, filename)` / `wipe_all(conn)` — raw `DELETE`s over each store's own table(s),
separate from `Bridge` because `/api/reset` needs to wipe every bridged store unconditionally,
not just the ones a particular bundle happens to carry.

`core.portable.PORTABLE_FILES` is **unchanged by this task** — `PRICE_HISTORY_JSON`/
`INSTRUMENTS_JSON` were already there pre-cutover, so no addition was needed to make them LIVE
bridge targets; `EVIDENCE_JSON` does **not** join it (the dormancy ruling below). Either way,
`core/portable.py` stays a plain list of `Path` constants — no import of `portable_bridge` or
anything `db`-tier, ever, for any store. `core/portable.py` stays a leaf; the bridging decision
lives entirely in `services.portability`, which now does `from backend.db import
portable_bridge` (a normal downward services → db edge).

**Evidence stays non-portable — the cap collision (controller ruling, task 7 review fix round 1,
2026-08-07):** `BRIDGED["evidence.json"]` stays in the registry — wired, fully functional,
round-trip-tested directly against it (`test_dormant_evidence_bridge_round_trips_via_registry_
directly`) — but `EVIDENCE_JSON` is not added to `PORTABLE_FILES`, so `build_export_bundle`/
`apply_import` never reach it. Reasoning, per the controller's ruling:
1. **The cap collision is a real, reproduced bug, not a theoretical concern.** `services.
   portability._MAX_ENTRY_UNCOMPRESSED` caps any single bundle entry at 100MB (a pre-existing
   zip-bomb guard, task-7-unrelated). A real evidence export measures ~108.4MB. Export doesn't
   check this cap (it only guards decompression on the way IN), so `build_export_bundle` would
   happily produce an over-cap bundle; `inspect_bundle`/`apply_import` DOES check it, so importing
   that same bundle back — the most basic "does my backup work" test a user could run — 422s
   every time. Reproduced directly by the reviewer against `inspect_bundle`.
2. **Evidence is a rebuildable derived cache**, by its own store's docstring — not user money
   data, and the ONE store among the three where "just don't back it up" costs nothing but a
   re-scan.
3. **~30x bundle bloat** for data that regenerates from a re-scan is a bad trade for every
   self-hoster's export size, not just the rare instance that happens to graze the cap.
4. **Plan-1 precedent**: evidence was already deliberately excluded from checkpoints
   (`ai/checkpoint.py`) on the same "derived, rebuildable, not user data" reasoning this ruling
   now extends to portability.

`/api/reset` still wipes evidence's tables regardless of this ruling (see the `/api/reset`
subsection below) — a blank-slate reset and a travel-with-you export bundle are different
questions, and only the latter has a byte-size cap to collide with.

**Export** (`services.portability.export_bytes_for`, the renamed `_export_bytes_for` — now also
reused by `/api/reset`'s backup step): a bridged filename serializes
`json.dumps(bridge.export_fn(conn), ensure_ascii=False, indent=2).encode("utf-8")` — the *exact*
convention `db/json_store.py::JsonStore._save_raw` uses, so a bridged entry is byte-compatible
with one a pre-cutover `JsonStore` would have written. A bridged export that raises is **not**
caught — it propagates out of `build_export_bundle` and aborts the whole export, the same "loud
or nothing" bar every other failure mode in this bundle format already holds to.

**Import** (`services.portability.apply_import` → `_apply_bridged_import`): for a bridged
filename, one `unit_of_work()` transaction wipes that store's table(s) first, then — if the
bundle actually carries an entry for it — parses the bytes and calls `import_fn`; row counts in
the returned report come from `import_fn`'s own return value (e.g. price_history's is the true
point count across every series, not the "top-level dict key count" the generic `_row_count`
heuristic would report for every other dict-rooted file). A bundle that omits a bridged filename
still gets the wipe — matching the existing "absent portable file resets to empty" contract for
every other store (restore is point-in-time replace, never a merge). The dead JSON path is never
read, written, or hooked, for either branch. A raise inside `import_fn` (price_history_io's
strict-ascending check is the concrete case) rolls the wipe back too, via `unit_of_work`'s
commit-on-clean-exit/rollback-on-exception contract — a corrupt entry leaves that store completely
untouched, never partially replaced. Old bundles (built before a store's cutover, plain
JsonStore-shaped bytes) and new bundles (DB-sourced) are handled by the identical code path,
keyed only by filename — that sameness is the whole point of bridging under the original name
instead of minting a new one.

**Checkpoint interplay — a bridged read/write fires NO hook, neither `fire_write_hook` (the
dead path is never touched, so there is nothing to hook) nor `fire_db_write_hook` (the wipe is a
raw `DELETE`, the import is each io module's raw bulk `INSERT` — neither goes through
`PayloadTableStore`/`EvidenceStore`, the only callers that ever invoke that hook).** Concretely:
`instruments`' bridged import writes via `instruments_io.import_instruments` directly, **not**
through `InstrumentStore` — even though `instruments` is normally checkpoint-INCLUDED for an
ordinary AI-tool mutation. The ruling: **a portability restore is an admin operation, not an
AI-turn mutation.** It mirrors today's JSON-file import, which only ever fired the coarse
whole-file hook (never a per-row equivalent — JSON files have no row granularity), extended to its
DB-backed analogue: no hook at all, coarse or per-row, for a full-table wipe+reinsert. The
practical consequence: a bridged store's portion of a bundle import is **not undoable via the
in-app checkpoint/undo button** the way a JSON-file store's portion is (`test_import_replace_is_
undoable`'s guarantee does not extend to price_history/instruments — the two LIVE bridged stores
today — and would not extend to evidence either, should its bridge ever be flipped on). Recovery
for a bad bundle import is re-importing the correct bundle (or restoring from `/api/backup`, which
zips `onw.db` whole and was never subject to this gap in the first place), not the turn-scoped
checkpoint. This is judged acceptable because none of the three bridged stores are money-critical
(reference/world data, not the ledger), and because firing a per-row hook for tens of thousands of
rows on every restore (evidence alone measured 83,381 rows in production) would make bundle-import
cost scale with row count for an operation nobody undoes through chat in practice.

**`/api/reset`** (`services.portability.reset_bridged_stores`, called from
`api.setup.reset_all_data`): one `unit_of_work()` transaction wipes `evidence`,
`evidence_subjects`, `price_points`, and `instruments` — the exact four tables
`portable_bridge.wipe_all` owns. `write_log`/`json_cutovers` (migration/checkpoint machinery) and
the identity tables (`users`/`books`/`book_members`) are untouched by construction — `wipe_all`
never references them. The manifest-driven per-file reset loop now `continue`s past any filename
in `BRIDGED` (nothing to write — the table wipe already covers it), and the loop iterating
`portable.PORTABLE_FILES` for reset's pre-wipe backup snapshot now calls the same
`export_bytes_for` export uses, instead of `shutil.copy2` — **a second instance of the exact same
bug class, found while implementing this fix**: `shutil.copy2` requires `src.exists()`, which is
always false for a migrated-away file, so the reset backup silently stopped covering
price_history/instruments the moment their migrations ran (harmless before this task, since
nothing wiped those tables either — but this task's wipe extension would have turned that into
real, unrecoverable data loss on the very first `/api/reset` call had the backup gap shipped
alongside it). Fixing both in the same change was necessary, not optional, for the wipe extension
to be safe at all.

## Consequences

### Positive
- `GET /api/export` is honest again: a bundle taken today genuinely contains today's
  price_history/instruments data, not a stale pre-cutover snapshot or a silent omission.
- Restoring an OLD bundle (exported before this instance migrated, or before this task shipped)
  now actually lands its price_history/instruments rows in the DB instead of reporting false
  success while writing an inert file.
- `POST /api/reset` genuinely resets — the exact "reset means reset regardless of backing" bar
  the Task 5 finding named — and that now correctly includes evidence's tables even though
  evidence stays export/import-dormant (reset and portability are different questions).
- The reset-backup gap (found mid-implementation, same root cause) is closed in the same change,
  so the wipe extension above does not ship as a net-new data-loss risk.
- One small, reusable pattern (`Bridge` + `wipe`/`wipe_all`) that Plan 3/4's remaining ~40 stores
  can adopt individually, cheaply, without waiting on the unified rewrite.
- Evidence's dormant-but-wired bridge entry means flipping it on later (once the caps are raised
  and it's proven at real scale) is a **one-line `PORTABLE_FILES` addition**, not a rebuild —
  `export_fn`/`import_fn` are already correct and already tested against the registry directly.

### Negative
- This is deliberately **duplicated, per-store** wiring, not the systemic fix — every future
  migrated store needs its own `BRIDGED` entry (and, if it should be portable, its own
  `PORTABLE_FILES` addition) until Plan 4's unified rewrite lands. Three stores in, that
  duplication is still cheap; it will not stay cheap at forty.
- A bridged store's slice of a bundle import is **not undoable** via the in-app checkpoint/undo
  affordance, unlike every JSON-file store in the same bundle — a real, asymmetric, and
  deliberately-accepted trade-off (see Decision).
- **The first implementation of this task shipped the cap-collision bug** — it added
  `evidence.json` to `PORTABLE_FILES` and tested the bridge only against small, hand-built
  fixtures (a handful of rows), which never exercises the 100MB entry cap a real ~108.4MB
  evidence export collides with. The review caught it before merge, not in production, but the
  gap in the original test strategy — proving the mechanism works without also proving it works
  **at the scale the actual data reaches** — is worth naming plainly rather than glossing over
  now that it's fixed.

### Follow-ups
- Plan 4 (or whichever plan finishes cutting over the remaining ~40 stores) should retire this
  bridge module as each store's manifest entry becomes a real query-driven implementation instead
  of a `Bridge` pair — the unified rewrite D6 already names.
- If a future store's bundle-restore undoability actually matters in practice (unlike the three
  here), reconsider the no-hook ruling for that store specifically rather than blanket-extending
  it — the ruling here is scoped to "reference/world data, not money," not "every DB-backed
  store."
- **Flipping evidence's bridge on is explicitly deferred, not abandoned.** It requires (a) raising
  `_MAX_ENTRY_UNCOMPRESSED`/`_MAX_TOTAL_UNCOMPRESSED` — or moving to a streamed/chunked bundle
  format that doesn't need a single-entry cap at all — and (b) a real-scale test against
  production-sized data (~108MB+), not just small fixtures (see the Negative note above on why
  that specifically was the gap the first implementation had). This whole question — evidence's
  reversal, and the plan to revisit it — is flagged for **owner sign-off** in the program's final
  report per the controller ruling; it is not this ADR's place to pre-decide the owner's answer.

## Alternatives considered

- **Do the full unified `portable.py` rewrite now** (query-driven manifest, no more `PORTABLE_
  FILES` file-constant list) — rejected: the design spec explicitly assigns this to a later,
  unified step (D6) precisely because it touches every one of ~40 stores across Plans 2-4, not
  the three migrated so far; doing it now is a different, much larger task than "make export/
  import honest again for what's already migrated."
- **Include `evidence.json` in `PORTABLE_FILES` now, alongside price_history/instruments — the
  task's FIRST implementation, later reverted (fix round 1, this ADR's own history).** Original
  reasoning, preserved verbatim as honest history rather than quietly dropped: both this task's
  brief and the plan's own framing name `evidence.json` as one of "the three filenames" to
  bridge; the design spec's D6 explicitly intends every migrated store's exporter to become part
  of `portable.py`'s future, evidence included, not a price_history/instruments-only promise; the
  spec's own Tier-2 grouping (`evidence, price_history, instruments, fx_cache, asset_briefs` —
  "world reference data") treats these three as one class, and two of the three were already
  portable; and checkpoint-exclusion and portability are already proven orthogonal in this
  codebase — `price_history.json` is BOTH checkpoint-excluded AND portable, so "evidence is
  checkpoint-excluded" was never by itself a reason it couldn't also be portable. **This reasoning
  shipped, and was wrong to act on without also checking it against real-scale data**: the task 7
  review reproduced a concrete, blocking bug none of the above accounted for — a real evidence
  export (~108.4MB) exceeds `_MAX_ENTRY_UNCOMPRESSED` (100MB), so `build_export_bundle` (which
  never checks this cap on write) would succeed while every subsequent `inspect_bundle`/
  `apply_import` of that same bundle (which DOES check it on read) 422s — a broken round trip.
  Combined with evidence being a rebuildable derived cache, ~30x bundle bloat for regenerable
  news, and the Plan-1 checkpoint-exclusion precedent, the controller ruled evidence back to
  non-portable — dormant `BRIDGED` entry, `PORTABLE_FILES` unchanged — under the owner's
  resolve-everything directive, flagged for owner sign-off in the program's final report (see
  Decision and Follow-ups above). The Tier-2-grouping/orthogonality reasoning above isn't wrong on
  its own terms; it was simply incomplete without a real-scale check, which is exactly why it's
  kept here rather than deleted — a future re-proposal of flipping evidence on inherits this
  reasoning AND the cap-collision fact, not just one or the other.
- **Raise `_MAX_ENTRY_UNCOMPRESSED`/`_MAX_TOTAL_UNCOMPRESSED` now, in this same fix round,
  instead of reverting evidence** — rejected by the controller ruling: those caps are a
  zip-bomb/decompression-abuse guard, not portability plumbing, and raising a security-relevant
  limit as a side effect of a portability fix — with no real-scale test proving the new ceiling is
  actually safe and sufficient (evidence today is ~108MB; tomorrow's could be larger) — is exactly
  the kind of under-verified change this review process exists to catch. Explicitly named as
  future, deliberate work in Follow-ups instead.
- **Fire `fire_db_write_hook` per row during a bridged import**, making it checkpoint/undo-able
  like an ordinary `instruments` mutation — rejected: a full-table wipe+reinsert of tens of
  thousands of rows would mean a `write_log` row per touched row for an admin operation nobody
  undoes through the chat undo button in practice (the real recovery path is re-importing the
  right bundle, or `/api/backup`'s whole-DB-file snapshot); it also mirrors today's JSON-file
  import, which only ever fires the coarse whole-file hook, never a per-row equivalent.
- **Fold a `wipe_fn` into `Bridge` as a third field** — rejected in favor of the two-field shape
  the brief specifies plus a separate `wipe`/`wipe_all` API in the same module: `/api/reset` wipes
  every bridged store unconditionally, independent of any particular bundle's contents, so a
  per-`Bridge` wipe function would either go unused by reset or be awkwardly reused outside the
  "restoring this filename" context it would imply.
- **Let a bridged filename's row_count in the import report come from the existing generic
  `_row_count` heuristic** (parse JSON, count list length or top-level dict keys) — rejected for
  `price_history.json` specifically: its root is `{price_source_id: [points...]}`, so the generic
  heuristic would report "number of instruments with history," not "number of price points
  restored." Reporting `import_fn`'s own return value instead gives the true row count for every
  bridged store, including this one.

## Agent provenance

- **Decided by:** Claude (implementer agent), storage plan 2 task 7, on `lane/storage-plan1`
  (worktree `storage-plan1`) — first implementation (commit `5ebbb123`); the evidence-dormancy
  reversal in this same file is a **controller ruling** from the task 7 review (fix round 1,
  2026-08-07), made under the owner's resolve-everything directive and implemented by the same
  agent. Recorded in the review ledger; **flagged for owner sign-off in the program's final
  report** — this ADR documents the ruling as currently implemented, not a closed question the
  owner has personally reviewed yet.
- **Alternatives weighed:** see above — the unified-rewrite-now and evidence-inclusion framings
  were the two live candidates in the first implementation (both explicitly raised by the task
  brief's own framing of "narrow bridge now, unified later" and by Task 3's review flagging
  evidence as never having been portable); the hook-firing and `Bridge` shape questions were
  resolved during that same implementation. The evidence-inclusion choice was then **reversed** by
  the review, on a fact the first implementation's own test suite never checked: real-scale entry
  size against the actual import cap.
- **Driving artifacts:** `docs/superpowers/specs/2026-08-05-storage-engine-sqlite-postgres-design.md`
  (D6, the Tier-2 reference-data grouping, the Problem section's evidence.json size measurement);
  `.superpowers/sdd/2026-08-07-storage-plan2-reference-data-and-write-log/task-3-report.md`'s
  Self-review section (the gap's first discovery, and the explicit "evidence was never portable"
  note); `task-5-report.md`'s "`/api/reset` gap (found, documented, deliberately NOT fixed)"
  section (the reset half of the same root cause); `task-7-brief.md`'s binding requirements; the
  task 7 review verdict (fix round 1: 1 Critical reproduced against `inspect_bundle` + 2
  Important, including this reset-backup regression-test ask);
  `tests/db/test_portability_db_bridge.py` (14 tests post-fix-round-1: the real-manifest check
  that price_history/instruments are portable and evidence is NOT, the registry-shape check, a
  direct-registry round-trip proving the dormant evidence bridge still works, DB-sourced export
  under original filenames with a byte-format assertion — evidence excluded even though seeded
  with real rows, loud export failure, a bundle carrying an evidence.json entry rejected as
  unknown, old-bundle restore with io-module-accurate row counts, absent-file-wipes-to-empty,
  atomic rollback on a corrupt series, no-hook proof, export→import round trip proving dormancy
  ≠ deletion, reset wipes all four tables — evidence included — while leaving identity/
  migration-ledger tables untouched, one API-level `POST /api/reset` proof, and the reset-backup
  regression test with real pre-reset rows parsed out of the actual backup directory).
- **Why this way (one line):** reuse each migrated store's already-verified `export_*`/`import_*`
  under its original filename so export/import/reset are honest again for exactly what's
  migrated so far, without pulling the larger unified `portable.py` rewrite into this task's
  scope — and don't ship a store past a hard size cap it fails on the very next import just
  because the reasoning for including it sounded right at small scale.
