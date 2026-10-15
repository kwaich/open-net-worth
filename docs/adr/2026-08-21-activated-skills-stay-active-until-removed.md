---
title: Keep activated skills active until removed
status: Superseded
date: 2026-08-21
deciders: Codex primary agent + repo owner
---

# Keep activated skills active until removed

Superseded by [Skills are session context, not active modes](2026-08-22-skills-are-session-context-not-active-modes.md).

## Context

Open Net Worth durably recorded skill activation but deliberately removed every prior skill body
from the next provider request and cleared its resource scope at turn end. A follow-up in the same
conversation therefore retained only catalog metadata: the model lost the workflow instructions and
`read_skill_file` could no longer open the skill's references. The behavior contradicted the user's
visible “Loaded skill” receipt and the Claude Code lifecycle chosen as the product target.

Activation can originate from a deterministic slash/picker selection or the model's
`activate_skill` call. Once either path succeeds, the resulting task context has the same user-facing
meaning. Treating the sources differently after activation would create two skill lifecycles and make
the visible state unreliable.

## Decision

We will keep every successfully activated skill active for its conversation until the user explicitly
removes it or the skill becomes unavailable. Activation source remains audit metadata only.

The transcript is the source of truth. It stores append-only activation and deactivation receipts,
not a second mutable skill-state store. At every fresh user boundary the host derives the ordered
active set, re-reads and validates each current `SKILL.md`, reopens its resource bundle, and injects a
fresh provider-only copy immediately before the new user message. Old rendered bodies are never
trusted or shown again. Fold/window compaction cannot remove these fresh copies. Invocation arguments
are one-shot input: they are present on the activating turn but are not replayed as if the skill had
been invoked again on every follow-up.

The API exposes the derived active set with transcript hydration. Every shared composer renders the
same removable active-skill chips. Removal takes the same per-session lock as a chat turn, appends one
deactivation receipt, and applies before the next provider call. A globally disabled, deleted, or
invalid skill fails closed and is retired from the conversation automatically.

At most six skills and 120 KB of rendered skill instructions may be active at once. Skills remain
instructions only: activation never grants, denies, adds, or removes tools.

## Consequences

### Positive

- Follow-ups, reloads, approval resumes, queue replay, and context folding retain the same workflow.
- Slash and model activation have one observable lifecycle and one deactivation control.
- Bundled references and scripts remain addressable while their skill is active.
- The active UI is derived from the same append-only evidence the provider runtime uses.

### Negative

- Active skills consume prompt context on every later turn until removed.
- An unrelated topic in the same conversation can still carry old skill instructions; the chip makes
  that state explicit and removable instead of guessing task completion.
- Editing a skill changes the instructions used by its next follow-up because continuity revalidates
  the current file rather than replaying stale rendered bytes.

### Follow-ups

- Keep the focused activation, restart, folding, bundle-resource, hydration, and deactivation tests in
  the blocking skill/runtime suites.
- If real conversations regularly hit the active-instruction budget, measure token usage before
  changing the cap or inventing implicit expiry.

## Alternatives considered

- Per-turn expiry for every skill — rejected because it caused the reported loss of workflow and
  resource context immediately after “Loaded skill.”
- Persist only slash-selected skills — rejected because activation source does not change what an
  activated workflow means and would create two hidden lifecycle rules.
- Persist rendered instruction bodies forever — rejected because edits, disables, deletes, and
  invalid files must take effect safely; re-reading under the existing pinned snapshot is fail-closed.
- Store active skills in session metadata — rejected because the transcript already supplies ordered,
  durable evidence and a second mutable record could drift from activation activity.

## Agent provenance

- **Decided by:** Codex primary implementation agent with the repo owner
- **Lane / branch:** `fix/scheduled-run-reliability-and-conversation-provenance`
- **Alternatives weighed:** per-turn expiry, slash-only persistence, stale-body replay, and separate session metadata
- **Driving artifacts:** owner screenshots of `account-balance-tracer` losing its instructions and [the superseded lifecycle ADR](2026-08-09-host-owns-skill-invocation-lifecycle.md)
- **Why this way (one line):** A loaded skill must remain the same durable conversation context regardless of who activated it.
