---
status: Superseded
date: 2026-08-09
decision-makers: [maintainer, Codex]
---

# Host owns the skill invocation lifecycle

> Superseded by [2026-08-21-activated-skills-stay-active-until-removed](2026-08-21-activated-skills-stay-active-until-removed.md).

## Context

Lucius treated an invoked skill as an ordinary `run_skill` JSON result. Explicit slash invocation was
an imperative prompt asking the model to call that tool. Generic result decay and context windowing
could later discard the body. The format was portable, but activation and adherence were probabilistic.

Codex instead expands explicit invocations in the host, persists rendered skill content as one
conversation message, deduplicates identical reinvocations, and carries the newest invocation of each
skill through compaction under fixed budgets.

## Decision

The AI runtime, not the model and not the skill tool result, owns activation. Every direct or automatic
invocation is rendered once and persisted as a provider-compatible hidden transcript message carrying
server-only identity and content-hash metadata. The same runtime projects that message into provider
context, hides it from chat rehydration, deduplicates identical content, and reattaches the newest
invocation of each skill after folding under 5,000-token-per-skill and 25,000-token-total budgets.

Invocation fields and argument substitutions are stored directly in `SKILL.md`.
The rendered instructions persist; skill activation never changes the ordinary tool offer or policy.

## Consequences

- `/skill` becomes deterministic and costs no preliminary model round.
- An activated skill is durable session instruction context rather than shrinkable tool data.
- Editing a skill does not rewrite instructions already invoked in a session; a later invocation with
  different rendered bytes adds a new copy.
- Hidden invocation messages become part of transcript accounting and must be handled explicitly by
  windowing, folding, rehydration, checkpoints, and cost-turn joins.
- The existing skill allowlist/deny behavior is retired for inline invocation.

## Amendment (2026-08-21) — Codex structured selection and instruction-only skills

Current Codex uses ordered structured skill selections, loads each selected `SKILL.md`, and injects
the full instructions as user-role context before the model turn. Open Net Worth now uses an ordered
`ChatRequest.skills` list for the same deterministic host path and supports multiple selections.
Legacy skill tool-list and guardrail metadata is inert and cannot alter ordinary tool discovery,
dispatch, MCP policy, or security gates.

The two activation sources remain intentionally different at the boundary: an explicit picker/slash
selection is host-expanded before provider call one, while natural-language matching remains a model
decision expressed through the real `activate_skill` tool. They converge immediately afterward on the
same durable hidden invocation record and the same public lifecycle events. The chat renders those as
dedicated `Loading <name> skill` → `Loaded <name> skill` activity rows (or a truthful failure), records
`user` versus `model` provenance, supports multiple rows in order, and reconstructs loaded rows from
the hidden transcript metadata after reload. The provider transcript retains model tool calls; the UI
does not duplicate `activate_skill` as a generic tool pill.

## Agent provenance

- Agent: Codex, primary implementation agent
- Lane: `lane/SKILL1-claude-native-runtime`

## Security hardening addendum (2026-08-09 final remediation)

- Queued invocations persist only `{skill_id, arguments, source}`. The host refetches and rechecks
  existence, enabled state, and the appropriate invocation flag while holding the session lock.
- Visible and hidden injected entries move through exact-object claim → durable save → ack. Any
  setup/render/save/cancellation failure nacks them ahead of later arrivals; delivery is announced
  only after durable adoption.
- Skill metadata cannot widen or narrow any offer. Unattended/capture/scheduled runs retain the
  caller's pre-existing reach and policy.
- Catalog metadata and model-sourced argument values are explicitly fenced as untrusted selection
  data. Direct user argument substitution remains Claude-compatible.
- Canonical ids, realpath containment, and fail-closed skill state protect every catalog, lookup,
  bundle, and invocation path.
- Skill folder and state mutations hold one cross-process exclusive lock for the complete transaction.
  Invocation holds the matching shared lock across refetch, eligibility, rendering, and bundle
  activation; create/update/delete failures leave a durable disabled state instead of a live partial
  record.
- Folder transactions use no-follow directory descriptors and verify the lexical root's device/inode
  before final enablement. Create refuses an entry that appears after its disabled state save; delete
  renames a verified inode to a private tombstone before descriptor-relative removal and never deletes
  a swapped target.
- Every persisted state row has a strict total schema (`enabled`, `author`, `created_at`, `updated_at`,
  with optional `invocation_count`). Missing core fields, invalid dates/types, and unknown fields
  quarantine the file and raise `DataIntegrityError`; no ambiguous row is auto-enabled.
- The managed `skill-creator` body and all reference files install as one exclusive, fail-loud bundle
  transaction and become enabled only after every durable write succeeds. Startup alone catches and
  logs this optional install failure.
- Legacy parameter declarations use unique identifier names matching
  `[A-Za-z_][A-Za-z0-9_]*`. The model catalog omits the removed per-skill script-gate annotation;
  script execution remains governed by the live bundle and current instance/unattended gates.
- A queued invocation preserves and validates its `user` or `model` source on live and residue replay.
  Identical sibling activations share one body and do not consume recursion depth twice.
- Basis: owner directive to make skill injection and adherence exactly match Claude Code
