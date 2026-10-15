# Retire Agents; the unattended write envelope is removed with no successor

- **Date:** 2026-08-02
- **Status:** Accepted
- **Owner decision.** Recorded by the agent that carried it out (provenance block at the end).

## Context

Agents were a second way to run unattended work, alongside Automations: a stored prompt, a
tool allowlist, a schedule, a findings queue, a self-improvement review loop, and a
`chat_responder` that let one answer inbound chat.

A live audit before the change found the product had already hollowed out:

- **No Automation invoked an Agent.** Nothing called `run_agent_now` or `dispatch_to_agent`.
- **The scheduler ran only Automations.**
- **Every Agent's prompt had already been inlined** into an Automation's instructions.

So the substrate was carrying a full surface — 11 tools, 5 stores, a REST API, a tab, 15
frontend components, an envelope policy — for a product nothing dispatched to.

## Decision

**Retire Agents entirely** — not hide the tab. Lucius, driven by an Automation, is the one
way unattended work runs.

Deleted: `models/agent.py`, four agent stores, `services/agents.py`,
`services/agent_reflection.py`, `services/chat_responder.py`, `api/agents.py`,
`ai/tools/agents.py`, `core/active_agent.py`, the Agents tab and its components, and the
~20 agent tools. `core/agent_runner.py` became `core/headless_turn.py` — kept, because
`services/self_harness.py` still calls `get_runner()` and self-harness is live.

Data migrated by `backend/scripts/retire_agents.py` (idempotent, backed up first).

### The consequence that needs stating plainly

**`AgentEnvelope` is gone and has no successor.**

It bounded an unattended write by account, kind, amount ceiling and category set. Removing
Agents removed the only thing that carried one, and the owner's decision was to rely on
**approval cards only** rather than rebuild an equivalent for Automations.

What that means concretely:

- An unattended write is no longer bounded by amount, account or kind.
- The **hardline tier survives** — `delete_transactions_where` and `apply_payee_rules` are
  still refused outright to an unattended caller, because their target set cannot be
  verified after the fact.
- Everything else is governed by the Automation's `approval` mode (`manual` / `auto` /
  `skip`) and its `Always allowed` list.

This is a real reduction in defence-in-depth, taken deliberately. `tests/test_agents_retired
.py` asserts it in the affirmative — an unattended write of 1,000,000 now proceeds — so the
removal stays visible rather than being discovered later by someone reading an absence.

## Alternatives considered

- **Hide the tab, keep the code.** Rejected: the maintenance cost was the point, and a
  dormant second execution path is exactly the thing that rots and then surprises you.
- **Port the envelope to Automations.** Rejected by the owner. An envelope nobody could
  configure was never a real bound — there was no UI for one, and `models/agent.py`
  described REST as "once wired there", which it never was.

## Consequences

- One execution path to reason about, one place approvals are decided.
- Characterization coverage moved rather than being dropped: `test_char_tool_dispatch`'s
  agent-tool blocks were deleted and `test_agents_retired.py` asserts the same names are
  gone and their modules no longer import.
- The tool names are permanently retired — see `docs/ai/TOOL_RENAMES.md`. A tool name is
  persisted data, so they are recorded rather than silently vanishing.
- **Follow-up, not done here:** nothing rebuilds a configurable unattended-write bound. If
  one is ever wanted, it belongs on the Automation, and this ADR is the reason it does not
  exist today.

## Agent provenance

Carried out by Claude Opus 5 across 2026-08-02/03 at the owner's direction, in a single
long session. The owner made every scoping call: retire rather than hide, approval cards
rather than an envelope, no per-run tool ceiling, and no iteration caps on a run. The
implementing agent chose the deletion order, the migration design, and what to keep
(`headless_turn`, self-harness) — and got the envelope's justification wrong once, arguing
for an ADR on the wrong grounds before being corrected.
