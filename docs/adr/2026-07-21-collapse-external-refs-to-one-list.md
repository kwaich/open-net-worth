---
title: Collapse `external_source`/`external_ref`/`external_refs` into ONE `external_refs` list; multi-source-email drawer
status: Accepted
date: 2026-07-21
deciders: Repo owner + Claude (data-model / dedup / drawer UX)
supersedes: docs/adr/2026-07-20-retire-external-id-two-field-split.md (the scalar+list field layout only — the retirement of the combined string, the short server-computed email id, and every dedup invariant stand unchanged)
---

# 2026-07-21. One `external_refs` list — no scalar primary pair

## Context

The 2026-07-20 external-id retirement left a transaction with THREE provenance fields:
`external_source` + `external_ref` (scalars — the creation-time origin) and `external_refs`
(a list — pairs absorbed by the merge/dedup-review flow). The split was an artifact of the old
`external_id`/`external_ids` naming carried forward, not a design: the runtime already unions
the scalar pair and the list into one set before ANY dedup check runs
(`core.txn_sources.ext_ref_pairs`), so the scalar/list distinction was load-bearing only for
"how a writer passes the value in" and for the two display readers that wanted "the" origin
(Added-by inference, the drawer's Source-email section). The owner decided to collapse it.

## Decision

1. **Schema.** `external_source`/`external_ref` are REMOVED everywhere (model, store, REST,
   AI tool, eval mirror). `external_refs: List[{"source": str|None, "ref": str}]` is the ONLY
   provenance field, always present (may be `[]`). **The FIRST entry is the creation-time
   origin** — there is no dedicated "primary" field, just list order; merges append absorbed
   pairs after it. `core.txn_sources` gains `primary_pair`/`primary_source`/`primary_ref` so
   the "the source" readers (`audit._infer_actor_surface`, opening-txn guards, feed-grade
   checks, `tax._is_investment_income_txn`, `matching._judge_payload`) share one definition
   of entry-[0] instead of each re-implementing it.
2. **Every pair is a dedup key** (unchanged in substance): `TransactionStore.by_external_ref`
   (source, ref) matches anywhere in the list; the L0 floor in `matching.check` now iterates
   EVERY pair the incoming row carries (deterministic order) instead of only the scalar pair.
   `merge_transactions` becomes a straight order-preserving union of two lists.
   `merged_from` (internal txn ids absorbed by merge) is untouched — different concern.
3. **Clean cut, no tolerance shims.** No dual-read of the scalar fields anywhere. The AI tool
   input (`add_transactions`/`update_transactions`) exposes an `external_refs` array (item
   `{source: enum["email"], ref}`) and REFUSES the retired flat spelling with a loud
   `ValidationError` — silently ignoring unknown keys would silently drop dedup, the worst
   failure mode. Starter agent/skill prompts and the frontend agent template are updated to
   the list spelling (an install seeded earlier keeps its old prompt until re-seeded —
   `seed_starters()` never overwrites; the loud refusal is what surfaces that staleness).
4. **Multi-source-email drawer (Option B).** `email_snapshots.snapshots_for_transaction`
   returns a LIST — one entry per `{"source": "email"}` pair (a merge can absorb several
   source emails onto one row), oldest-received-first; a pruned/never-stashed snapshot is an
   explicit `{"message_id", "unavailable": true}` entry, never a silent omission (owner:
   missing evidence stays visible). REST: `GET /api/transactions/{id}/source-email` →
   `GET .../source-emails` returning `{"emails": [...]}` (batch-pluralization precedent).
   The drawer renders one repeatable Subject/From/Received/full-body block per email with
   independent expand state keyed by message id; the single-email case renders identically
   to before (header stays "Source email", no added chrome).
5. **Migration** — `backend/scripts/migrate_external_refs_collapse.py`, the 4th one-shot pass
   in this train (dry-run default, `--apply`, timestamped backups, atomic writes, idempotent,
   NOT a boot migration). A fresh full-tree scan (`--scan`) found **five** locations, not the
   four the plan listed: `transactions.json`, `statements.json` `import_rows[]`,
   `import_batches.json` `merges[].before`, checkpoint blobs (re-pointed by new digest), and
   — new — **`merge_journal.json`** (`canonical_before`/`absorbed_before`/
   `post_canonical_snapshot`): `unmerge` restores those snapshots verbatim and byte-compares
   the live canonical against `post_canonical_snapshot`, so un-migrated journal entries would
   refuse every undo and could write scalar-shaped rows back into a migrated ledger.
   Deliberately NOT migrated: `run_history.json` `steps[].input` and session transcripts'
   recorded tool inputs — they are history (never re-executed, never read back into the
   ledger); rewriting them would falsify the record of what the model actually sent.

## Consequences

- Writers pass one shape (`external_refs=[{source, ref}]`); readers needing "the" origin use
  `primary_source`/`primary_pair`. No more strip-both-then-reattach dances in
  `update_transaction`/`set_account_opening` — one key to pop, one to reattach.
- A row's origin is now positional. Anything that REORDERS `external_refs` would silently
  change the row's displayed origin — merges must keep appending, never prepending
  (pinned by the merge-union tests).
- The statement-parse `CanonicalRow` dataclass carries `external_refs: list` (cached
  `import_rows` round-trip through `CanonicalRow(**row)`, hence the statements.json
  migration).
- Old stored tool calls / an old seeded agent prompt using the flat fields fail LOUDLY at the
  tool boundary instead of silently losing dedup — the deliberate trade of the no-shim rule.
