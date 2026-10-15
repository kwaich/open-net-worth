---
title: Skills are session context, not active modes
status: Accepted
date: 2026-08-22
deciders: Codex primary agent + repo owner
---

# Skills are session context, not active modes

## Context

Open Net Worth made invoked skills look like durable application modes. Transcript hydration exposed
an `active_skills` projection, every composer rendered removable chips, and a conversation endpoint
appended deactivation receipts. The provider runtime nevertheless treated the rendered `SKILL.md` as
conversation instructions. This created a false lifecycle promise: neither the model nor the host can
reliably infer that a workflow is complete, and removing a chip cannot erase instructions the provider
has already seen earlier in the same conversation.

Claude Code and Codex instead expose skill loading as an invocation-time activity. The instructions
become part of the conversation context; they are not a running process with a trustworthy completion
or unload event. The host may need to restore those instructions after local transcript folding, but
that restoration is context preservation, not another invocation or a user-visible active state.

This decision supersedes [Keep activated skills active until removed](2026-08-21-activated-skills-stay-active-until-removed.md).

## Decision

A successful slash, picker, or model-selected invocation has one lifecycle:

- Emit one `Loaded <skill> skill` activity receipt for the invocation.
- Keep the rendered instructions and resource scope available throughout the conversation. When ONW
  folds or windows its local transcript, the host re-reads, validates, and restores the current skill
  instructions before the next provider call. Restored context is provider-only continuity and does
  not emit another loaded receipt.
- Do not expose `active_skills` in transcript hydration, persistent skill chips in composers, or a
  conversational skill-deactivation endpoint. ONW will not claim that it can unload instructions the
  model has already received.
- Do not infer completion from model prose, tool calls, elapsed turns, or topic changes. A new
  conversation is the deterministic clean-context boundary. Disabling or deleting a skill prevents
  future use and causes host restoration to fail closed; it is not presented as conversational unload.

Claude's maximum of six skills selected in one message remains enforced by slash parsing. There is no
six-skill lifetime limit across sequential invocations in one conversation. The existing rendered
instruction byte budget remains a host safety bound, not a permission system: skills never grant,
deny, add, or remove tools.

The removed ONW active-mode implementation remains recoverable at remote branch
`archive/active-skill-chips-deactivation-2026-08-22` if product evidence later justifies an explicit
ONW extension.

## Consequences

### Positive

- Slash, picker, and model-selected invocations behave identically.
- The UI reports the event that actually occurred without inventing a running or completed state.
- Follow-ups, approval resumes, queue replay, restart, and transcript folding retain workflow context.
- Long conversations can invoke more than six skills sequentially while one message remains bounded.

### Negative

- A user cannot reliably unload one skill midway through a conversation; starting a new conversation
  is required for a clean context.
- Earlier skill instructions may still influence a later topic in the same conversation.
- Host restoration consumes context, bounded by the existing aggregate instruction-byte guard.

### Follow-ups

- Keep focused tests for slash/model parity, one-time activity receipts, restart/folding restoration,
  resource access, absence of hydration/deactivation UI, and sequential invocations.
- Revisit explicit conversational modes only with a provider-level context removal primitive or clear
  product evidence; do not infer workflow completion from free-form model output.

## Alternatives considered

- Keep removable active-skill chips — rejected because the UI implies instructions can be removed
  from provider history. The implementation is archived rather than deleted from project history.
- Automatically hide or unload a skill when it appears complete — rejected because completion has no
  deterministic signal and multi-step workflows may resume after ordinary follow-ups.
- Make model-selected skills turn-scoped but slash-selected skills persistent — rejected because the
  invocation source does not change the meaning of the loaded instructions.
- Make every skill turn-scoped — rejected because follow-ups would lose the workflow and bundled
  resource context that the load receipt says was established.

## Agent provenance

- **Decided by:** Codex primary implementation agent with the repo owner
- **Lane / branch:** `fix/scheduled-run-reliability-and-conversation-provenance`
- **Alternatives weighed:** removable modes, inferred completion, source-dependent lifetimes, and
  turn-scoped instructions
- **Driving artifacts:** owner screenshots of persistent chips contradicting provider behavior and
  Claude Code's invocation-time `Loaded <skill> skill` activity
- **Why this way (one line):** Preserve real session context while showing only lifecycle state the
  host can truthfully guarantee.
