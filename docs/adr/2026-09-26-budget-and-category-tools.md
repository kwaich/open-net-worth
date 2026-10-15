---
title: Lucius's budget and category tools read labelled rows from the budget's own stores, and none lies about its effect
status: Accepted
date: 2026-09-26
deciders: orchestrating agent (Claude) + repo owner
---

# 2026-09-26. Budget and category tools: labelled reads, honest names, no dead ends

## Context

The owner asked for every budget and category tool to be reviewed and fixed end to end
against the tool conventions. The review (41 tools across `planning`, `envelopes`,
`categories`, `budgets`, `budget_engines`) found three dead ends and a stale store, beyond the
naming work the 2026-07-19 lanes already finished (`tool_inventory --lint` reports 0 defects):

- `list_envelopes` returned raw envelope rows: `group_id`/`category_ids` with no names (ADR
  `2026-07-20-resolve-ids-and-drop-dead-profile-fields` requires the label beside the id), and
  `target`/`note` copies left on rows from before ADR `2026-07-05-envelope-id-native` moved them
  to `profile.category_targets`/`category_notes`. On the owner's book 14 of 46 envelopes'
  copies disagreed with the real target (one read 23,083.76 against a real 7,653), and one
  live note existed only in such a copy.
- Notes were readable only through `get_envelope_note`, one id per call, while
  `list_envelopes` told the model notes were on `get_budget_month` rows — they weren't.
- Six descriptions said "after the user confirms, apply via `apply_budget_change`". No such
  tool exists since ADR `2026-08-25-one-confirm-mechanism-for-every-tool`: the confirm card
  applies itself. Its dead input schema was still in `ai/tools/planning.py`.
- `set_spending_limit_rollover_start` wrote `budget_config.rollover_start` — also Zero-Based's
  carry floor and Ready to Assign's zero line — under a spending-limit name, with none of the
  fresh start's safeguards.

## Decision

We will serve the budget reads from `services.budget_catalog`, which reads every per-envelope
setting from the store the budget itself reads and puts the name beside every id:

- `list_envelopes` rows are the whole envelope — `group {id, name}`, `linked_categories [{id,
  label}]`, `zero_based_target`, `note`, `color`, `managed_by_app`, and (once 50/30/20 is set
  up) `fifty_thirty_twenty {bucket, cap}` as the 50/30/20 screen resolves them — with combinable
  filters `envelope_ids` (which covers "get one envelope"), `group`, `category`, `search`,
  `has_target`, `include_archived`. The raw store fields are no longer shipped.
- `list_categories` rows add `envelope {id, name}` (null = spending in no envelope) with
  filters `kind`, `linked`, `envelope`, `search`.
- `get_budget_month` rows add `linked_categories` and `note` beside the existing fields.

We will delete every "apply via apply_budget_change" instruction and the dead schema, and
retire `set_spending_limit_rollover_start` (recorded in the rename ledger; a fresh start is the
Plan screen's).

## Consequences

### Positive
- One read answers "tell me about this envelope" with names, and never a stale target.
- No description sends the model to a tool that doesn't exist; no tool can move the budget's
  start under another name.

### Negative
- `list_envelopes`' row shape changed (not purely additive): a caller reading `category_ids`
  or `group_id` must read `linked_categories`/`group`. Tests were updated; no stored skill or
  eval read them.
- `list_envelopes` now computes the 50/30/20 month when that method is configured — a heavier
  read than the raw rows.

### Follow-ups
- Phase 2, renames per the conventions: `add_categories`→`create_categories` (rule 2's listed
  violator), `remove_categories`→`delete_categories`, `rename_envelope`+`set_envelope_note`→
  `update_envelopes`, `get_envelope_note` retired, `move_envelope_to_group`→
  `move_envelopes_to_group`, `link_category`/`unlink_category`→plural, `auto_assign`→
  `preview_auto_assign`; the three group tools' `categories` params → `envelope_ids`.
- Phase 3: the legacy spending-limit tools (no UI), savings goals named "targets", the 50/30/20
  draft's category-level proposal.
- Data: move stale row notes to `profile.category_notes` and drop the stale row copies (needs
  the owner's go — it writes their book).

## Alternatives considered
- **A `get_envelope` tool beside `list_envelopes`** — rejected: `list_envelopes(envelope_ids=[…])`
  is the same read; two tools would be two shapes for one concept.
- **Keep the raw rows and add labels beside them** — rejected: the raw rows carry the stale
  `target`, the one field that must not reach the model.
- **Relabel `set_spending_limit_rollover_start` honestly** — rejected: it would still be a second,
  unguarded way to move the budget's start; the fresh start is the guarded one.

## Agent provenance
- **Decided by:** orchestrating Claude agent, on the owner's "review them … and improve them e2e!
  make sure they are following other conventions we have".
- **Lane / branch:** `main` (owner's standing rule).
- **Alternatives weighed:** get-one tool vs filters; additive labels vs clean rows; relabel vs
  retire.
- **Driving artifacts:** the owner's book (14 stale targets, an orphaned note); ADRs 2026-07-05,
  2026-07-20, 2026-08-25; `docs/ai/TOOL_CONVENTIONS.md`.
- **Why this way (one line):** the model should read an envelope once, by name, from the same
  stores the budget computes from — and every tool should do what its name says.

## Amendment (2026-09-26): phase 2 landed, two items corrected

Renamed per the conventions (all in `backend/scripts/tool_renames.py`): `create_categories`,
`delete_categories` (`deletions`), `link_categories`/`unlink_categories` (`category_ids`),
`move_envelopes_to_group` (`envelope_ids`), `update_envelopes` (replacing `rename_envelope` and
`set_envelope_note`; the note now rides the budget choke as `envelope_note`, so one card and one
checkpoint cover an envelope's edits); `get_envelope_note` retired. Two planned items changed on
inspection: `auto_assign` keeps its name — under the one confirm mechanism approving its card
WRITES the assignments (and an unattended caller writes directly), so the name was right and its
READ_ONLY_TOOLS classification was the bug (now `mutates=("budget",)`); and
`create_envelope_group` keeps `categories`, which really are category keys.

## Amendment (2026-09-26): phase 3 landed

Retired, recorded in `RENAMES`: `set_spending_limits` and `set_spending_limit_rollovers`. The
flat per-category limits (`profile.monthly_budgets`) have no screen and feed no figure the user
sees: `get_spending_summary` reads the home budget method. A cap is the method's own: 50/30/20's
through `apply_budget_verb` (`set_caps`, `copy_targets_to_caps`), Zero-Based's through
`set_envelope_targets`. The store and `services.budgets` stay (the category universe still reads
them).

Renamed: the savings-goal tools are now `get_goals`, `get_goal_allocation`, `create_goal`,
`update_goal` and `delete_goal` (`goal_id`, results say `goal`). "Target" also named
Zero-Based's per-envelope target and the investment target mix. The module is
`ai/tools/goals.py`; the store, service and REST keep their "targets" names.

What forced it: a live chat asked Lucius to set 50/30/20 caps from the Zero-Based targets.
Lucius activated the `budget-review` starter skill, which told it to read the non-existent
`profile.monthly_budgets` and write through `set_spending_limits`. It never loaded a budget
tool, and it paired target amounts with envelope names by guesswork. Fixed with it:
- `budget-review` is rewritten per method. It loads the budget tools first, reads
  `list_envelopes`, and names `copy_targets_to_caps`. `investment-checkup` reads the target
  mix from `get_rebalance_plan`.
- `SkillStore.refresh_superseded_starters` (every boot) replaces an installed starter whose
  body still hashes to a superseded built-in body. An edited copy is never touched. Without it,
  every existing install keeps the body it was first seeded with.
- A repeated `activate_skill` answers with where the instructions are and to load the tools
  with `find_tools`, not a bare "already active" (the chat looped on it 8 times).
- The screen a turn comes from loads its tools (ADR `2026-09-26-a-screen-loads-its-own-tools`).

## Amendment (2026-09-26): `apply_budget_verb` is `apply_budget_operation`

The owner found "verb" meaningless ("Apply budget verb" in a chat step). The tool is now
`apply_budget_operation(engine_id, operation, params)`, and `list_budget_engines` reports
`engines[].operations`. Its result names each envelope it touched instead of returning the
whole stored id map (`budget_catalog.label_engine_result`). Its chat row is labelled from the
operation (`ai/tools/display.py`). Recorded in `RENAMES`; engine internals keep "verb".
