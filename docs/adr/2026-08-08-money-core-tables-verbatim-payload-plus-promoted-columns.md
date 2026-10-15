---
title: Money-core tables — verbatim payload + promoted columns everywhere, RESTRICT/CASCADE FKs, no STRICT, opening pointer stays a bare column, money imports are undoable
status: Accepted
date: 2026-08-08
deciders: Claude (implementer agents, storage plan 3 tasks 2/3/4/6/8 — money schema, absorb, chokes, portable bridge, rehearsal), repo owner (D5/D6 rulings, design spec addendum)
---

# 2026-08-08. Money-core schema: verbatim payload + promoted columns, FK design, no STRICT, the opening-pointer protocol, and undoable money imports

## Context

Storage plan 3 moved the money core — `accounts`, `transactions` (+ `transaction_splits` +
`transaction_external_refs`), `txn_audit`, `import_batches`, `statements_meta`, `merge_journal` —
off `JsonStore` and onto SQLAlchemy Core + Alembic as **one referential unit** (design spec
`docs/superpowers/specs/2026-08-05-storage-engine-sqlite-postgres-design.md`, D4/D5). Four load-
bearing constraints were already in play before a line of schema was written:

- The design spec's original Tier-1 line called for **SQLite `STRICT` tables** on this tier ("type
  affinity silently accepting a string into a money column is exactly the failure this migration
  exists to prevent").
- The program's own cross-cutting discipline rule (D1) is **no dialect-specific SQL** — verified
  genuinely on Postgres in CI from the first revision, not assumed.
- CLAUDE.md's ledger invariant: **"An account's opening is ONE fact: `opening_txn_id`."** A
  dangling pointer RAISES on every read; the write choke (`set_account_opening`) tolerantly
  re-mints. Before this plan, the file-era checkpoint mechanism protected the `accounts.json` /
  `transactions.json` pair via a hand-maintained `_COMPANIONS` map ("capture the WHOLE partner FILE
  alongside this one on write") — the only unit a whole-file checkpoint had to work with.
- The Plan-2 ruling that a bridged reference-data import (evidence/price_history/instruments) fires
  **no** write hook ("an admin restore, not an AI-turn mutation … outside the checkpoint/undo system
  entirely") was explicitly scoped to reference data, not money.

Task 2's original brief called for `transactions` to be **fully promoted, no payload column** — a
Tier-1-proper table, matching `accounts`/`import_batches`/`statements_meta`/`merge_journal`'s
sibling "promoted-columns-over-payload" shape only loosely. Task 2's own review found this
contradicted the live ledger: `created_at` is genuinely absent (not merely falsy) on 68 of 1,816
real transaction rows, `memo` on 1,228 of 1,816, and 86 of 87 real statement rows carry their batch
linkage under a dead, unrenamed key (`import_batch_ids`) rather than the promoted column's name. A
promoted-only column cannot distinguish "key absent" from "key present but empty" — it must invent
a value for one, silently merging two different historical facts.

## Decision

We will:

1. **Every one of the eight money tables carries `payload JSON NOT NULL` as the round-trip source
   of truth, plus promoted columns as a derived query surface recomputed from the row on every
   write — never the other way around.** This supersedes task 2's original "transactions fully
   promoted" instruction: `transactions`/`transaction_splits`/`transaction_external_refs` adopt the
   SAME verbatim-payload pattern `accounts`/`import_batches`/`statements_meta`/`merge_journal`
   already used, specifically so a genuinely-missing key round-trips as genuinely missing.
   `created_at`/`memo`/`tags`/`merged_from` (`transactions`) and `batch_ids`
   (`statements_meta`) get type-appropriate `server_default`s (still NOT NULL) precisely because
   `payload`, not the promoted column, is now where absence is provable — a promoted column's
   NOT-NULL default is an honest "unknown, see payload" sentinel, never a claim the source was
   empty. `statements_meta`'s absorb additionally reads the dead key
   `import_batch_ids` as a fallback into the promoted `batch_ids` column (input normalization at
   promotion time — allowed under the owner's no-legacy-tolerance-branches rule; `payload` stays
   verbatim under whichever key the source row actually used).
2. **FK design**: `book_id` leads every non-PK index (Tier-1 partition key, spec D4). `from_account_id`/
   `to_account_id` on `transactions` are `ForeignKey("accounts.id", ondelete="RESTRICT")` — the
   existing "`delete_account` refused while referenced" invariant becomes DB-enforced, not only
   service-enforced. `transaction_splits`/`transaction_external_refs` are `ForeignKey("transactions.id",
   ondelete="CASCADE")`. `accounts.opening_txn_id` and `transactions.merged_into` carry **NO FK** —
   both are circular-reference hazards (an opening pointer into a transaction row that may not exist
   yet during repair; a merge/undo target that can transiently point at a row still mid-tombstone) —
   services own the invariant in both cases, matching the pre-existing pattern for each.
3. **No `STRICT` tables** — this is the Global-Constraints-level ruling this ADR records for the
   schema that actually shipped: `STRICT` is SQLite-only DDL, and the program's own "no
   dialect-specific SQL" rule outranks the spec's original Tier-1 line. Typed columns +
   `CheckConstraint` (portable across SQLite/Postgres, e.g. `transfer_has_both_legs`, the four
   boolean-shaped `IN (0,1)` checks) + `NOT NULL` deliver the same integrity a self-hoster needs
   without the cross-dialect divergence Postgres-in-CI exists to prevent. `docs/superpowers/specs/
   2026-08-05-storage-engine-sqlite-postgres-design.md` gets a one-line amendment recording this
   (this task's docs pass).
4. **The opening-pointer protocol is unchanged in substance, only in substrate.**
   `accounts.opening_txn_id` stays a plain nullable `Text` column with no FK; the dangling-RAISES-
   on-read / re-mintable-on-write contract (CLAUDE.md, `set_account_opening`) is identical to the
   file era. What changes: the file-era `_COMPANIONS` map (`accounts.json` ↔ `transactions.json`,
   ADR `2026-08-01-referential-file-pairs-revert-together`) is now **structurally obsolete** and
   retired to an empty dict (kept as a mechanism for any *future* file pair, not deleted as code) —
   row-level `write_log` images (spec D7) capture every touched row in every table a turn touches,
   with no table-name pairing map needed: a `set_account_opening` write composes `accounts` +
   `transactions` through ONE `locked_unit_of_work`, so both rows land in the SAME checkpoint and
   restore reverts them together by construction. Proven directly by
   `tests/db/test_write_log_checkpoints.py::test_a_transactions_write_also_captures_the_opening_
   pointer_row_no_companions_map_needed`, and independently reproven by this task's own rehearsal
   3 `/rewind` proof (below).
5. **Money bundle imports are undoable — the opposite ruling from reference data.** The Plan-2
   no-hook ruling stays scoped to evidence/price_history/instruments (an admin restore). Money's
   `money_io.import_<store>` functions fire `fire_db_write_hook` for every row they insert;
   `portable_bridge.apply_money_bundle`'s wipe phase fires it again for every row about to be
   removed — together giving `/api/import`'s existing `begin_turn`/`end_turn` wrap (already true
   before this task; the only caller of `apply_import`) a complete before-image, table by table, so
   a self-hoster who imports the wrong bundle over their real ledger can `/rewind` it like any other
   AI-turn write. `txn_audit.json`/`merge_journal.json` join `PORTABLE_FILES`/`BRIDGED` for the
   first time this task — historically neither was ever wiped by `/api/reset` (an omission, not a
   considered exemption: both files were added to the app after the hand-enumerated wipe list
   existed, and nobody back-filled the manifest — confirmed by `git log -p --follow` returning zero
   hits for "audit" across `api/setup.py`'s whole history). The other four money files were already
   in `PORTABLE_FILES` since an earlier commit, but `BRIDGED` didn't know about them until this
   task — meaning `/api/reset` had been silently writing `[]` to their dead, post-cutover file paths
   and NOT actually wiping the DB-backed `accounts`/`transactions`/`statements_meta`/`import_batches`
   tables since the Task 3 cutover landed on this branch. This task's fix is the first real fix, not
   merely an extension.

**Verified at production scale (2026-08-08 dress rehearsal 3, a `/tmp` clone of a representative
book, live tree untouched and read-only throughout):** the full money unit — 29 accounts, 1,816
transactions, 5 splits, 824 external refs, 2,562 audit events, 268 import batches, 87 statement
records, 3 merge-journal entries — absorbed via the real `ensure_migrated()` chain 0001→0008.
`net_worth()`/`balances()`/`net_worth_history()` computed on this migrated clone produced a
canonical-JSON payload **byte-identical** (sha256 match, both 3,154 bytes) to the SAME three calls
made by the pre-cutover (SHA `7be167c22c9b6762fb2def5f18642562d27c99d2`) service+store code running
against an identically-cloned, still-JSON-money copy of the same source data — the owner's own "bet
my real numbers on it" test, executed by machine. A live `/rewind` proof (edit one non-opening
transaction's `memo` inside a `locked_unit_of_work`-composed choke, `begin_turn`/`end_turn`,
`restore(mode="data")`) reverted the row to a byte-exact match of its pre-edit state, confirming the
opening-pointer protocol's row-level-image claim directly rather than by inference. Full mechanism
and evidence: `.superpowers/sdd/2026-08-07-storage-plan3-money-core/task-8-report.md`.

## Consequences

### Positive

- Round-trip truth is structural, not tested-in: `payload` cannot drift from "the row as it was
  absorbed/as it was last written" because every promoted column is *derived from* it, never an
  independent write target.
- The money core's referential-integrity invariants that matter (delete-while-referenced, cascade
  cleanup of embedded children) are now DB-enforced on both dialects, closing a class of bug a
  service-layer-only check can miss under concurrent writers.
- Money imports have a real undo story for the first time — a wrong bundle import is a normal
  `/rewind`-able mistake, not a manual-recovery incident.
- No dialect divergence: the schema that shipped is provably portable (Postgres CI-tested from the
  first money revision), unlike the STRICT-tables line it deviates from.

### Negative

- Every money row now carries its data twice on disk (once in `payload`, once promoted) — bigger
  rows, more bytes per table, in exchange for the round-trip guarantee. Not measured as a real
  problem at the money core's actual scale (low thousands of rows; see the rehearsal counts above),
  but a future Tier-1 table absorbing a much larger store should re-weigh this trade explicitly
  rather than copy the pattern by default.
- A future engineer writing a direct SQL query against a hazard column (`transactions.created_at`/
  `memo`/`tags`/`merged_from`, `statements_meta.batch_ids`) must remember the promoted column's
  NOT-NULL default can mean "unknown" as well as "empty" — `payload` is the only place that
  distinguishes them. This is documented at length in `backend/db/tables.py`'s own comments, not
  just here.
- `transaction_external_refs`'s `(book_id, source, ref)` index is deliberately **non-unique** — a
  legitimate consequence of the merge design this ADR's schema hosts, not a gap in this ADR; see the
  dedicated ADR `2026-08-07-external-ref-index-non-unique.md` for why a unique constraint there would
  have been wrong.

### Follow-ups

- Plan 4's remaining JsonStore-backed money-adjacent stores (positions, lots, grants, …) should
  re-derive — not assume — whether they need the same verbatim-payload treatment: only run the
  "does live data prove a missing-vs-empty distinction matters" check task 2's review ran here
  before defaulting to fully-promoted.
- `services/ledger.py`'s `resync_transfer_base_stamps` is a fail-open one-time boot block that
  touches the now-DB-backed `transactions` table but lives in a shared service module, not a
  `backend/scripts/migrate_*.py` file — flagged by task 7, not resolved by this ADR or that task;
  needs its own D6-shaped ruling. **RESOLVED (plan 3 task 9 security-gate fix wave):** deleted
  outright along with its `main.py` boot block — the DB `TransactionStore` no longer nulls a
  transfer's `base_amount`, so the drift it healed can no longer be produced.

## Alternatives considered

- **Keep `transactions` fully promoted, no payload column** (task 2's original brief) — rejected:
  live data proves absence and emptiness are different historical facts on real rows, and a
  promoted-only schema cannot represent that difference without inventing one.
- **SQLite `STRICT` tables** (the design spec's original Tier-1 line) — rejected: dialect-specific
  DDL, contradicts the program's own no-divergence discipline; typed columns + portable
  `CheckConstraint`s deliver equivalent integrity.
- **Keep `_COMPANIONS` populated with an `accounts.json ↔ transactions.json`-shaped entry even after
  the DB cutover** — rejected: nothing left for a table-name pairing map to do once row-level
  `write_log` images exist; would be dead weight pretending to be a safety mechanism.
- **A real FK on `opening_txn_id`/`merged_into`** — rejected in both cases for the same reason: a
  circular reference the services layer already owns correctly (the dangling-RAISES-but-repairable
  contract; the merge/undo transient-pointer window) — a DB constraint would either block a legal
  transient state or require deferred-constraint machinery not worth the cross-dialect complexity.
- **Extend the reference-data no-hook ruling to money bundle imports** — rejected: a self-hoster's
  real ledger is not an admin-only reference cache; an unrecoverable wrong-import would be a genuine
  regression from the JSON era, where the whole-file blob snapshot already made every import
  undoable.

## Agent provenance

- **Decided by:** Claude (implementer agents across storage plan 3 tasks 2, 3, 4, 6; recorded by the
  task 8 implementer agent, which additionally ran the production-scale rehearsal cited above as
  independent verification of this ADR's claims). Lane/branch: `lane/storage-plan1` (worktree
  `storage-plan1`).
- **Alternatives weighed:** fully-promoted `transactions` was rejected against live-data evidence of
  genuine key absence; `STRICT` tables were rejected against the program's own dialect-portability
  rule; FKs on the two circular-reference columns were rejected as the wrong tool for an invariant
  services already own; extending the reference-data no-hook ruling to money was rejected as an
  undo-story regression from the JSON era.
- **Driving artifacts:** `docs/superpowers/specs/2026-08-05-storage-engine-sqlite-postgres-design.md`
  (D4/D5, the Addendum's D5/D6 rulings); `docs/superpowers/plans/2026-08-07-storage-plan3-money-core.md`
  (Global Constraints — the no-STRICT ruling recorded at plan level); task 2/3/4/6 reports and
  their review packages (`.superpowers/sdd/2026-08-07-storage-plan3-money-core/`); ADR
  `2026-08-01-referential-file-pairs-revert-together` (the file-era mechanism this ADR retires);
  ADR `2026-08-07-write-log-row-level-checkpoints` (the row-level mechanism that replaces it); ADR
  `2026-08-07-portable-export-bridges-db-backed-stores` (the reference-data no-hook ruling this ADR
  deliberately does not extend to money); this task's own rehearsal 3 report
  (`.superpowers/sdd/2026-08-07-storage-plan3-money-core/task-8-report.md`).
- **Why this way (one line):** a schema whose promoted columns cannot tell "absent" from "empty" on
  real rows is lying about the data it holds, so `payload` stays the truth everywhere in this tier —
  and an invariant the service layer already enforces correctly (opening pointer, merge target)
  should stay a service invariant, not be duplicated as a DB constraint that can't express its
  legal transient states.

