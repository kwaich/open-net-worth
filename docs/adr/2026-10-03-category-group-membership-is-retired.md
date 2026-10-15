---
number: 2026-10-03-category-group-membership-is-retired
title: Category→group membership is retired; groups are envelope groups
status: Accepted
date: 2026-10-03
deciders: senior-engineer lane (Claude) + repo owner
---

# Category→group membership is retired; groups are envelope groups

## Context

The envelope ⊥ category full split (`.claude/rfcs/envelope-category-full-split.md`) made an
envelope place itself by `envelope.group_id` + `order`; the budget table stopped reading a
group's `categories` list. The RFC kept that list "transitionally … removal is a later
cleanup", so it kept being written and read — and kept two jobs alive that no longer made sense:

- **Income placement.** `income_view()` grouped income categories by the same membership lists,
  and the old Inflow manager created a group on demand. `categories._ensure_group` matched a
  group by id **or name** and *created one named after any unknown string*, which is how a
  group literally named `__income__` was persisted in a real book. Several readers then needed
  defensive rules to hide it (`_is_income_only_group`, the `income` flag on
  `GET /api/planning/groups`, a sentinel guard in the money-flow graph).
- **A second, display-only source of truth.** Move / create / merge / delete of a category or
  envelope each had to mirror the change into the membership lists (`_relocate_in_groups_store`,
  `_place_key_in_group`, `_fold_groups`, the delete scrub) — extra writes inside money-adjacent
  chokes that no budgeting math read.

## Decision

We will remove category→group membership entirely and keep the group entities as the budget's
**envelope groups**.

- A group is `{id, name, collapsed, order, is_others}` — a heading that holds envelopes through
  `envelope.group_id`. The store is `EnvelopeGroupsStore` (`backend/db/envelope_groups_store.py`)
  over the table `envelope_groups` (renamed from `category_groups`). REST paths
  `/api/planning/groups*` and AI tool names are unchanged (tool names are FINAL).
- A category belongs to no group. `create_category`, rename/merge and delete never touch the
  groups table; `create_category(group=)`, `POST /api/categories {group}`, the
  `create_categories` tool's `group`, and the `category_create` budget verb's `group_id` are
  removed (the API and verb REFUSE it rather than ignore it). Creating a category can never
  create a group.
- A group is created only through `group_create` (`POST /api/planning/groups`), empty, with a
  non-blank name that is neither one of the app's own (`Commitments`, `Credit Card Payments`)
  nor a `__x__` sentinel. Envelopes are placed with `move_category`/`envelope_create`.
  `move_category` refuses an income category (income has no envelope and no group).
  `group_delete` moves the group's envelopes to Others (atomically with the delete).
- Income is flat: `income_view` returns `categories: [...]` (no `groups`), the reports rollup's
  income rows carry no group, and the money-flow graph links income categories straight to the
  total — the full graph is always four columns (income → total → spending groups / Saved →
  spending categories); compact stays three. This amends the five-column graph in ADR
  `2026-10-01-money-flow-is-a-server-built-svg-sankey`.
- The first-boot seed no longer reads membership: `ensure_builtins_seeded` seeds the starter
  groups as empty headings and `ensure_envelopes_migrated` places each starter envelope by
  `models.category_defs.STARTER_GROUPS` (a name → starter-category table), anything else in
  Others.
- The portable bundle keeps its entry name `category_groups.json` (the export contract — renaming
  it would orphan every existing backup); its rows are envelope groups, and an older bundle's
  `categories` / `envelope_ids` keys are dropped on import (`portable_bridge._envelope_groups_bridge`).

### Migration (`202610031200_envelope_groups_not_category_groups`, idempotent)

1. Rename `category_groups` → `envelope_groups` (and its `book_id/seq` index); `write_log`
   before-images of the old table follow the rename so a retained checkpoint still restores.
2. Delete a group iff ALL hold, evaluated on the membership lists *before* they are stripped:
   no envelope (live or archived) names it as `group_id`; it is not `is_others`; its name is not
   `Commitments` / `Credit Card Payments`; and it is a sentinel (id or name matching `^__.+__$`,
   e.g. `__income__`) **or** income-only (its list names ≥1 live income category and no live
   non-income category). A user-created empty spending group is kept; a group an envelope uses is
   never deleted.
3. Strip `categories` / `envelope_ids` from every surviving group payload.

The downgrade renames the table back; deleted groups and stripped lists are not restored (the
pre-migration backup is the recovery path).

## Consequences

### Positive
- One placement authority (`envelope.group_id`); category lifecycle ops lose their group writes
  and a whole class of "orphaned from every group" invariants.
- A group can only be created deliberately; a sentinel name can no longer reach storage.
- The money-flow graph and `income_view` are simpler, with no income-group heuristics.

### Negative
- A destructive data migration (it deletes sentinel / income-only groups). Narrow by
  construction, but not reversible without the pre-migration backup.
- Old exports with `categories` are accepted but lossy by design (the lists are dropped).
- `income_view`'s wire shape changed (`groups[].cats` → `categories`); its only consumer is the
  server-side reports rollup.

### Follow-ups
- The bundle entry `category_groups.json` could be renamed with an import alias in a future
  export-format revision.
- `excluded_groups` in a book's 50/30/20 state may name a group this migration deleted; readers
  already ignore ids that no longer exist.

## Alternatives considered
- Keep `categories` as a display-only list — rejected: no reader needed it, and it kept the
  create-by-name group leak alive.
- Delete every empty, envelope-less group — rejected: a user's own empty spending group is a
  legitimate, fillable heading (`test_empty_spending_group_still_renders_in_budget_table`).
- Keep the table name `category_groups` and only drop the field — rejected: the name is the
  misleading part; the rename is one cheap, mechanical migration.
- Rename the bundle file too — rejected for now: it would reject every existing export.

## Agent provenance

- **Decided by:** senior-engineer lane (Claude), scope approved by the repo owner.
- **Lane / branch:** isolated worktree off `main` (agent-a14dc118a5d79b7da).
- **Alternatives weighed:** display-only list vs. delete-all-empty vs. full retirement (chosen).
- **Driving artifacts:** `.claude/rfcs/envelope-category-full-split.md` ("removal is a later
  cleanup"); a persisted `__income__` group in the owner's book.
- **Why this way (one line):** the full split already made `envelope.group_id` the only
  placement authority, so the leftover membership list was pure liability.
