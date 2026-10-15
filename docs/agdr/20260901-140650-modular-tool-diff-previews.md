---
timestamp: 2026-09-01T14:06:50Z
agent: pi-coding-agent
model: gpt-5
session: unknown
trigger: user-prompt
status: executed
ticket: no-ticket
---

# Modular Tool Diff Previews

> In the context of approval cards for AI write tools, facing tool-specific preview logic that made each new diff view require changes across the confirmation gate and React card, I decided to register preview adapters with tool metadata and render by a server-provided preview kind to achieve incremental tool onboarding, accepting a small display-envelope and registry contract.

## Context

- PR #34 hardcoded `update_automations` in backend staging, approval resolution, and `ChangePreview.tsx`.
- Approval remains bound to server-stored raw arguments; preview data must stay display-only.
- Existing approval cards need to remain readable after reloads and deployments.

## Options Considered

| Option | Pros | Cons |
|--------|------|------|
| Tool-registered preview adapters and frontend renderer registry | Localizes onboarding to tool metadata plus a renderer kind; supports adapter-owned freshness checks | Adds a small cross-layer preview contract |
| One universal renderer with a fixed payload schema | Fewer frontend renderers | Forces every tool into one shape and makes domain-specific previews awkward |
| Tool-name conditionals in the shared confirmation card | Smallest immediate diff | Repeats the PR #34 coupling and makes each new tool expand central UI logic |

## Decision

Chosen: **Tool-registered preview adapters and frontend renderer registry**, because the repository already uses registry-collected tool metadata and the failure mode was specifically duplicated tool-name branching. The adapter owns server-side resolution and freshness validation; the UI selects a renderer by `preview.kind` and falls back safely for unknown kinds.

## Consequences

- New gated tools can opt into a diff preview without modifying the confirmation gate or branching on their tool name in `ChangePreview`.
- Legacy automation preview payloads remain renderable through structural normalization.
- Preview payloads add a contract that must be documented and tested when new renderer kinds are introduced.

## Artifacts

- Feature branch: `feat/update-automations-diff-approval`
