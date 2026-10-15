---
title: Export bundles carry every file tree and stream through disk
status: Accepted
date: 2026-10-03
deciders: W1 agent-app workstream (Claude) + repo owner
---

# Export bundles carry every file tree and stream through disk

## Context

`core.portable.PORTABLE_DIRS` (skills, attachments, receipts) was never read by the export, and
statement source files and generated documents were never listed anywhere, so every export, backup
and move silently dropped them. The bundle was also assembled and read as `bytes` — a book with
gigabytes of documents cannot move through a 1 GB machine that way.

## Decision

1. The bundle carries `blobs/<tree>/<relative path>` for `core.portable.portable_trees()` (skills,
   attachments, receipts, statements, artifacts). A statement's `*.parsed.json` sidecar is not
   exported (the money bridge already inlines it) and is kept when the tree is replaced. Symlinks
   are never exported.
2. Export writes to a temp file under the cache dir and the API serves that file; import reads from
   the upload's spooled file. Files are copied in 1 MiB chunks, never held whole.
3. Import is two-phase: every file is copied and checked (size and sha256) into a staging directory
   beside its live tree **before** any store is replaced; they are swapped in after the stores. A
   corrupt file aborts with nothing changed.
4. The manifest lists `blob_trees`. A tree it lists is replaced exactly; an older bundle lists none,
   so its import leaves the existing files alone — this is the "old bundles still import" rule, not a
   second reader.
5. The fixed 100 MB upload cap is replaced by free disk space less a margin.

## Consequences

### Positive
- Export, backup and move no longer lose files; memory use does not grow with the size of the book.

### Negative
- Import still runs inside the request, on the event loop, for the stores' JSON; a very large import
  holds the worker. The move protocol (W2) commits from disk in its own job.
- Chat sessions and flat stores are still built as in-memory entries (each under the old 100 MB cap).

## Alternatives considered

- A new bundle schema version — rejected: it would refuse every existing backup for no gain.
- Writing blobs straight into the live trees while reading — rejected: a corrupt file would leave a
  half-replaced book.

## Agent provenance

- **Decided by:** W1 agent-app workstream (Claude)
- **Lane / branch:** `build/w1-agent-cloud`
- **Alternatives weighed:** new schema version; in-place restore
- **Driving artifacts:** Private Cloud contracts C9, gaps M1/M2
- **Why this way (one line):** verify everything before replacing anything, and never hold a file whole.
