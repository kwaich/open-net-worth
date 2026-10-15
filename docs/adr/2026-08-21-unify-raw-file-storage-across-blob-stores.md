---
title: Unify raw-file storage across all four blob stores, under one shared directory
status: Accepted
date: 2026-08-21
deciders: repo owner + implementing agent
---

# Unify raw-file storage across all four blob stores, under one shared directory

## Context

This app has four file-backed blob stores, each pairing a DB metadata row with the
original file's bytes on disk: `artifacts` (chat-rendered HTML/SVG), `statements`
(imported bank statement files), `receipts` (transaction evidence), and `attachments`
(generic chat uploads). The owner discovered — by literally listing
`~/.open-net-worth/data/*/` — that these four stores do NOT follow one consistent
convention, despite solving the identical problem:

- **`artifacts`** (`ARTIFACTS_DIR/<id>.html` / `<id>.svg`) — a real file, a real
  extension. Correct from day one.
- **`statements`** (`STATEMENTS_FILES_DIR/<id><ext>`, `ext` derived by
  `statement_store.py`'s own `_file_ext(filename, mime)` — a small, fixed allowlist of
  known extensions, filename-suffix match first, then a mime-type map, `.bin` fallback
  only for a genuinely unknown type; NEVER trusts the raw filename as a path component) —
  also correct, and the exact pattern the other two needed.
- **`receipts`** (`RECEIPTS_DIR/<id>.bin`) — a real file, but ALWAYS `.bin` regardless of
  actual type. The owner's own words: "we should store the file as is, for all files... I
  don't want us to store raw bytes at all, we should store the file as is."
- **`attachments`** (`ATTACHMENTS_DIR/<id>.json`) — the real problem. Every chat upload
  was a JSON file with the raw bytes base64-encoded and buried INSIDE it, mixed together
  with derived/parsed data (extracted text, page-render images, format). This is directly
  why a non-image chat attachment (a PDF, a spreadsheet) could never be downloaded again —
  only images had their bytes reachable in a servable shape
  (`GET /api/uploads/{ref_id}/image`) — and directly upstream of a scanned-PDF-promoted-
  as-a-receipt bug fixed separately the same day: bytes buried inside a parsed-cache shape
  instead of being a first-class file.

Separately, all four stores' directories sat as flat siblings of the SQLite database file,
backups, and logs directly under `DATA_DIR` — no shared parent naming them as "this app's
raw file storage" as a group, even though they are exactly that.

## Decision

**One consistent pattern across all four stores**: the file on disk IS the real file (real
bytes, real extension), addressed by a server-minted id — never a user-supplied filename
used as a path component (a hard security invariant, satisfied via a small, SAFE,
allowlisted extension derivation, not a generic `.bin`/`.json` catch-all). Local disk only
— explicitly not S3, not now (the owner's own words: "we don't need to do s3, right now,
just store local disk").

1. **Shared extension helper** (`backend/core/file_ext.py`): `statement_store.py`'s
   `_file_ext` extracted into a stdlib-only leaf (`core/` may only import stdlib +
   `backend.paths`, per CLAUDE.md's dependency rule), expanded to cover every kind
   `backend/services/attachment_parse.py` produces (plain text, the structured
   bank-statement formats — OFX/QFX/QBO/QIF/MT940/STA/CAMT — and a broader set of real
   image extensions, since an `image/*` upload is accepted for ANY sub-mime, not just the
   original four raster types). `statement_store.py` now imports the shared version —
   behavior-preserving for every input it already handled.

2. **`receipts`**: `ReceiptStore.blob_path()`/`create()`/`read_bytes()`/`delete_receipt()`
   derive the real extension from the row's own `filename`/`media_type` via the shared
   helper, instead of hardcoding `.bin`.

3. **`attachments`** (the substantial change): the raw bytes move OUT of the `<id>.json`
   payload entirely, onto `ATTACHMENTS_DIR/<id><ext>` as a real file. The JSON payload
   keeps only DERIVED fields the chat/tool layer needs without re-parsing — `kind`,
   `summary`, `format`, extracted `text`, a `pdf_pages` entry's rendered page images (a
   genuine derived render, not a duplicate of the original bytes). `AttachmentStore.
   read_payload()` reconstructs the EXACT pre-2026-08-21 entry shape (`data`/`content`
   re-derived from the raw file at read time), so every existing consumer —
   `ai/attachments.py`'s dispatch-time resolution, `services.receipts.
   _receipt_bytes_from_entry` (receipt promotion), `services.attachments.
   get_attachment_import_source` (re-import from an attachment), `ai/tools/_vision_read.py`
   (on-demand reading) — keeps working with its existing calling contract, unchanged.
   `GET /api/uploads/{ref_id}/file` (new) serves any attachment's raw bytes back — closing
   the real UX gap: a non-image chat attachment finally has a servable-shape download
   route, using the same `inline_blob_headers` allowlist gate the sibling receipt/image
   routes already use (a caller-declared media type that isn't on the safe-to-render list
   is forced to download as `application/octet-stream`, never rendered inline).

4. **One shared parent directory** (mid-implementation addition, same underlying
   motivation): `FILES_DIR = DATA_DIR / "files"`, with `ARTIFACTS_DIR`/`ATTACHMENTS_DIR`/
   `RECEIPTS_DIR`/`STATEMENTS_FILES_DIR` becoming `FILES_DIR / "artifacts"` /
   `"attachments"` / `"receipts"` / `"statements"` — one discoverable answer to "where does
   this app's file storage live", instead of four flat siblings of the DB file/backups/
   logs. Every constant name is unchanged (only what it points to moves), so every other
   call site that imports them from `backend.core.paths` keeps working unmodified.
   `STATEMENTS_PARSED_DIR` deliberately stays OUTSIDE `files/` — it holds PARSED/DERIVED
   statement rows (extracted transaction data), not an original uploaded file, so it isn't
   one of "this store's raw files" in the sense `files/` groups; it remains a sibling of
   `files/` directly under `DATA_DIR`.

### Migration

Both the directory move and the two stores' content-shape changes are ONE Alembic
revision (`0045_files_dir_and_raw_blob_storage`), not a standalone `backend/scripts/`
tool — this repo's own established precedent for exactly this shape of change: revision
0041 (`uploads/` → `attachments/`, the `UploadStore`→`AttachmentStore` rename) already
merged a blob directory inside a normal boot migration. Running it as an Alembic revision
means it inherits `db.migrate.ensure_migrated()`'s existing fail-closed, verified-backup-
first boot gate for free — the owner's next normal app boot runs it automatically,
instead of a separate script the owner would have to remember to invoke by hand.

Non-destructive throughout, matching this repo's established Wave-5 convention ("never
delete, rename/leave a trail once the new shape is confirmed correct"):

- The directory move is a MERGE (mirrors 0041's own `_move_blob_dir`), never a bare
  rename gated on the destination being absent — `backend.core.paths` unconditionally
  `mkdir(exist_ok=True)`s every destination at import time, and that module sits on this
  migration's own import path, so the destination always already exists (empty) by the
  time `upgrade()` runs. Merging file-by-file and never clobbering a name already present
  at the destination is safe regardless of which side got created first.
- A receipt's `<id>.bin` is COPIED (not moved) to `<id><ext>`; the original `.bin` is left
  in place, unreferenced but never deleted — once `ReceiptStore` itself is
  extension-aware, a leftover `.bin` is simply orphaned, which this repo does not
  aggressively garbage-collect.
- An attachment's `<id>.json` is backed up to `<id>.json.pre-raw-file-migration-<ts>`
  (mirrors Wave 5F's `*.pre-*.json` one-off-script safety-copy convention) BEFORE being
  overwritten with the trimmed shape; the extracted raw bytes are verified byte-identical
  (sha256) against a fresh read of the newly-written file before the row is considered
  migrated. A verification failure raises and fails the whole migration — safe to re-drive,
  since nothing was ever destroyed.

### A drive-by robustness fix found along the way

Implementing this surfaced a real, independent bug: `ReceiptStore.create`/
`AttachmentStore.create`/`StatementStore.create`/`ArtifactStore.create` wrote their raw
file via a bare `Path.write_bytes()`/`atomic_write_text()`-adjacent call that assumes the
target directory already exists — true only because `backend.core.paths` mkdir's it once,
at import time. A caller that wipes and recreates `DATA_DIR` in-process after that (e.g.
the eval harness's `bootstrap.reset_data_dir()`, `backend/evals/tool_surface/bootstrap.py`)
left the directory permanently gone for the rest of that process's life — every later
receipt/attachment/statement/artifact write failed with `FileNotFoundError`. Every one of
these four `create()` methods now writes through `core.io.atomic_write_bytes`/
`atomic_write_text`, both of which self-heal via their own `path.parent.mkdir(parents=True,
exist_ok=True)` — the same convention every other file-backed store in this codebase
already relied on for exactly this reason. Found via a real, reproducible test-suite
cross-file interaction (`tests/evals/test_tool_surface_harness.py` running before
`tests/test_vision_fallback_chat.py` in the same process), not by inspection — fixed in
this change since it directly blocked verifying the storage-shape change itself, rather
than filed as a separate follow-up.

## Consequences

### Positive

- One pattern, one mental model, for "where do this app's files live and how are they
  named" — a new blob store built after this ADR has an obvious precedent to follow.
- A non-image chat attachment is downloadable again, closing a real UX gap.
- A receipt or attachment's file extension now matches its real content, instead of a
  generic `.bin`/being buried in a JSON blob — usable directly from a file manager, an
  antivirus scan, an `xdg-open`, etc. if the owner ever inspects `DATA_DIR` by hand (the
  exact way this inconsistency was originally found).
- `attachments`' on-disk payload shrinks (no more base64's ~33% size tax duplicated
  alongside a real file) and its metadata row's `size_bytes` now reports the true
  original-artifact size (previously the base64+JSON-inflated wrapper size) — matching
  `ReceiptStore`'s own convention.

### Negative

- `AttachmentStore.create()`'s signature gained a `content: bytes | None` parameter
  (optional, falling back to deriving it from `entry`'s own `content`/`data` keys for a
  caller that only has a parsed entry in hand) — a real, if narrow, API surface change.
- The migration touches every existing receipt and attachment file on a live install; a
  bug in it risks the one thing this ADR explicitly forbids (destroying original bytes) —
  mitigated by never deleting/overwriting-without-a-backup and by verifying every
  extraction byte-identical before considering a row done.
- Two copies of a receipt's bytes exist on disk after migration (`<id>.bin` and
  `<id><ext>`) until an operator manually cleans up the orphaned `.bin` — accepted, per
  this repo's "does not aggressively garbage-collect superseded files" convention.

## Alternatives considered

- **S3 / object storage** — rejected outright by the owner: "we don't need to do s3, right
  now, just store local disk."
- **Keep `attachments`' JSON-wrapped shape, add a decode-on-demand download route** —
  rejected: it would perpetuate the actual root cause (raw bytes not being a first-class
  file) and leave the receipts `.bin` inconsistency and the four-flat-siblings directory
  layout untouched; the owner asked for the underlying pattern to be fixed, not routed
  around.
- **A standalone `backend/scripts/` migration tool instead of an Alembic revision** — the
  original brief suggested this as one option; rejected in favor of Alembic once
  revision 0041's own precedent (a blob-directory merge inside a normal schema-adjacent
  migration) was found — it gives the migration the fail-closed, verified-backup boot
  gate for free instead of relying on the owner remembering to run a separate tool
  correctly.
- **Move `STATEMENTS_PARSED_DIR` under `files/` too** — rejected: it holds derived/parsed
  data (extracted transaction rows), not an original uploaded file — a different category
  from what `files/` groups, so folding it in would blur the directory's own meaning.

## Amendment (2026-08-21 — security review, same day)

A security review of this migration before it could reach a real boot found four real bugs,
verified against real repros; all four are fixed in this same revision (0045), not a
follow-up:

1. **Statements/artifacts orphaned by their own stored `file_path`.** The directory-move
   step (decision point 4) relocated the BYTES for every store, but nothing rewrote the
   `file_path` reference `statements_meta`/`artifacts` rows carry — unlike receipts/
   attachments, these two stores already wrote "the right shape" pre-migration, so the
   migration's original scope (receipt extensions, attachment payload trim) never touched
   them at all. Every pre-existing statement/artifact's `read_file_bytes`/`read_text` would
   have silently returned `None` post-migration, and `StatementStore.delete_statement`
   would have unlinked nothing while still dropping the DB row — a real-file leak riding
   every DR backup forever. Fixed by two new sub-steps, `_fix_statement_file_paths`/
   `_fix_artifact_file_paths`, rewriting every `statements/...`/`artifacts/...`-prefixed
   stored path (BOTH `statements_meta`'s promoted `file_path` column and the identical key
   inside its own `payload`, since the store's own read path — `StatementStore._full` —
   reads out of `payload`, not the column) to the new `files/...` form, idempotently. Proven
   against a real, secrets-stripped clone of the owner's live data (113 statements, 29
   artifacts) — 100% of artifacts and 107/109 statements read back correctly through their
   real store methods post-migration; the 2 unreadable statement rows were confirmed
   (via a second, untouched clone) to have been dangling — their backing file already
   missing — BEFORE this migration ever ran, an unrelated pre-existing data-integrity gap,
   not something this change introduced or is in scope to repair.

2. **A delete no longer actually deleted the document.** The migration's own
   non-destructive-trail convention (a receipt's old `.bin`, an attachment's
   `.pre-raw-file-migration-<ts>` backup) meant `ReceiptStore.delete_receipt`/
   `AttachmentStore.delete_attachment` — which only ever unlinked the CURRENT canonical
   path — left a full byte-identical copy of the original file on disk forever after a
   user-initiated delete, riding every backup zip and the sanitised export. Both now expose
   `unlink_all_blobs(ref_id)`, glob-unlinking every `<id>.*` sibling in their store
   directory (a single-level `Path.glob` pattern, inherently scoped — it cannot walk
   outside the directory), and both `delete_*`/`services.receipts._delete` route through it.

3. **Write-time and read-time extension could disagree.** `AttachmentStore.create()` passed
   the caller's raw `media_type`/`filename` params to `blob_path()` when deciding where to
   WRITE, but every read path re-derived the extension from the STORED row's resolved
   `media_type or entry.get("media_type")` value — so a caller passing `media_type=None`
   with a parsed `entry` that itself carries a real media type (`backend/api/upload.py`'s
   `store_upload()` helper, reproduced exactly in
   `test_create_resolves_media_type_once_so_write_and_read_paths_agree`) could write under
   one extension and have every subsequent read expect a different one — silently orphaning
   the bytes (`read_bytes`/`read_payload` just returned `None`). Fixed by resolving
   `filename`/`media_type` to their effective values ONCE, before either is used, and using
   that same resolved pair for both the write-time `blob_path()` call and the stored row.

4. **One bad attachment payload could brick every future boot.** `_fix_attachment_payloads`
   had no per-row error handling, and `ensure_migrated()` is fail-closed — an uncaught
   exception on a single malformed row (non-UTF-8/invalid JSON, a non-dict payload, a
   `"kind": "image"` row with `"data": null"` falling into `base64.b64decode(None)`,
   corrupt base64) would refuse to boot on EVERY subsequent attempt, since nothing about
   the bad row changes between tries. Now every row's processing is wrapped per-row; any
   failure is logged and counted in the migration's report rather than propagated — EXCEPT
   a sha256 byte-verification mismatch (`_ByteVerificationFailed`), which still halts boot,
   because that specific failure means real data corruption was just detected, not a
   malformed-but-recoverable row. The trimmed-payload write also moved from a bare
   `Path.write_text` to `atomic_write_text`, closing a separate crash-mid-write hazard that
   would have turned a one-time transient failure into a permanent one.

Two LOW-severity defense-in-depth items from the same review are also folded in, since both
were cheap relative to the stakes of code that runs unconditionally against real financial
data on every boot: `ReceiptStore.blob_path()`/`AttachmentStore.blob_path()` (and the
migration's own per-row path construction) now assert the constructed path resolves inside
the intended store directory, refusing rather than silently building a path that could
escape it via a hostile id; and `_merge_move_dir` now uses `os.path.lexists` (not
`Path.exists`) for its "already relocated" check — a dangling symlink at the destination
previously read as absent, and a symlink in the migration's source is now skipped and
logged rather than blindly relocated.

None of these four bugs were introduced by a design flaw in the decision above — each is a
completeness gap in the original implementation of that design (a step the original scope
didn't cover, a fallback path the original code didn't route through the same resolution,
error handling the original per-row loop didn't have). The decision itself — one pattern,
real files, one shared parent directory, migrated via a fail-closed Alembic revision — is
unchanged.

## Amendment (2026-08-22 — consolidate-derived-data-storage RFC)

This ADR's own "Alternatives considered" rejected folding `STATEMENTS_PARSED_DIR` into
`files/`, reasoning that it "holds derived/parsed data (extracted transaction rows), not an
original uploaded file — a different category from what `files/` groups, so folding it in
would blur the directory's own meaning" (decision point 4 restates the same line: "it isn't
one of 'this store's raw files' in the sense `files/` groups").

That line does not hold up, on the evidence of the tree THIS SAME MIGRATION produced.
`files/attachments/<id>.json` — decision point 3, above — is a pure-derived-fields payload
(no raw bytes; those moved OUT to the real file), and step 4 of this revision's own migration
docstring deliberately puts it inside `FILES_DIR / "attachments"`. So as shipped, `files/`
already meant "the filesystem home of the four blob stores", not "raw bytes only" — the
statements carve-out was one store obeying a rule the other store, in the same directory
tree, from the same migration, already broke. `.claude/rfcs/2026-08-22-consolidate-derived-
data-storage.md` found this by re-reading the tree this ADR actually produced (not merely
re-litigating the original design in the abstract), and revision `0046_derived_data_
colocation.py` reverses this one line: `STATEMENTS_PARSED_DIR` is RETIRED, its content moves
to `FILES_DIR/statements/<id>.parsed.json`, and attachments' own `<id>.json` sidecar is
renamed `<id>.parsed.json` in the same change — so the naming rule (`<id><ext>` = original,
`<id>.parsed.json` = derived) is now literally uniform across every store, closing the A.4
collision hazard that RFC's own §3 identifies (today safe only because `.json` isn't in the
extension allowlist).

Everything else this ADR decided is unchanged: real files with real extensions, one shared
`FILES_DIR` parent, local disk only, an Alembic-driven migration inheriting the fail-closed
boot gate, `unlink_all_blobs`'s single-directory glob-delete (statements gained its own
version of this the same day, closing the identical delete-completeness gap this ADR's own
security amendment, item 2, closed for receipts/attachments). See `docs/DATA_MODEL.md`'s
"Derived-data colocation" entry and `docs/adr/2026-08-22-consolidate-derived-data-storage.md`
(the RFC itself, kept as the design-options record) for the full detail, including the
`has_parsed_rows` vs. stored-path column decision this ADR never had to make (statements'
derived-cache pointer stayed a stored path here; the follow-on RFC revisited that too, once
the destination became fully deterministic).

## Amendment (2026-08-22 — security review fix round 2, same day as revision 0046)

A security review of revision 0046 (consolidate-derived-data-storage) before it could reach
a real boot found nine real findings — the same shape and stakes as the 2026-08-21 amendment
above, one migration cycle later. All nine fixed in the same pre-merge change, not a
follow-up:

1. **Statements' legacy sidecar source, `old_path`, had no containment check.** Unlike its
   sibling `new_path` (already `_assert_contained`), `_migrate_one_statement_row` used
   `data_dir / row.parsed_rows_path` directly — a DB string exactly as untrusted, on a
   portable-bundle restore, as the id class FR-C1 already guards. Fixed with the same
   `_assert_contained` re-proof against `old_parsed_dir`; a violation lands in that row's
   `report["failed"]` entry, per-row-fault-tolerant like every other caught failure here,
   never a boot-halting raise.
2. **The migration imported a path constant from a re-exporter, not its owner** —
   `backend.db.money_io.STATEMENTS_FILES_DIR`, itself just `from backend.core.paths import
   STATEMENTS_FILES_DIR` — risking a silent `ImportError` inside the fail-closed boot gate
   on a future rename of that re-export.
3. **The same constant was separately re-bound in five places** (`core/paths.py`,
   `backend/paths.py`, `statement_store.py`, `money_io.py`, `portable_bridge.py`), each a
   SEPARATE Python object reference despite naming the same directory — the test suite
   needed a ~13-file "double-monkeypatch dance" to keep them in sync, and a forgotten patch
   was a SILENT split-brain (`money_io._reinline_statement` degrading to `parsed_rows=None`
   instead of raising). Fixes 2 and 3 close together: `core.paths.statements_files_dir()`,
   a bare accessor resolved at call time from that module's own global, replaces every
   direct import of the constant — there is exactly ONE binding left to patch, anywhere,
   which structurally forecloses fix 2's whole class of problem rather than merely patching
   this one instance of it. `0007_money_core_cutover.py`'s own frozen `_bool01`/
   `validate_statement_id` imports from live `money_io` (the same rename-fragility class,
   smaller) were frozen locally too.
4. **The FR-C1 path-traversal regression test's canary was unreachable under the new
   layout**, so it could no longer distinguish "the escape was refused" from "the escape
   succeeded, just not where the old canary happened to be watching" — an `id="../auth"`
   escape from `data_dir/files/statements/` resolves to `data_dir/files/auth.parsed.json`
   now, not `data_dir/auth.json`. A second canary was seeded at the REAL current escape
   target.
5. **`delete_statement` no longer unlinked a row's stored `file_path` when it was
   NON-canonical** (left untouched by this same revision's own `_rewrite_old_relative_path`
   equivalent, or copied verbatim by a portable-bundle import) — a real leak riding every
   backup, the same class of bug the 2026-08-21 amendment's item 1 fixed for the READ side.
   Fixed WITHOUT reintroducing the unconditional unlink the 2026-08-21 rewrite replaced:
   `DATA_DIR / file_path` is only unlinked when it resolves INSIDE
   `statements_files_dir()` (contained check, mirroring `blob_path()`'s own defense-in-depth
   from that amendment's LOW-severity items); a path resolving outside is logged as an
   orphan needing manual attention instead — a discoverable leak, never a silent one, and
   never a delete of something this store cannot prove it owns.
6. **`_reinline_statement` derived the sidecar path from `payload.get('id')`**, not the
   row's own PRIMARY KEY column — latent (no current caller triggers a mismatch), but a
   merge of caller `changes` over a reconstructed row could in principle split them. Every
   call site now passes the column id explicitly.
7. **`delete_statement(conn=...)` unlinked blobs INSIDE the caller's transaction**, unlike
   `ReceiptStore.delete_receipt`'s own documented deferred-unlink contract (a filesystem
   delete is never undone by a SQL rollback). Now mirrors that contract exactly.
8. **`purge_migration_trail.py` (built the same day as revision 0046) was never extended
   for statements' new trail shape** — the pre-migration `statements_parsed/` directory was
   unreachable from any store method, leaking full historical parsed-rows content in every
   backup forever. Extended to sweep both trail shapes: `.migrated-<ts>` re-verified by
   JSON-equality against the canonical replacement before deleting; `.removed-<ts>`
   (an already-confirmed orphan, per the migration's own step 3) deletable outright.
9. Three RFC §6 test cases the reviewer flagged as missing were added: a statement delete
   removing raw file + sidecar + trail in one operation; the `.parsed.json` collision
   hazard pinned as structurally impossible (id is the table's PRIMARY KEY); an
   import-a-bundle-twice round trip against the staged temp path in
   `money_io.import_statements`.

Verified against a real, un-migrated clone of the owner's own data (`clone_data_for_qa.py`,
plus a manual read-only copy of the pre-migration `statements_parsed/` directory the QA
clone's own drop-list otherwise omits, since it assumes an already-migrated source) — all
three real statement rows in that data migrated byte-identically to their new sidecar, and
`purge_migration_trail.py --execute` correctly identified and removed their trail
afterward, leaving the canonical sidecars untouched.

None of these nine findings were a flaw in this revision's own design (one directory, one
glob-delete, per store; a boolean flag over a stored path once the destination is fully
deterministic) — each is a completeness gap in the implementation, the exact same shape as
the 2026-08-21 amendment above.

## Agent provenance

- **Decided by:** repo owner (found by inspecting `~/.open-net-worth/data/*/` directly) +
  implementing agent
- **Lane / branch:** `feat/attachments-raw-file-storage`
- **Alternatives weighed:** S3/object storage, decode-on-demand without a real file,
  a standalone migration script vs. an Alembic revision, folding `STATEMENTS_PARSED_DIR`
  into `files/`
- **Driving artifacts:** the owner's direct `~/.open-net-worth/data/*/` inspection; the
  same-day scanned-PDF-promoted-as-a-receipt bug this is directly upstream of; revision
  `0041_attachment_store.py`'s `_move_blob_dir` precedent
- **Why this way (one line):** three of four blob stores already did the right thing (a
  real file, a real extension, safely derived) — bring the fourth (and receipts' `.bin`
  gap) in line with that existing pattern, group all four under one discoverable parent
  directory, and let this repo's own fail-closed boot-migration machinery carry the risk
  of migrating real user data, rather than a bespoke script.
