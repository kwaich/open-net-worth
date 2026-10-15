---
title: Remove the layered AI permission system (T0/T1/T2, headless ceiling, per-turn scopes, auto-apply grants); keep authentication, anti-tampering, and untrusted-input fencing
status: Accepted
date: 2026-07-18
deciders: repo owner (explicit decision, 2026-07-18); recorded by the harness-audit orchestrating agent
supersedes: docs/adr/2026-07-12-payee-config-writes-grantable-t1.md
---

# Remove the layered AI permission system

## Context

Over Phase 3 the repo accumulated a layered authorization model on top of the AI tool
surface. It has five distinct mechanisms, built at different times for different reasons,
that together decide whether a given tool call is allowed to execute:

1. **The tier taxonomy** (`backend/core/agent_caps.py`) — every tool is T0 (read / notify /
   propose), T1 (a write listed in a named `CAPABILITIES` bundle, grantable per agent), or
   T2 (everything else, never reachable unattended). Closed-world: unclassified means T2.
   A hardened `_UNTRUSTED_T1_FLOOR` further narrows agents that ingest web/email content.
2. **The headless ceiling** (`backend/core/headless_scope.py`, `HEADLESS_ALLOWED_TOOLS`) —
   a fixed allowlist that a scheduled `prompt_lucius` turn, an inbound MCP call, and an
   agent run are all narrowed to. Enforced twice: at the `tools=` offer (`_active_tools`)
   and again at dispatch (the default-deny block in `ai/assistant.py`).
3. **Three per-turn tool-scope narrowings** (three `_skill_scope` ContextVars, checked in
   priority order) — headless (hard read+notify+propose allowlist), skill (the skill's
   allowlist ∪ skill-authoring tools), responder (an `@<agent>` chat turn narrowed to that
   agent's `allowed_tools`).
4. **The propose→wait→apply protocol** — money-touching writes are split into a
   `propose_*` tool that returns a change dict and an `apply_*` tool that commits it,
   with the human confirm card in between.
5. **Per-agent auto-apply grants** (`models/agent.py::AutoApplyGrant`, the
   `core.auto_apply_scope` ContextVar, `services.agents.exceeds_amount_cap`) plus the
   **inbound MCP tier gate** that reuses all of the above verbatim.

Four facts about what this system is actually protecting, stated by the owner:

- **The app records money; it does not move it.** There are no trades, no transfers, no
  payment rails. Every write is a row in a JSON store describing something that already
  happened in the real world. The worst outcome of an unwanted write is a wrong number on
  a screen, not a lost dollar.
- **Every write is already reversible.** Every `JsonStore._save_raw()` fires
  `core.io.fire_write_hook(path)` → a checkpoint auto-snapshot. Corrupted or unwanted rows
  are recoverable from the checkpoint, independent of who or what wrote them.
- **It is over-engineered relative to a single-user install.** The threat model the tiers
  were designed against — a caller who has authenticated but should still be constrained —
  does not exist here. There is one user. Anyone holding the Bearer token or a session is
  that user. The tiers spend their complexity defending the user from themselves.
- **The tiers were guessed, not measured.** The T0/T1/T2 line, the ten-vs-five payee split
  (ADR 2026-07-12), the `_UNTRUSTED_T1_FLOOR` membership — none of these were derived from
  an observed failure. They were an a-priori guess at where harm might occur, and each
  guess has since cost real capability: the payee ADR exists solely because a legitimate
  unattended bookkeeping job hit `"Tool 'create_payee' is not allowed for an agent"`, and
  `.claude/rfcs/headless-proposal-pipeline.md` documents a live incident where the
  permission layer and the system prompt disagreed and five real trades were silently
  destroyed.

The owner would rather run the system with real usage and design a real gate from observed
failures than keep guessing at one.

## Decision

**The layered permission system is removed.** Specifically, the following are torn out:

- The **T0/T1/T2 tier taxonomy** and the `CAPABILITIES` bundles, including
  `_UNTRUSTED_T1_FLOOR`.
- The **headless ceiling** (`HEADLESS_ALLOWED_TOOLS` and the `core.headless_scope` seam),
  and with it the `effective_scope = HEADLESS_ALLOWED_TOOLS ∩ agent.allowed_tools`
  computation and the tighten-only validation rule in `services.agents.validate_agent`.
- The **three per-turn tool-scope narrowings** (headless / skill / responder) and the
  dispatch-time default-deny block that enforced them.
- The **propose→wait→apply protocol** as a *permission* mechanism. (The confirm card
  itself is kept — see below. What goes is the enforcement that a write is unreachable
  until a separate `apply_*` call.)
- **Per-agent auto-apply grants** (`AutoApplyGrant`, the `core.auto_apply_scope`
  ContextVar, amount caps as an authorization gate).
- The **inbound MCP tier gate** — the T0-read / T1-iff-granted / T2-refused ladder on
  `backend/mcp/`.

This is decided, not proposed. It is not conditional on the observability work below
landing first; the observability surfaces named there are the mechanism by which the
decision is later revisited from evidence.

### What is explicitly KEPT — and why it is not permission tiering

A future agent reading this ADR will be tempted to "finish the job." Do not. Each item
below is a *different mechanism defending a different boundary*, and none of them is
authorization of a trusted caller.

**1. Bearer auth on the inbound MCP server.** This is **authentication, not
authorization** — it answers "is this the owner?", not "may the owner do this?". Removing
the tiers removes the second question entirely; it does not remove the first. An
unauthenticated caller must still be refused. `core.security.verify_api_token` and the
app-wide gate in `api/_deps.require_auth` stay exactly as they are.

**2. Nonce staging that fires server-STORED args** (`backend/db/mcp_pending_call_store.py`).
This is **anti-tampering**, not gating. The staged call's arguments are written to the
store at propose time and re-read from the store at execution time; the nonce redeemed by
the approver selects a stored plan, and the approval never carries the arguments back in.
That makes an approve-then-substitute attack — approve a benign call, execute a different
one — structurally impossible rather than merely unlikely. It is worth noting that OpenClaw
independently arrived at the same mechanism (a canonical stored plan re-validated at
execution), which is a reasonable signal that this is the load-bearing part of a confirm
flow and the tier ladder around it was not.

**3. The IDOR proposer↔tapper binding in the chat-connector confirm path.** A confirm tap
arriving over Telegram/Slack/Discord must come from the same sender that received the
proposal. This defends against a *different principal* on a shared channel, not against
the owner. It stays.

**4. Every untrusted-text sanitization / fencing site.** Control-character stripping,
length caps, delimiter-collapse, and the fenced `«screen»…«/screen»` /
`--- BEGIN PRIOR NOTE ---` / `fence_untrusted_block("owner-guidance", …)` conventions
defend against **prompt injection from third-party content** — news headlines, emails,
screen snapshots, statement text, external MCP responses. That content is untrusted no
matter how permissive the tool surface is; removing the tiers *raises* the value of these
sites, it does not lower it.

The decision was taken against a working figure of "21 sites." A full inventory taken while
writing this ADR found **66 distinct call sites**, in three families, and the corrected
number is recorded here so a future sweep does not stop at 21:

| Family | Sites | Where |
|---|---|---|
| `core.untrusted` fences (`fence_untrusted_block` / `fence_screen_context` / `fence_mention_context` / `fence_external_output`) | 23 | `mcp/client_manager.py` (8), `services/statements/tiers.py` (3), `ai/tools/misc.py` (3), `monitoring/asset_brief.py` (2), `services/statements/lucius_pass.py` (2), `core/mcp_external.py`, `ai/tools/screen.py`, `ai/tools/email_ingest.py`, `services/screen_context.py`, `services/mentions/dispatch.py` |
| `core.untrusted.sanitize_untrusted` | 24 | `services/mentions/dispatch.py` (4), `monitoring/asset_brief.py` (5), `services/statements/*` (5), `services/email_snapshots.py` (3), `ai/tools/misc.py` (3), + 4 others |
| `monitoring.text_sanitize` (`sanitize_untrusted_text`, `content_tokens`) | 19 | `monitoring/analyst_prompt.py` (8), `monitoring/news_screen.py` (6), `monitoring/analyst.py`, `monitoring/topic_analyst.py` |

Counting local wrapper fan-out (`services/screen_context.py::_sanitize` ×12,
`ai/tools/catalog.py::_sanitize_external` ×3) the broad total is **81**.

Two corrections to the "five sites in `catalog.py` and `assistant.py`" framing, both of
which make the sweep hazard *more* real, not less:

- **`ai/tools/catalog.py` has 3 sites, and they do not use `core/untrusted` at all.** It
  defines its own private `_sanitize_external` (control-char strip, whitespace collapse,
  leading markdown-header/fence-marker strip) called at three points — an external tool's
  description summary, its server label, and its *name* (then capped to 80 chars). What it
  is defending: attacker-controlled external-MCP server and tool metadata being embedded
  into the **1h-cached system prompt**. Because it is a private helper in a
  non-security-named module with no import from `core/untrusted`, a grep-driven teardown
  will not find it. Leave it.
- **`ai/assistant.py` has 0 fencing call sites.** It only *references* the mechanism (a
  docstring on external servers being untrusted; a comment noting the external dispatcher
  returns already-fenced text). It is a consumer of fenced text, not a fencing site — so
  there is nothing to preserve there, and equally nothing whose removal is safe to infer
  from its comments.

None of these are permission code. Leave all of them.

**5. Everything in the OUTBOUND external-MCP client.** The fail-closed posture (a trusted
server may auto-fire ONLY a tool positively classified read-only), the
money-movement-always-confirms rule, and the read-only allowlist defend against **a
third-party server we are calling** — untrusted code whose responses re-enter our model.
That is the *opposite polarity* from the inbound boundary: inbound, we are the trusted
resource and the caller is the owner; outbound, the remote party is untrusted. Tearing
down the inbound tiers says nothing about the outbound ones. They stay in full.

**6. The human-readable confirm card (`ChangePreview`) and the `proposed_change` SSE
event.** These are a **UX affordance** — showing the user exactly what is about to be
written, every field including the date, before it is written. They remain the right
interaction for a money-shaped change, and the FE-4/INV1-2 completeness rule (a confirm
card MUST show every field that will be set) still binds. What changed is that the card is
no longer the thing that makes a write *possible*; it is the thing that makes a write
*legible*.

### The observability replacement

"Tighten later from evidence" is only credible if the evidence will actually exist. A gate
removed without a record is a gate removed permanently, because nobody will ever be able to
say what it would have caught. The condition attached to this decision is therefore that
AI-originated writes remain **fully attributable after the fact**, via surfaces that
already exist and must not be swept along with the tiers:

- **Checkpoint snapshots** — every `JsonStore._save_raw()` fires the write hook. This is
  the recovery path and the reason a wrong write is a nuisance rather than a loss. It is
  now load-bearing in a way it was not before, when the tiers were the first line.
- **Transaction audit** (`txn_audit.json` via `services/audit.py`, context via
  `core/audit_context.py`) — every ledger create/update/delete, with its scope, already
  records who wrote it and what changed, including `rule_applied` provenance when a rule
  decided the category.
- **The run ledger** (`run_history.json` via `services/run_ledger.record_run()`) — per-agent
  and per-scheduled-task run outcomes, with trigger provenance.
- **Cost/telemetry** (`get_cost_summary`, `get_recent_logs`) — what the model actually did,
  per turn.

The gate that replaces the tiers, if one is ever needed, is to be designed from these
records: a specific observed failure, its trace, and the narrowest mechanism that would
have caught it. Not from a re-derivation of a tier table. If a future agent finds itself
proposing a tier taxonomy again without naming the incident that motivated it, that is the
signal it is re-guessing.

## Consequences

### Positive

- One tool surface. A tool is a tool; the caller class (interactive chat, scheduled task,
  agent run, inbound MCP) no longer changes which tools exist or what they do. The
  `*`/`†` headless annotations, the tier table, the capability bundles, and the four
  places a tool name had to be kept in sync (`HEADLESS_ALLOWED_TOOLS`, `agent_caps._T0`,
  `CAPABILITIES`, the CLAUDE.md legend) collapse to zero.
- Whole classes of bug disappear: the prompt/permission disagreement that destroyed five
  proposed trades; a tool silently unreachable because nobody classified it; a capability
  blocked "by omission, not by judgment" (the exact wording of the ADR this supersedes).
- Agents can do the jobs they were configured to do without a per-tool grant negotiation.

### Negative

- A misbehaving or prompt-injected agent can now write directly, unattended, at the full
  tool surface. The mitigations are the ones the owner weighed and accepted: the app moves
  no money, every write is checkpoint-reversible, the write is attributable in the audit
  trail, and the untrusted-content fencing (kept, item 4) remains the primary defense
  against the injection path itself.
- Documentation across the repo asserted a *structural guarantee* ("an unattended caller
  can NEVER auto-apply a money mutation") that no longer holds. Every such assertion has to
  be corrected rather than left to rot into a lie a future agent trusts. See
  `.claude/rfcs/harness-audit-2026-07-18/claude-md-teardown-edits.md` and the superseded
  headers listed below.
- ADR 2026-07-12's entire thesis — that ten payee config writes are grantable T1 and five
  bulk/destructive ones stay T2 — is reversed. There is no T1 and no T2 to be in. All
  fifteen payee tools are simply tools.

### Follow-ups

- Apply the staged CLAUDE.md replacement blocks
  (`.claude/rfcs/harness-audit-2026-07-18/claude-md-teardown-edits.md`) — CLAUDE.md is
  concurrently modified by other lanes, so the edits are staged rather than applied inline.
- The code teardown itself (`core/agent_caps.py`, `core/headless_scope.py`, the
  `_skill_scope` ContextVars, the dispatch default-deny block, `AutoApplyGrant`, the MCP
  tier gate) is a separate lane. This ADR records the decision; it does not perform it.
- The three `_sanitize_external` sites in `ai/tools/catalog.py` should be covered by a test
  that fails if they are removed, so that "easy to sweep by accident" stops being true for
  the one family that a grep for `core.untrusted` will never surface.
- Several documents outside the list this lane was given are primary or near-primary specs
  of the removed system and now carry pointer headers (`.claude/rfcs/phase3/agents.md`,
  `docs/rfc/self-improvement.md`, `docs/rfc/lucius-self-harness.md`,
  `.claude/rfcs/phase2/sk1-skill-registry.md`). A longer tail of ~30 further documents
  references the tiers in passing and was left alone; the full inventory is in this lane's
  report.

## Alternatives considered

- **Keep the tiers, widen them case by case.** Rejected — this is the status quo, and the
  status quo is a stream of ADRs like 2026-07-12, each reclassifying one family after it
  blocks a legitimate job. The cost is paid per-tool, forever, and the classification is
  still a guess.
- **Collapse three tiers to two (read vs. write).** Rejected — it keeps the whole
  enforcement apparatus (the ceiling, the ContextVars, the double-enforcement at offer and
  dispatch, the sync-four-lists problem) while giving up most of the granularity that was
  the apparatus's only justification. Worst of both.
- **Keep the headless ceiling, drop the rest.** Rejected — the headless ceiling is the
  mechanism most directly responsible for the observed failures (the destroyed-proposals
  incident; the `create_payee` refusal), because it is the one that silently makes a
  configured capability unreachable at runtime.
- **Remove everything security-named, including the kept items.** Rejected explicitly, and
  the reason is recorded above in detail: authentication, anti-tampering, cross-principal
  IDOR binding, untrusted-input fencing, and the outbound third-party posture are four
  different boundaries, none of them the inbound-authorization boundary being removed.

## Agent provenance

- **Decided by:** the repo owner, explicitly, 2026-07-18. Recorded (not recommended) by the
  orchestrating agent on `harness/reliability-program`; the owner was AFK for the write-up.
- **Lane / branch:** `harness/reliability-program`, docs-only lane (no code changed in this
  lane — six other agents were live in `backend/`/`frontend/` at the time).
- **Alternatives weighed:** widen tiers case-by-case / collapse to two tiers / keep only
  the headless ceiling / remove everything security-named.
- **Driving artifacts:** `.claude/rfcs/phase3/agents-permissions.md` (the T0/T1/T2 model
  being removed); `docs/adr/2026-07-12-payee-config-writes-grantable-t1.md` (superseded);
  `.claude/rfcs/headless-proposal-pipeline.md` (the 2026-06-12 incident where the
  permission layer and the system prompt disagreed and five proposed disposals were
  silently destroyed); `.claude/rfcs/harness-audit-2026-07-18/` (the audit that surfaced
  the maintenance surface of the tier annotations).
- **Why this way (one line):** the app records money rather than moving it and every write
  is checkpoint-reversible, so a guessed-at authorization ladder was costing real
  capability to defend a single user from themselves — run it open, keep the mechanisms
  that defend the *other* boundaries, and design any future gate from an observed failure
  instead of a prediction.
