---
title: Planning write chokes compose through locked_unit_of_work — envelopes+budget_assignments, categories+category_groups+transactions; the create_category atomicity fix and its find_by_id conn= root cause; the residual category-delete/automations hybrid-period window
status: Accepted
date: 2026-08-08
deciders: Claude (implementer agents, storage plan 4a tasks 5/8 — choke seam, rehearsal), repo owner (D7 hybrid-period ruling)
---

# 2026-08-08. Planning write chokes compose through `locked_unit_of_work`

## Context

`JsonStore` never had cross-store atomicity. Plan 3 closed this for the money core's own
multi-store chokes via `backend.db.engine.locked_unit_of_work(*tables)` (ADR
`2026-08-08-money-core-choke-points-become-one-transaction.md`). Planning's 12 tables (now
DB-backed per the companion ADR, `2026-08-08-planning-stores-on-payload-tables.md`) had the
IDENTICAL gap on their OWN multi-table writes — an envelope merge/delete moving money AND
archiving a row, a category create/rename/delete touching `categories` +
`category_groups` (+ `transactions` on a re-key), all as separate, non-atomic writes that a
crash mid-sequence could leave half-done. Two things were already true before this task
started:

- `locked_unit_of_work` already existed, proven deadlock-free by money's own two-thread
  opposite-order test (`tests/db/test_choke_seam.py`) — planning needed to REUSE it, not
  reinvent it.
- The design spec's D7 hybrid-period ruling: `automations` stays a `JsonStore` file this wave
  (no service layer or DB table exists for it yet), so any planning write that also updates an
  automation's stored instruction text cannot be composed into the SAME DB transaction as the
  planning tables it accompanies — there is, structurally, nothing on the DB side to compose
  it WITH yet.

## Decision

We will:

1. **`services.envelopes` composes `(budget_assignments, envelopes)`** in `merge_envelope`,
   `delete_envelope`, `force_delete_card_payment_envelope`, `force_delete_commitment_envelope`,
   `add_category`, and the sole-member-merge branch of `on_category_renamed` / the archive
   branch of `on_category_deleted` — every one of these moves assigned money (zero it out or
   fold it into another envelope) AND writes the envelope row itself, previously as two
   separate transactions. `_move_assigned` gained an additive `*, conn: Connection | None =
   None`, forwarding into both `assignments_store.set_assigned(...)` calls it makes;
   `EnvelopeStore.update_envelope`/`.bulk_patch` were already (or became) `conn=`-capable.
   `split_category` and `reorder_categories` were swept and found genuinely single-table (no
   money move) — deliberately left unwrapped, documented inline, rather than taking an
   uncontended lock for zero atomicity benefit.
2. **`services.categories` composes `(categories, category_groups, transactions)`** in
   `rename_category`'s merge branch, `delete_category`, and — found only by re-checking
   coordinator review, not the original sweep — `create_category`'s `kind in ("expense",
   "income")` branch (the only branch that ever touches `category_groups`;
   `transfer`/`adjustment`/`investment` categories never do and keep a plain, unwrapped
   `store.insert(entry)`). `_fold_transactions`/`_fold_groups` gained an additive `*, conn=None`,
   forwarding into `transaction_store.replace_all(txns, conn=conn)` (already `conn=`-capable
   from Plan 3) and `groups_store.update_group(..., conn=conn)`. `_op_move_category`
   (`services/planning.py` — found by the mandated sweep of every `_op_*` in
   `apply_budget_change`, outside the brief's originally-named files) composes
   `(category_groups, envelopes)` the same way.
3. **`create_category`'s fix exposed a second, deeper, latent bug: `PayloadTableStore.find_by_id`
   had no `conn=` parameter at all.** Wrapping `create_category`'s writes in
   `locked_unit_of_work` alone was not sufficient — a happy-path sanity test failed silently
   (category created, never actually landed in its group, no exception). Root cause:
   `EnvelopeStore.update_envelope`/`CategoryGroupsStore.update_group` each do their OWN
   existence pre-check (`if self.find_by_id(id) is None: return None`) before delegating to
   `update_by_id`, and `find_by_id` — unlike every other mutate on the class — always opened a
   FRESH, separate connection, blind to the caller's own open transaction.
   `create_category`'s new composition is the FIRST caller in the codebase to insert a row and
   then immediately update that SAME row within one open transaction (every one of the other
   ten compositions only ever updates rows that already existed before the transaction opened,
   so the pre-check's fresh-connection read always happened to see them fine — this is why the
   gap was never caught until now). Fixed at the root, not the call site:
   `PayloadTableStore.find_by_id` gained an additive `*, conn: Connection | None = None` — when
   given, it reads through the caller's own transaction via the existing `self._row(conn,
   id_val)` helper every other mutate already used; omitting `conn` remains byte-identical to
   before. This closes the SAME latent gap for any FUTURE choke that inserts-then-updates one
   row in a single transaction, not just `create_category`.
4. **The residual category-delete/automations atomicity window is accepted, documented, and
   tracked — not silently left implicit.** `delete_category`'s automations-instruction rewrite
   (renaming a deleted category out of any stored Automation trigger/condition text) already
   ran, in the pre-existing code, AFTER the category/group/transaction writes; composing those
   three into one `locked_unit_of_work` did not change that relative order, but it DID sharpen
   what the window means: the category re-key + group scrub + transaction fold now land
   atomically as one commit, and the automations rewrite that follows is a SEPARATE,
   sequential, best-effort step against a still-`JsonStore` file — a crash between the two
   leaves a committed category re-key with a stale automation reference, self-healing only on
   the next successful run, never silently corrupting the category data itself. Recorded
   inline at the `delete_category` call site and tracked in `docs/launch/BOARD.md`'s inbox:
   *"planning cat-delete: fold the automation-instruction rewrite into the category re-key's
   `locked_unit_of_work` once automations is DB-backed (Wave D) — residual hybrid-period
   window from plan 4a T5 — 2026-08-08."* The envelope hook
   (`envelopes_service.on_category_deleted`) stays sequenced BEFORE the
   categories/category_groups/transactions transaction (its pre-existing relative position) —
   a crash between the two leaves a harmless, self-healing "zombie" category row
   (already-unreferenced by the cleaned-up envelope), the strictly safer of the two possible
   orderings compared to the category row disappearing first.

**Verified at production scale (2026-08-08 dress rehearsal 4A):** this task's own `/rewind`
proof is a direct, real-data exercise of item 1's composition mechanism and the shared
`write_log` seam item 3 depends on. On the migrated clone, `services.planning.apply_budget_change`
(`type: "assign"`) set a real `(month, category)` and `services.commitments.update_commitment`
edited a real commitment's `note`, both inside one `begin_turn`/`end_turn` pair — TWO different
planning tables (`budget_assignments`, `commitments`) written by two different service-layer
chokes in one checkpoint turn. Both rows' full DB row images were captured independently (a raw
`SELECT`, not the checkpoint's own bookkeeping) before the edit, confirmed changed after the
edit, then reverted via `ai.checkpoint.restore(checkpoint_id, mode="data")` — both rows
byte-identical (sha256 match) to their pre-edit capture. This proves the SAME row-level
`write_log` mechanism the money core's rehearsal proved (ADR
`2026-08-08-money-core-choke-points-become-one-transaction.md`) now covers planning writes
too, with no planning-specific gap beyond the already-documented `corrections` exclusion (the
companion ADR). Full mechanism and evidence:
`.superpowers/sdd/2026-08-08-storage-plan4a-planning/task-8-report.md`.

## Consequences

### Positive

- Every planning choke that genuinely writes ≥2 wave tables now shares the money core's exact
  atomicity guarantee — proven by the SAME class of two-thread deadlock-free test
  (`tests/services/test_planning_choke_atomicity.py`), not a new, weaker mechanism.
- The `find_by_id(conn=)` fix is a real hardening of the shared `PayloadTableStore` base, not
  a one-off patch — any future choke that inserts-then-updates one row in a single transaction
  inherits the fix automatically.
- The residual automations window is now a documented, tracked, self-healing gap with a named
  closure condition (Wave D DB-backing automations) rather than an implicit assumption nobody
  wrote down.

### Negative

- `locked_unit_of_work`'s global-alphabetical-lock-ordering contract remains a discipline, not
  a compiler-enforced rule (same caveat the money-core ADR already records) — a future planning
  choke that composes writes WITHOUT going through this seam can silently reintroduce the
  deadlock class it exists to close.
- The category-delete/automations window is a REAL, if narrow, inconsistency surface: between
  the category-re-key commit and the automations rewrite, a reader of `automations.json` would
  see a stale category reference. Accepted as a hybrid-period cost, not eliminated.
- `split_category`/`reorder_categories` staying unwrapped means a future change to either that
  ADDS a second-table write would need to be caught by the same kind of sweep this task ran —
  nothing structurally forces that re-check.

### Follow-ups

- Wave D (once `automations` is DB-backed): fold the automation-instruction rewrite into
  `delete_category`'s (and `rename_category`'s merge branch's) `locked_unit_of_work` block,
  closing the residual window named above. Tracked in `docs/launch/BOARD.md`.
- Any future multi-table planning write should be checked for `locked_unit_of_work`
  composition at review time, the same standing item the money-core ADR already names for its
  own tier.

## Alternatives considered

- **Compose the automations rewrite into the same transaction by giving `automations` a
  temporary DB shadow table just for this rewrite** — rejected: a throwaway shadow table for
  one write path is more machinery than the documented, self-healing window it would close,
  and would need its own migration/sync story the moment real Wave D work superseded it.
- **Fix `create_category` by reordering the writes (group first, category second) instead of
  composing a transaction** — rejected: reordering only moves WHICH half can be orphaned by a
  crash, it does not remove the non-atomicity; `locked_unit_of_work` removes the whole class.
- **Fix the `find_by_id` gap narrowly, only inside `create_category`'s own call path (a local
  workaround instead of a base-class change)** — rejected: the gap is in the shared
  `PayloadTableStore` primitive every planning (and money) store inherits from; fixing it there
  once is strictly safer than hoping every future insert-then-update caller remembers a
  local workaround.
- **Silently leave the category-delete/automations window undocumented, treating it as "close
  enough" since automations is a display/trigger concern, not money** — rejected: an
  undocumented atomicity gap is indistinguishable from an unnoticed one on the next person's
  read of the code; writing it down + tracking it in `BOARD.md` costs one comment and one bug
  entry, and closes the exact silent-scope-creep failure mode this program's own review
  process exists to catch.

## Agent provenance

- **Decided by:** Claude (implementer agent, storage plan 4a task 5 — original composition +
  the coordinator-re-review fix round that found `create_category`/`find_by_id`; recorded by
  the task 8 implementer agent, which independently re-verified the choke seam and restore
  mechanism via the production-scale rehearsal cited above). Lane/branch:
  `lane/storage-plan1` (worktree `storage-plan1`).
- **Alternatives weighed:** a temporary DB shadow table for automations was rejected as more
  machinery than the window it would close; reordering `create_category`'s writes was rejected
  as treating a symptom instead of the underlying non-atomicity; a local `find_by_id` workaround
  was rejected in favor of the shared base-class fix; leaving the automations window
  undocumented was rejected as indistinguishable from not having found it.
- **Driving artifacts:** ADR `2026-08-08-money-core-choke-points-become-one-transaction.md`
  (the `locked_unit_of_work` mechanism and lock-ordering discipline this ADR reuses verbatim);
  ADR `2026-08-08-planning-stores-on-payload-tables.md` (the schema this choke seam writes
  into); `docs/superpowers/specs/2026-08-05-storage-engine-sqlite-postgres-design.md` (D7, the
  hybrid-period ruling); `docs/launch/BOARD.md` (the tracked follow-up entry); plan 4a task 5
  report + its fix-round-1 section
  (`.superpowers/sdd/2026-08-08-storage-plan4a-planning/task-5-report.md`); this task's own
  rehearsal report (`.superpowers/sdd/2026-08-08-storage-plan4a-planning/task-8-report.md`).
- **Why this way (one line):** reusing the money core's exact atomicity primitive for planning's
  own multi-table writes — rather than inventing a parallel mechanism — means the two tiers
  share one proven-deadlock-free seam and one documented discipline, and the one place planning
  genuinely cannot compose yet (automations, still JsonStore) is named and tracked instead of
  quietly absorbed into "good enough."
