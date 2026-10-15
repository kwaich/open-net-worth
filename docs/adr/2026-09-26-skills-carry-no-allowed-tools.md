---
title: Skills carry no tool list — allowed-tools and disallowed-tools are dropped on read and never written
status: Accepted
date: 2026-09-26
deciders: orchestrating agent (Claude) + repo owner
---

# 2026-09-26. Skills carry no tool list

## Context

Skills stopped gating tools on 2026-07-18 (ADR `2026-07-18-remove-layered-ai-permission-system`).
The experimental Agent Skills `allowed-tools` field was kept anyway, "preserved but inert,
matching Codex", so imported and exported skills would round-trip it.

It was never inert to the model. `services.skills.render` → `db.skill_files.build_skill_md`
renders the whole SKILL.md, frontmatter included, as the text an activation injects, and
`list_skills` rows returned the field too. A seeded starter keeps whatever body it was first
seeded with (`seed_starters` only inserts missing ids), so 8 of the owner's 14 live skills
still listed tools from an older seed.

On 2026-09-26 (session `bff982fc`) Lucius, on GLM-5.3 Flash, activated `budget-review`. That
skill's `allowed-tools: get_spending_summary get_ledger_summary get_profile set_spending_limits`
listed four tools, and only `get_profile` was in its offer. The model's own reasoning shows it
took that as "Skill context says allowed-tools include get_spending_summary etc., but those
aren't in my actual function list". It concluded it had only `get_profile` and `activate_skill`,
though its offer held 15 tools, `find_tools` among them. It never loaded a budget tool, called
those two tools about 20 times, and guessed which envelope names matched which amounts.

## Decision

A skill carries no tool list, in either direction:

- `parse_skill_md` callers never read `allowed-tools` into a record (`_read_skill_folder`,
  upload), `build_skill_md` never writes it, and frontmatter validation ignores its shape. An
  imported file that lists tools is accepted and the field is discarded.
- Claude Code's `disallowed-tools` gets the same treatment. It is out of the Claude-extension
  allowlist (`_CLAUDE_RECORD_TO_FRONTMATTER` / `_CLAUDE_LIST_FIELDS`), so it is dropped on read,
  never written, and absent from `claude` records. It is equally unenforced, and it tells a model
  what it may not use.
- `allowed_tools` is not a skill field. It is gone from `EDITABLE_FIELDS`, the create record,
  `SkillCreate`, the Skills editor (along with the Disallowed tools box — the whole "Tool metadata"
  panel), the frontend `Skill` type and the skill confirm-card rows.
- A legacy file keeps its line only until its next write. The owner's 8 live files had it
  stripped directly, frontmatter only, bodies byte-identical.

Agents' and automations' `allowed_tools` are unchanged. That field is an explicit offer
(`_skill_scope.set_offer_scope`), not skill metadata.

## Consequences

### Positive
- What a skill injects contains no tool names except the ones its instructions use. The
  model's tool list is its offer plus `find_tools`, with nothing contradicting it.
- One fewer field that no code enforces.

### Negative
- Export to a host that honors `allowed-tools` / `disallowed-tools` (Claude Code) no longer
  carries a skill's tool lists, so the user re-adds them there.

## Alternatives considered
- **Keep storing it, strip it only from the render** — rejected. It keeps a field nothing
  reads, the editor keeps showing it, and every new renderer must remember to strip it.
- **Enforce it again as a per-skill offer** — rejected. The 2026-07-18 removal stands, and the
  stale skill in this chat would have been enforced too.

## Agent provenance
- **Decided by:** Claude (Opus 5.5) agent, on the owner's "drop that allowed tools from skills",
  then "remove that completely" for `disallowed-tools`.
- **Lane / branch:** `main` (owner's standing rule), alongside the
  `2026-09-26-a-screen-loads-its-own-tools` session fixing the same chat.
- **Driving artifacts:** session `bff982fc`; `tests/test_skill_tools.py::test_legacy_tool_list_lines_never_reach_the_model_and_are_dropped_on_rewrite`.
- **Why this way (one line):** the rendered skill is the model's context, and a tool list in it
  reads as the model's whole toolset.
