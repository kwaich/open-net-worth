---
title: No backward-compat aliases for renames — single-operator self-hosted instance
status: Accepted
date: 2026-08-20
deciders: owner
---

# No backward-compat aliases for renames — single-operator self-hosted instance

## Context

CLAUDE.md's stated convention is that renaming an AI tool requires a compatibility alias
(`@tool(aliases=(...))` + `registry._LEGACY_TOOL_ALIASES` + `migrate_tool_names.RENAMES`),
because "a tool name is persisted data — it lives in the automations table, skill
frontmatter, backups, and in-flight transcripts." That convention was written for a
deployment where the app might have multiple operators, shared exports/imports, or data
migrated between installs — situations where an old name could resurface from somewhere
the renaming change didn't touch.

This instance has exactly one operator (the owner, self-hosted). There is no other caller
who could hold a stale reference this app doesn't already control.

## Decision

The `signals` → `notification_log` rename (`get_signals` → `get_notifications`,
`mark_signals_read` → `mark_notifications_read`, revision 0044) is a **clean break, not an
aliased rename**: the old tool names are gone outright, with no alias table entry, no
`migrate_tool_names.RENAMES` entry, and no dual-read fallback anywhere in the rename.

This is the **standing exception** to the alias convention for this instance. A future
rename in this repo may point back to this ADR instead of re-litigating the same question,
as long as the premise holds: single operator, self-hosted, no shared/multi-instance
deployment.

## Consequences

- An in-flight chat transcript, a saved automation, or skill frontmatter that references
  `get_signals`/`mark_signals_read` by name will fail to resolve after this change, rather
  than silently continuing to work. This is acceptable because the owner is the only
  possible author of such a reference and can update it by hand — there is no other party
  who would be surprised by the break.
- If this instance's deployment model ever changes (multi-user, shared exports consumed by
  another instance, etc.), this exception no longer applies and the standard alias
  convention resumes.
