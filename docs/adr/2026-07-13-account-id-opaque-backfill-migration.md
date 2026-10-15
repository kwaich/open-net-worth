# Backfill every account id onto the short opaque `acc_` scheme

**Date**: 2026-07-13 · **Status**: Accepted · **Provenance**: Agent (Claude) build decision, owner-directed ("let's do backfill migration!", "i will be afk, execute by yourself with backup", "please ensure keeping everything consistent, do not leave anything not consistent")

## Context

`core/ids.py` (ADR 0011) mints every entity id as `prefix_<12hex>` — short, token-cheap,
self-describing — specifically because a long dashed UUID shared across many similar
records in one tool-call context invites exactly the failure that happened: the
Transaction & Trade Logger agent needed the Meridian Bank Rewards Card's account id, held it and
a second wallet account's id in context together, and spliced fragments of the two real
UUIDs into a fabricated, non-existent id (`51dbc39c-a12e-41df-b9fc-1352f6715053` — one account's
prefix + the other's tail, one nibble flipped). It then insisted the id was correct through
three straight `add_transaction` rejections instead of re-calling `list_accounts`, and told
the user "system error, check account configuration" — a false diagnosis. The real cause:
`backend/db/account_store.py` already defaults new accounts to `new_id("acc")`, but no
backfill migration had ever reminted the *existing* accounts (unlike categories, which did
get an opaque-id migration). Every one of this user's 29 live accounts still carried its
original 36-character UUID.

## Decision

One-shot migration (`backend/scripts/migrate_account_ids.py`) reminting every legacy-UUID
account id to `acc_<12hex>` and cascading the rename everywhere it's referenced:

1. **Pure id relocation, not a data change.** No amount, date, category, or any business
   field moves — only the label an account is known by. Verified: account-side net worth
   is byte-identical before/after on the live tree (SGD 980,000.00 both ways).
2. **Full consistency, not just structured foreign keys** (owner decision): every
   structured FK field (`transactions.json` `from_account_id`/`to_account_id`,
   `positions.json`/`lots.json`/`realized_events.json`/`income_events.json`/`grants.json`
   `account_id`, `commitments.json` `linked_account_id`, `scheduled_tasks.json`
   `card_account_id`, `import_batches.json`/`statements.json`/`rules.json` account
   references, connector `account_map` **values**), forensic/historical logs
   (`txn_audit.json`, `mcp_proposals.json` — an id is a pointer, not the substance of what
   happened, so relabeling it doesn't falsify the record), and free-text agent/chat
   narration (`run_history.json`, `agent_findings.json`, `memory.json`, chat session
   transcripts) are all rewritten. Nothing is left referencing a stale id, structured or
   prose.
3. **Point-in-time snapshots are deliberately excluded** — `checkpoints/`, `backups/`,
   `_repair_backup_*/`, sibling `data-backup-*` dirs, and any `*.bak*`/`*.tmp-*`/
   `*.corrupt-*` file. These represent history as it was captured; a checkpoint restore is
   supposed to bring back the old state, not a silently-patched one.
4. **Generic recursive rewrite, not a hand-maintained field list.** `_rewrite()` walks any
   JSON structure and replaces a legacy id wherever it appears as a string value — exact
   match (structured fields) or substring (prose). Dict *keys* are left untouched (a
   connector's `account_map` key is the external provider's id, never ours). This is
   deliberately more complete than an enumerated per-store field list and doesn't need
   updating when a new store adds an `account_id`-shaped field.
5. **Grep-gated per file**: a file is parsed and rewritten only if it actually contains a
   legacy id right now — an unrelated store (categories, envelopes, watchlist, ...) is
   never opened for writing.
6. **Safe by construction**: dry-run default; `--apply` backs up the full data dir first
   (`data-backup-migrate-account-ids`, mirrors the `migrate_starting_balance` precedent);
   idempotent (only non-`acc_`-prefixed ids are reminted, so a second run is a no-op);
   post-write verification asserts zero remaining legacy-id occurrences across every
   scanned file AND that each file's own record count is unchanged (a pure re-serialize
   can't drop or duplicate a row).

**No code changes were needed beyond the migration script.** A full repo sweep (models,
stores, frontend types) found zero UUID-format validation or parsing for account ids
anywhere — the switch is a pure data-shape change.

**Pre-existing, unrelated data anomalies found during verification (not fixed by this
migration, flagged for separate follow-up):** one transaction (`82ec2960-...`) carries the
literal string `"null"` instead of JSON `null` for `to_account_id`; six transactions
reference two already-short account ids (`acc_76e0fd5a0000`, `acc_438931260000`) that don't
exist in the current `accounts.json` at all. Both predate this migration and are out of its
scope (a pure id-relocation shouldn't also silently repair unrelated data-quality bugs).

## Consequences

- Every account id in the live book is now `acc_<12hex>`, consistent with every other
  entity type, closing the exact failure class that caused the Grab-transaction and
  ExampleSub-classification incidents from recurring on account ids specifically.
- The two flagged pre-existing anomalies remain open and should be triaged separately.
