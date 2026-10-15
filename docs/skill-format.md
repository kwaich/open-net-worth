# Skill format

Open Net Worth uses the Agent Skills folder format directly. A skill is a directory whose only
required file is `SKILL.md`:

```text
my-skill/
├── SKILL.md
├── agents/openai.yaml   # optional OpenAI metadata
├── scripts/             # optional executable helpers
├── references/          # optional on-demand documentation
└── assets/              # optional static resources
```

## SKILL.md

```markdown
---
name: monthly-close
description: Reconcile a month and report the net-worth change. Use when the user asks to close or review a month.
license: Apache-2.0
compatibility: Designed for Open Net Worth
metadata:
  author: acme
  version: "1.0"
---

# Monthly close

Read the requested month from the current conversation, then follow the numbered workflow.
```

`name` and `description` are required. `license`, `compatibility`, and `metadata` are standard
optional fields. The standard's experimental `allowed-tools` field is accepted on import and
dropped: an ONW skill carries no tool list. The rendered SKILL.md is what Lucius reads when a
skill loads, and a tool list there reads as its whole toolset (ADR
`2026-09-26-skills-carry-no-allowed-tools`).

Claude Code adds optional frontmatter to the same file. ONW preserves the documented fields:
`when_to_use`, `argument-hint`, `arguments`, `disable-model-invocation`, `user-invocable`,
`model`, `effort`, `context`, `agent`, `background`, `hooks`, `paths`, and `shell`. Claude Code's
`disallowed-tools` is dropped like `allowed-tools`, since it is a tool list too. Claude Code-only
frontmatter is not accepted by claude.ai/API Agent Skills packaging.

ONW implements Claude-compatible invocation selection and argument rendering: `$ARGUMENTS`,
`$ARGUMENTS[N]`, `$N`, and names declared by `arguments`. Old ONW `parameters` and
`{{placeholder}}` templates remain unsupported. Missing positional values stay literal, and a
single backslash escapes a placeholder as Claude Code documents. Claude execution-environment features such as
forked subagents, model overrides, hooks, and dynamic shell preprocessing are preserved for export
but are not executed by ONW.

Direct-user arguments keep Claude's substitution semantics. Arguments authored by the model in an
automatic `activate_skill` call are never promoted to user instructions: placeholders point to a
bounded, nonce-fenced JSON data block that is explicitly marked untrusted.

## agents/openai.yaml

The optional OpenAI metadata file supports the Codex sections `interface`, `dependencies`, and
`policy`. In ONW, `policy.allow_implicit_invocation: false` removes a skill from automatic model
selection while leaving explicit `/skill-name` and structured UI invocation available. Users can
edit `interface`, `dependencies`, and policy fields from Skills → Edit → OpenAI.

```yaml
interface:
  display_name: Monthly close
  short_description: Reconcile and close a month
policy:
  allow_implicit_invocation: false
```

## Activation and lifetime

- The model receives a budgeted catalog containing enabled skill names and descriptions, not full
  bodies. It may call `activate_skill(skill_id=...)` when a description matches the request.
- `/skill-name` and the Skills tab pass the stored skill ID directly to the host, so explicit
  activation is deterministic and does not depend on model judgment.
- `/skill-name trailing text` passes the trailing text to every stacked inline skill (up to six),
  matching Claude Code's argument expansion.
- The host reads the canonical `SKILL.md`, adds it to user-role context for that turn, and emits
  visible loading/loaded/failed activity.
- Multiple distinct skills can be loaded in one turn. Repeated activation of the same skill within
  a turn is deduplicated.
- Skill bodies are turn-local. A later unrelated user turn does not automatically receive a prior
  skill body; the model can select it again from the catalog when appropriate.
- An approval pause/resume is still the same logical turn, so its selected skill bodies are restored
  after a durable reload. A genuinely new or injected user message retires the earlier selection.
- Skills never grant, deny, hide, or preapprove tools. Ordinary tool discovery and app-wide
  confirmation/security policy remain authoritative.

At most six unique skills may be selected for one message, and their combined rendered instructions
are capped at 120 KB. Bundle assets are stored and exported byte-for-byte; text-only reference reads
refuse binary files. Script and reference access reopens the enabled skill under the store lock and
requires the activation-time folder identity and content hash to still match.

Imports are canonicalized: portable Agent Skills fields, documented Claude Code frontmatter, and
`agents/openai.yaml` are retained. Old ONW-only fields and sidecars are ignored and are not emitted
again on export. Non-canonical or duplicate ZIP paths and malformed invocation-policy types are
rejected rather than silently normalized or enabled.
