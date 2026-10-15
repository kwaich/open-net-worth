# Tool names are final — no aliases

- **Date:** 2026-08-25
- **Status:** Accepted
- **Generalises:** `2026-08-20-no-compat-aliases-single-user-instance` (which declined an
  alias for one rename) to every rename, past and future.
- **Supersedes:** the "Tool-name aliases (rename resilience)" section of
  `ai/tools/registry.py`, and CLAUDE.md's "**Renaming a tool requires an alias**".

## Context

A tool name is persisted data. It lives in the `automations` table, in a skill's
`allowed-tools` frontmatter, in backups and exports, and in in-flight transcripts. The
alias mechanism existed because of a real incident (2026-07-18): two envelope-split renames
left a 156-tool skill referencing two dead names, and because *any* missing tool marked the
whole skill degraded, the entire playbook went invisible to the model — total capability
loss, zero errors logged.

By 2026-08-25 there were **85 aliases**. They were free at the prompt (never offered to the
model) but not free in the codebase: an `aliases=` kwarg on `@tool`, a registry table, a
resolution map, `resolve_tool_name` threaded through ~15 call sites in `ai/`, `mcp/` and
`api/`, a `core` seam exporting it to `services/`, a `canonical_tool_names` helper on the
playbook read path, and a test pinning the alias table equal to the migration table.

`2026-08-20-no-compat-aliases-single-user-instance` had already declined to add the 86th, on
the grounds that this is a single-operator self-hosted instance: the owner is the only caller
of these names and updates their own references by hand. That reasoning does not stop at one
rename.

### What the aliases were actually load-bearing for

Measured against the owner's data, not assumed:

| Store | Alias references |
|---|---|
| `automations` (27 rows) | 0 |
| `skills_state` (8 rows) | 0 |
| tool schemas offered to the model | 0 |
| on-disk `skills/*/SKILL.md` | **155 across 3 skills** |

So the alias table was carrying exactly one thing: three skill files that had never been
migrated. `backend/scripts/migrate_tool_names.py` — which already existed, with a dry run and
a timestamped backup — rewrites all of them.

## Decision

**Delete every alias. A tool name is final; renaming one is a data migration.**

- `_LEGACY_TOOL_ALIASES`, `_ALIASES`, the `aliases=` kwarg, `Tool.aliases`,
  `resolve_tool_name` and `tool_aliases` are gone from the registry.
- `core/tool_registry_seam`'s alias provider, `resolve_tool_name` and `resolve_tool_names` are
  gone; `services/_playbook.canonical_tool_names` with them. A stored name is judged exactly
  as written.
- All 66 `@tool(aliases=…)` declarations are removed.
- `backend/scripts/migrate_tool_names.py`'s `RENAMES` is now the **sole** record of every
  rename. A rename is not done until it has an entry there and the script has been run.

An un-migrated name is an unknown tool and says so. That is the honest outcome, and it is
loud rather than quiet — which is the opposite of the 2026-07-18 incident's failure mode.

## The migration got one thing wrong, and this exposed it

`migrate_skill_md` rewrote only the frontmatter. A skill's **body** is instructions the model
reads and acts on, so a dead name there is a broken instruction too — and the alias was
silently covering it. Found live: `budget-review`'s frontmatter correctly said
`set_spending_limits` while its step 4 still read *"propose a revised set of caps via
update_spending_budgets"*.

The script now rewrites body prose as well, matched on a word boundary and only against
`RENAMES`, so ordinary prose is never touched — every replacement is a name that is now
genuinely unknown.

## Consequences

- Deleting a name is now loud. That is the point.
- The owner must run `migrate_tool_names --apply` against their live data dir once. Until
  then, `monthly-close` (149 refs), `budget-review` (5) and
  `meridian-momo-statement-import-deduplication` (6) reference dead names.
- An old backup or export restored later carries old names; re-run the script after restoring.
- A transcript recorded before a rename renders its old name as a label. Display only —
  `describe_tool_call` types the raw name rather than failing.

## Agent provenance

The owner's instruction was "we can't support old aliases anymore… no old, legacy logic at
all". The measurement above is what made it safe to act on: the aliases looked load-bearing
and were, for exactly three files that a script already knew how to fix. Checking the real
data before deleting is what turned a risky call into a bounded one — and it is also what
surfaced the body-prose gap, which the alias had been hiding rather than solving.
