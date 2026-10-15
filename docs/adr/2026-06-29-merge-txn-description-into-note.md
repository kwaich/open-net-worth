---
title: Merge transaction `description` into a single `note` field
status: Accepted
date: 2026-06-29
deciders: Repo owner + Claude (data-model hardening)
---

# 2026-06-29. Merge transaction `description` into a single `note` field

## Context

A stored transaction historically carried **two** freeform text fields: `description` and `note`.
There was never a principled separation between them — both are unstructured, user-facing strings
with no schema, no distinct semantics, and no consumer that treated them differently in a load-
bearing way. In practice `description` held the imported/typed label and `note` held an
occasional annotation, but importers, rules (`note_append`), and the UI all blurred the line. Two
freeform fields for one job is a recurring source of "which one do I read/write?" bugs and forces
every consumer (display, search, dedup signature, audit) to decide how to combine them.

The runtime model had already been collapsed to a single survivor `note`
(`backend/models/transaction.py` — `note` is "the single freeform text field (collapsed
description+note survivor)"), with `transaction_store._merge_note()` acting as a transition shim
that folds any legacy `description` an un-migrated caller still passes into `note`. But the
persisted `transactions.json` on disk still carried both keys (verified: all 1266 live rows have a
`description` key), so the stored shape and the model shape had diverged. That gap is exactly the
kind of latent inconsistency that bites later (a raw reader, an export, a future schema check).

## Decision

**Collapse to a single freeform field, `note`. Drop `description` entirely.**

A one-shot migration (`backend/scripts/migrate_txn_description_into_note.py`) rewrites
`transactions.json` so each row's `note` becomes the merge of the old `description` + `note`, and
the `description` key is **deleted**. The merge rule is byte-identical to the runtime shim
`transaction_store._merge_note`:

- only one set → that one
- both set and identical → one copy
- both set and differ → `"{description} — {note}"` (em-dash separator)
- neither set → `""`

This keeps a row migrated on disk indistinguishable from one re-normalised by the running app, so
the migration can run before or after any given row passes through the choke point without changing
the result.

### Migration approach + backup

- **Idempotent.** A row already migrated has no `description` key and is skipped; a second run
  reports zero migrations and writes nothing. Safe to re-run.
- **Dry-run is the default** (`--dry-run`): reports counts (total / migrated / both-merged /
  skipped) and writes NOTHING. `--apply` is required to write.
- **Backup-first on apply.** Before any write, the script drops a timestamped copy
  (`transactions.json.bak-YYYYMMDD-HHMMSS`) next to the target, then writes through the app's atomic
  seam `core.io.atomic_write_text` when importable, else a local temp-file + `os.replace` rename —
  so a crash mid-write can never truncate the ledger.
- **Reads the app's configured `DATA_DIR`** (`backend/paths.TRANSACTIONS_JSON`) by default; `--file`
  targets a copy. Proven correct by cloning the live data to a temp dir and asserting the result
  (dry-run mutates nothing; apply merges all 1266 rows to their expected note, leaves zero
  `description` keys, writes one backup, and the second apply is a no-op). The live tree was never
  written by the author.

### Dedup / audit / external_id implications

- **Dedup.** The duplicate-detection signature must read `note`, not `description`. Any matcher that
  hashed/compared `description` now compares the merged `note`. Because the merge is deterministic
  and identical to the runtime shim, two rows that were duplicates before remain duplicates after
  (their merged notes are equal iff their source pairs were equal). No new false-positive or
  false-negative classes are introduced by the field rename itself.
- **Audit.** The migration is a bulk data rewrite performed OUT OF BAND of the ledger choke point —
  it deliberately does NOT emit `txn_audit.json` events (it is a schema migration, not a user/agent
  edit; 1266 phantom "update" events would be noise and would misattribute the change). The
  pre-write backup is the audit trail for the migration itself. Going forward, edits to the
  freeform text are ordinary `update` events on the single `note` field.
- **external_id.** Untouched. `external_id` is the importer's idempotency key and is independent of
  the freeform text; the migration neither reads nor writes it, so import dedup and the
  `by_external_id` lookup are unaffected.

## Consequences

### Positive

- One freeform field — no more "which one?" ambiguity for any consumer (display, search, dedup,
  rules, export). Stored shape now matches the model shape.
- The migration is reversible (timestamped backup) and re-runnable (idempotent).

### Negative

- The `description` → `note` merge is **lossy of the boundary**: once both fields are folded with
  the `—` separator, you can no longer mechanically tell which half was the original description.
  The backup is the only way back.
- Any external/raw reader of `transactions.json` that still expects a `description` key must be
  updated; post-migration the key is absent.

### Follow-ups

- The runtime `_merge_note` shim can eventually be retired once we are confident no caller passes
  `description` anymore (kept for now as cheap insurance).

## Alternatives considered

- **Keep both fields, define a convention** — rejected: there was no real semantic split to encode;
  a convention nobody enforces just re-creates the ambiguity.
- **Drop `description`, discard its text** — rejected: lossy of real user data; many rows carry the
  label only in `description`.
- **Migrate via the ledger choke point (per-row `update_transaction`)** — rejected: emits 1266 audit
  events, is far slower, and re-runs FX/rules side effects for a pure text rename; a direct,
  backed-up file rewrite is the right tool for a schema migration.

## Agent provenance

- **Decided by:** Claude (data-model hardening subagent), ratified by repo owner.
- **Lane / branch:** `main` (migration script + test + this ADR authored together).
- **Alternatives weighed:** keep both + convention / drop description text / migrate via choke point
  — all rejected in favour of a single `note` with a backed-up, idempotent direct rewrite.
- **Driving artifacts:** `backend/scripts/migrate_txn_description_into_note.py`,
  `tests/test_migrate_txn_description_into_note.py`, `backend/models/transaction.py` (`note`
  survivor), `backend/db/transaction_store.py` (`_merge_note` shim).
- **Why this way (one line):** two freeform fields for one job had no principled separation, so we
  collapse to one (`note`) and finish the already-started model migration on disk — safely, with a
  backup and an idempotent dry-run-default script.
