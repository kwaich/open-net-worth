---
title: Retire the transaction `note` INPUT alias — finish the description/memo split
status: Accepted
date: 2026-07-20
deciders: Repo owner + Claude (data-model)
amends: 2026-07-17-split-note-into-description-and-memo.md
---

# 2026-07-20. Retire the transaction `note` input alias

## Context

ADR `2026-07-17-split-note-into-description-and-memo` split the transaction ledger's single
freeform `note` field into `description` (the bank/matching string) and `memo` (the user's
personal annotation), and shipped a one-shot migration for data already on disk
(`backend/scripts/migrate_note_into_description.py`). To keep that cutover byte-identical it
deliberately left a **write-side compatibility alias**: any caller still sending `note` had it
silently mapped onto `description`, duplicated across at least six surfaces —

- Two AI tool schemas (`schedule_task`/`update_scheduled_task`'s recurring-transaction template)
  still advertised `note` to the model outright.
- `ai/tools/_shared.py`'s `_txn_data_from_ai` (the `add_transactions` normaliser) read
  `item.get("description") or item.get("note")`.
- `mcp/adapter.py`'s write-audit summary read the same fallback.
- `models/transaction.py` (a Pydantic scaffold) and `api/ledger.py` (the LIVE REST request models
  — `TransactionCreate`/`TransactionUpdate`/`BulkTxnChanges`/`BulkTxnUpdateFlat`) each carried a
  `model_validator(mode="before")` that rewrote a posted `note` onto `description`.
- The write CHOKE POINTS themselves — `services/ledger.py`'s `create_transaction`,
  `bulk_create_transactions`, and `update_transaction` — re-applied the same alias to the row/patch
  dict, and `db/transaction_store.py::make_transaction` applied it a THIRD time at the bottom of
  the write path.

Owner directive (verbatim): "just use description only, stop having backward compatible code for
old ones." This ADR records the sweep that finished the retirement and the two places something
was deliberately kept, with the reasoning, per the instruction to never keep a safety net silently.

## Decision

**Every WRITE-facing acceptance of `note` is removed.** A caller (AI tool, REST client, MCP call,
or internal service code) that still sends `note` now has it silently ignored — exactly like any
other field the schema/model doesn't declare — rather than aliased onto `description`. Removed
from: both scheduling tool schemas (`ai/tools/scheduling.py::TaskTransactionTemplate`, which also
gained a real `memo` field), `ai/tools/_shared.py`, `ai/tools/ledger.py`'s error-logging path,
`mcp/adapter.py`, `models/transaction.py`, `api/ledger.py` (all four request models),
`services/ledger.py`'s three choke points, `services/ledger_preview.py::preview_update` (which
mirrors the update choke so the confirm-card preview never disagrees with the write), and
`db/transaction_store.py::make_transaction`.

**A live bug was found and fixed along the way.** `services/options.py`'s
`_maybe_book_premium_leg`/`_maybe_book_close_premium_leg` (the option premium/close cash-leg
booking) constructed their `create_transaction` dict with `"note": note` — never `description` —
so those four legs were relying ENTIRELY on the now-removed choke-point alias to get a
description at all. Fixed to pass `"description"` directly; regression assertions added to
`tests/test_options_edge.py`/`tests/test_options_close_leg.py` (they would now fail with a blank
description if the alias, or this fix, ever regressed).

**Two things were deliberately KEPT, both read-only, both already-decided by the 2026-07-17 ADR:**

1. `TransactionStore._load_items()` still backfills `description` from a still-present `note` on
   READ, and `db.transaction_store.txn_text()` still falls back to `note` for a raw dict that
   bypasses the store. This is a self-hosted, open-source app; the migration script is opt-in
   (`--apply`, not a boot migration), so another install's `transactions.json` may still carry
   un-migrated `note` rows today. Removing this would silently blank every such row's description
   on the very next read — the "silent blank description" failure mode this change is explicitly
   trying to AVOID, not cause. This is unchanged from 2026-07-17 and was never in scope to remove.
2. `services/scheduling.py::_run_create_transaction` (the recurring-transaction runner) keeps a
   narrow read-side `template.get("description") or template.get("note")` when building the
   posted row. Reasoning: `scheduled_tasks.json` is persisted data exactly like
   `transactions.json` — a task authored before this fix could have `transaction.note` sitting on
   disk with no `description`, and nothing else in the app ever re-normalises a stored task the way
   `_load_items()` re-normalises a stored transaction. Without this, such a task would silently
   post a blank description on every future run, forever, with no error — the single worst outcome
   named in this change's own review guidance. `description` still wins when both are present.

**Checked and found unnecessary: a migration script for `scheduled_tasks.json`.** The owner's live
`scheduled_tasks.json` (15 tasks) has zero rows with `action="create_transaction"` and zero raw
`"note"` occurrences anywhere in the file — so the dry-run-default/`--apply`/backup script pattern
this task considered writing would have nothing to migrate. Per the task's own instruction ("if
there are none, say so and skip it"), no script was written. This was checked against the one
accessible install only; the kept read-fallback in `_run_create_transaction` above is what protects
a different self-hosted install that does have an affected row.

**Left alone, not part of this retirement (same field name, different thing):**

- `services/ledger.py`'s `q_field="note"` search-scope value (and the frontend register's
  `note:`/`memo:` search token that sends it) — a DELIBERATE, documented, still-live UX convenience
  that searches BOTH `description` and `memo` under one ergonomic name (`txn_matches_q`'s own
  comment: "This is what the register's `note:` search token sends, so it stays span-both."). It
  was never part of the AI tool schema's `q_field` enum and nothing here changes it.
- `splits[i].note` (a per-split-line annotation) and every OTHER entity's own legitimate `note`
  field — accounts, positions, lots, grants/vests/tranches, income events, option contracts,
  watchlist items, alerts, audit-timeline notes, envelope/category notes. None of these are the
  transaction ledger's retired field; they are unrelated columns that happen to share a name.
- `services/rules.py::_txn_desc()` was simplified to delegate to `db.transaction_store.txn_text()`
  instead of re-implementing the identical `description or note` fallback a second time — a
  same-behavior de-duplication, not a new decision.

## Consequences

### Positive
- One real implementation of "what is this transaction's bank/matching text," not three
  (`services/ledger.py` choke points, `make_transaction`, and the REST/AI/MCP input layers all
  previously re-derived it).
- Found and fixed a live bug (options premium/close legs booking with an unintentionally-blank
  description) that the alias had been masking since the 2026-07-17 cutover.
- The recurring-transaction template UI (`ScheduledTasks.tsx`) is fixed to actually send
  `description`/`memo` — it had been sending `note`, which the REST `TransactionTemplateSpec`
  never declared, so the field was silently dropped by pydantic on every save. Gained a Memo
  field in the same pass for parity with the manual ledger entry form.

### Negative / accepted risk
- A REST client (or MCP caller, or a stale scripted integration) still hand-authoring bodies with
  `note` will now have that text silently dropped rather than aliased — a deliberate breaking
  change, directed by the owner, consistent with "no backward-compat shims" (`CLAUDE.md`). No
  loud error is raised for this case (an unrecognized JSON field is not distinguishable from a
  typo at the pydantic layer); the two READ-only safety nets above are the mitigation for
  already-persisted data, not for a client's future write.

## Alternatives considered

- **Raise/refuse on an incoming `note` key instead of silently dropping it** — considered for the
  REST/MCP/AI-tool surfaces, to make a stale caller's mistake loud rather than silent. Rejected for
  v1: none of these surfaces validate `additionalProperties` today (the Tool error contract
  explicitly documents this as intentional — "the declared schemas do NOT describe what the
  handlers accept"), so adding one special case for this one retired field would be inconsistent
  with the rest of the surface. The AI tool schema no longer naming `note` at all is the load-bearing
  fix (a model reads the schema); a hand-written REST/MCP caller ignoring the schema is already
  outside the guarantees this app makes.
- **Also remove the `_run_create_transaction` read fallback** (treat it exactly like the ledger
  choke points) — rejected: unlike the ledger choke points, nothing else in the app re-normalises
  a stored scheduled task, so removing it converts "a pre-existing task might error loudly" into "a
  pre-existing task silently posts blank descriptions forever," which is strictly worse and is the
  one failure mode this whole change is written to avoid.

## Agent provenance

- **Decided by:** Repo owner (the retirement directive, verbatim, and the "report what you kept"
  instruction), implemented by Claude.
- **Driving artifact:** this ADR; the touch list is the six-plus-choke-point sweep described above,
  each fixed in the same change, plus the `services/options.py` bug found during the sweep.
- **Why this way (one line):** delete every WRITE-side acceptance of a retired field name across
  all six duplicated implementations (closing the actual owner ask), keep only the two READ-side
  safety nets that protect data already on disk from a silent, irreversible blanking — and say so
  out loud rather than leaving a silent judgment call for someone else to discover later.
