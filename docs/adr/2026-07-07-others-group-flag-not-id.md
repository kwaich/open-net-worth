# 2026-07-07 — The "Others" catch-all group is identified by a flag, not a fixed id

**Status:** accepted (owner directive, 2026-07-07); one-shot migration deleted 2026-07-08 after
confirming it ran successfully — see Consequences
**Where:** `backend/db/category_groups_store.py` (`ensure_others`),
`backend/services/planning.py` (`_envelope_group_buckets`, `_grouped_rows`, `_op_group_delete`),
`tests/test_planning_stores.py`, `tests/test_planning_service.py`,
`tests/test_budgeting_acceptance.py`, `tests/test_envelope_flip.py`,
`tests/test_envelope_migration.py`

## Decision

The Planning category-groups store used to mint the default catch-all group with a fixed
literal id, `"others"` (`OTHERS_GROUP_ID`), and every call site that needed "is this the
catch-all?" compared a group's id against that constant. This was the ONLY group in the store
with a non-opaque id — every user-created group gets `new_id("grp")`, matching the opaque-id
convention already enforced for categories (`cat_<hex>`, ADR `2026-07-05-category-opaque-ids.md`).

That inconsistency caused a real bug: a group's `name` is freely user-editable (rename is
unguarded — only delete is), so the catch-all's display name had silently drifted from
"Others" to "Holding" on the live book. The id-based checks kept working (id equality doesn't
care about the name), but the drift showed the underlying design smell — an id was being
used as a stand-in for "is this special," which is exactly what a dedicated flag is for.

Fix: the catch-all group now carries `"is_others": true|false`. `ensure_others()` looks for a
flagged group first; if none exists it mints one with a normal opaque `grp-` id, identical in
shape to every other group. No code anywhere compares a group's id against a fixed sentinel.

**No legacy-id fallback lives in the request path.** `ensure_others()` does not special-case
the old literal id at all — that would just be swapping one hardcoded string check for another.
Instead, a one-shot, idempotent upgrade step (mirrors `services.envelopes.ensure_envelopes_migrated`)
ran once at boot from `main.py`: it found the group with the old literal id `"others"`, minted
it a normal opaque id (dropping the sentinel entirely, matching how a freshly seeded category
gets a real id from day one — not just flagging the old row in place), flagged it `is_others`,
and re-keyed every envelope whose `group_id` still pointed at `"others"` to the new id. A book
that never had the legacy row was a no-op.

Verified against the live book: boot log showed
`category groups: migrated the legacy 'others' catch-all to id=grp_eef97840f669 (name='Holding');
re-keyed 4 envelope(s)`, run through the normal `JsonStore` write path (checkpoint auto-snapshot
fired), preserving the group's user-set name "Holding" and its full `categories`/`envelope_ids`
lists, with the literal string `"others"` left nowhere in the data afterward. Owner directive,
2026-07-08: since this is a single-owner instance with no other installs to carry forward
compatibility for, the migration method and its boot wiring were deleted once confirmed
applied — see Consequences.

## Why

An id is an identity, not a property. "Is this the default catch-all" is a property of a group,
so it belongs in a field on the group, not encoded into which literal string was chosen as its
id. Conflating the two meant the display name (something users are explicitly allowed to
change) and the catch-all invariant (something the code depends on) were accidentally coupled
through a value that looked immutable but wasn't being protected as one.

The codebase already had a working precedent for the "flag, not id" pattern one line away: the
auto-managed Commitments group is also minted with a normal opaque id and identified for
display purposes without a fixed sentinel (`services/planning.py`'s `_grouped_rows`, which
notes "the client never has to guess a magic id"). The catch-all should have followed the same
rule from the start.

The migration was a boot-time upgrade rather than a manual one-shot script (contrast
`migrate_envelope_id_native.py`) because it was low-risk and fully self-contained: mint the
catch-all a normal opaque id (dropping the old literal `"others"` sentinel), flag it, and
re-key the handful of envelopes whose `group_id` still pointed at the old value — no money
math, no cross-store ambiguity, nothing an operator needed to review before it ran.

## Consequences

- The "Others" group's display name can be renamed freely (as it always could) with zero risk
  of breaking the delete-guard, the empty-group-hides-in-the-table rule, or the orphan-category
  fallback bucket — all three now check the flag, never the name or a fixed id.
- Any code that still assumed the catch-all's id was literally `"others"` (a handful of test
  fixtures) had to switch to resolving it dynamically via `ensure_others()["id"]` — done in the
  same change; the rest of the fixtures used `"others"` merely as an arbitrary-but-valid
  placeholder id and needed no change.
- A fresh install's catch-all group id is now a normal `grp-<hex>` value instead of the literal
  `"others"` — nothing in the codebase depends on the specific value anymore, only the flag.
- Verified: full backend suite green (8697 passed), `ruff` clean, import-linter 5/5 contracts
  kept, and the migration confirmed against the live book via the running dev server's
  `--reload` picking up the boot-path change.
- **2026-07-08:** `CategoryGroupsStore.migrate_legacy_others_id()`, its `main.py` boot wiring,
  and its dedicated tests were deleted — the one real book is confirmed migrated (no group
  anywhere carries the literal id `"others"` any more) and there are no other installs to
  upgrade, so the one-shot scaffolding was pure dead weight once it had run. `ensure_others()`
  and the flag-based checks it backs are permanent; only the migration path was removed.

## Agent provenance

Found while explaining to the owner why a Planning group was unexpectedly named "Holding"
(root cause: an unguarded rename on the reserved catch-all). The owner pushed back hard on the
underlying fixed-id design once it surfaced, and directed a real data migration with zero
backward-compatibility branching left in the runtime code path — this ADR and the accompanying
diff implement that directive.
