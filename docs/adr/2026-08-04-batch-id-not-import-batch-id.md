---
title: The write-group field is batch_id, not import_batch_id
status: Accepted
date: 2026-08-04
deciders: Claude (senior-engineer lane) + owner
---

## Context

`import_batch_id` groups rows written in **one operation**. That is true of a statement import,
a connector sync, and a chat bulk-add alike — but the name claimed all of them were imports.

Asking Lucius to log five transactions mints a batch and tags every row `import_batch_id`,
because only a *single*-row call skips the batch machinery. And the field is read far outside
importing: `matching._same_feed` (invariant 3 — same batch ⇒ two real charges, never flagged
against each other) consults it on **every** write path.

The RFC that raised this also proposed it as the fix for chat bulk-adds appearing as imports in
`import_batches.json`. **That premise was wrong** and is corrected here: the records are already
labelled correctly — `ai/tools/ledger.py` writes `"Bulk add (<date>)"` and
`statements/decisions.py` writes `"Statement import: <filename>"`. The only thing that misstated
anything was the field name itself.

## Decision

Rename the stored field **`import_batch_id` → `batch_id`**, everywhere: 322 code sites
(119 backend, 34 frontend, 96 tests, 73 in test fixtures/goldens) and 1,973 keys across three
live data files.

**The store keeps its name.** `import_batches.json` / `ImportBatchStore` are untouched: the
record they hold is still mostly an import record, and renaming a data *file* touches the
checkpoint companion set and the portable-export manifest — a larger decision this change
deliberately does not fold in.

**No read-side alias, on purpose.** A stale `import_batch_id` (restored backup, old export)
simply reads as *no batch*, which is the safe direction: the row stops being grouped, so
`_same_feed` no longer exempts it and the pair is **surfaced for review** rather than silently
trusted. An alias would instead keep an unmigrated file working invisibly — which is exactly how
the 2026-07-17 description/memo split left 427 stale fixtures alive behind a fallback no
production path could reach.

## Consequences

**Positive.** The field says what it means, so `_same_feed` reading it on a chat bulk-add is no
longer a surprise. Nothing about grouping semantics changed.

**Negative.**
- **A breaking API change.** `POST /api/transactions/bulk` and the import-commit responses now
  emit `batch_id`. Any external client reading `import_batch_id` breaks. Accepted deliberately:
  a compatibility shim here is the same trap as the alias above.
- 1,973 keys rewritten across `transactions.json` (1,733), `import_batches.json` (231) and
  `merge_journal.json` (9). `statements.json` carried none.
- The migration recurses rather than touching row top level only — the key nests inside batch
  records, merge-journal snapshots and statement `import_rows`.

**Verified** on a copy of the live book: 1,973 keys renamed, **values identical**, row ids
identical, legacy key absent, and a second run reports nothing to migrate. Suite 12,808 passed;
frontend `tsc` clean, matching `main`.

## Alternatives considered

- **Keep the name, fix the label.** Rejected once the premise was checked — the labels were
  already correct, so there was nothing to fix and the name remained the only inaccuracy.
- **Rename the store/file too** (`import_batches.json` → `batches.json`). Deferred: it touches
  checkpoint companions and the export manifest, and the record genuinely is import-shaped.
- **Read-side alias for one release.** Rejected — see above; the safe failure mode makes it
  unnecessary, and an unreachable shim hides a migration backlog.

## Agent provenance

- **Decided by:** Claude (senior-engineer lane), owner directing an end-to-end run.
- **Driving artifacts:** RFC `.claude/rfcs/2026-08-02-transaction-provenance-one-fact-one-field.md`
  §"batch_id"; measured scope (322 code sites, 1,973 data keys).
- **Why this way:** the field is read by every write path, not just imports, so the name was
  actively misleading about which code may consult it — and the no-alias choice is what keeps
  the migration honest rather than indefinitely deferred.
