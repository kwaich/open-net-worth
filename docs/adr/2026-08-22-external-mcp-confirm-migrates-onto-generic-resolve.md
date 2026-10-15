---
title: External-MCP confirm→apply migrates off its own nonce store onto the generic paused-tool-use resolution mechanism
status: Accepted
date: 2026-08-22
deciders: owner + senior-engineer implementation lane (Claude)
---

# External-MCP confirm→apply migrates off its own nonce store onto the generic paused-tool-use resolution mechanism

## Context

The owner asked whether Codex's MCP approval flow carries the same nonce check this app's
external-MCP write path does. Verified against Codex's real source
(`codex-rs/core/src/mcp_tool_call.rs::handle_mcp_tool_call`): Codex's propose → wait-for-human
→ execute all run inside ONE continuous function over the same in-memory prepared call — the
model is never asked to re-call anything to fire an approved action. That is structurally
different from this app's PRE-existing external-MCP flow, which required Lucius to make a
SECOND tool call (`apply_external_mcp_call(nonce)`) after a human approved — a real model
round-trip, and the reason the nonce existed at all (closing an "approve-then-substitute" gap
Codex's architecture never opens).

This app already has a no-model-round-trip mechanism for every OTHER paused/awaiting-approval
tool call, built for the ledger confirm gate and later generalized to every gated tool (RFC
2026-07-22): `POST /api/chat/{id}/resolve` → `ai.assistant.resolve_pending_tool_use`, which
redeems a `PendingPlanStore` entry keyed by the paused `tool_use_id` and resumes the SAME turn
— no `confirm_token`, no model re-call, the frontend never constructs a model message for it.
External-MCP was the one write surface in the app that had NOT been migrated onto this
mechanism; it kept its own earlier, narrower propose→confirm→apply shape (a bespoke
`McpPendingCallStore` nonce queue + a second registry tool, `apply_external_mcp_call`,
predating RFC 2026-07-22). The owner also flagged a live consequence of that: `
apply_external_mcp_call` was ITSELF independently permission-gated (an ordinary tool reaches
`registry.dispatch`'s universal confirm gate), so a card the user had already approved
required a SECOND separate approval to actually apply — pure redundant friction, not
intentional defense.

## Decision

Migrate external-MCP's confirm→apply onto the SAME generic mechanism every other gated tool in
the app already uses, rather than inventing a third shape:

- `backend/mcp/client_manager.py::call_external_tool`'s `needs_approval` branch now stages
  `{server_id, tool, args}` under the IN-FLIGHT `tool_use_id`
  (`core.tool_use_context.get_tool_use_id()`) directly into `PendingPlanStore` — the exact
  `stage_pending`/`pop_pending_for_tool` shape `ai.tools._confirm_gate.stage_generic` uses for
  every other dispatch-gated tool. `ai/assistant.py`'s turn loop binds that context var before
  calling the external dispatcher, mirroring what `registry.dispatch` already does for an
  internal call (external tools bypass the registry entirely — they carry no registered
  handler — so nothing else would bind it).
- The confirm card (`type: "external_mcp_call"`) carries `tool_use_id`, not a minted `nonce`.
  It deliberately does NOT carry `grant_tool` — that key is reserved for a canonical REGISTRY
  tool name (the generic Allow-once/Always-allow grant machinery resolves an owner from the
  registry, which an `mcp__<server>__<tool>` name is not a member of); an external tool's
  standing permission is set through the Connectors tab's own ladder, unchanged.
- `POST /api/chat/{id}/resolve`'s approve path
  (`ai.assistant._resolve_pending_tool_use_body`) special-cases `mcp_external.is_external
  (tool_name)`: it redeems the `PendingPlanStore` entry via the SAME
  `ai.tools._confirm_gate.redeem` choke every self-staging handler uses, then calls
  `McpClientManager.apply_confirmed(name, args)` with the STORED args — never re-deriving them
  from the request. `apply_confirmed`'s signature changes from `(nonce)` to `(name, args)`
  accordingly: redemption (the security-critical pop) now happens at the caller, one layer up,
  so the dispatcher's own job narrows to "re-verify the CURRENT permission level and fire" —
  the same re-check `_dispatch_refusal` already performed.
- `apply_external_mcp_call` (`backend/ai/tools/external_mcp.py`) is DELETED outright, not
  aliased. This is a genuine removal, not a rename: there is no successor tool a legacy
  Automation/skill referencing it should resolve to, and CLAUDE.md's alias requirement is
  specifically about renames where an old name should keep working. A stored automation step
  or skill that still names `apply_external_mcp_call` will now fail with "unknown tool" —
  accepted, since the entire point of the removal is that nothing should ever need to call it
  again (the UI confirm tap resolves the card directly).
- `mcp_pending_calls` (table + `McpPendingCallStore`) is dropped in the same change (Alembic
  revision `0047`) — nothing else read or wrote it once the stage/apply paths above moved onto
  `pending_plans`.

The three security properties the nonce mechanism existed to guarantee carry over unchanged
onto `PendingPlanStore`, because they are the SAME properties `PendingPlanStore` already
guarantees every other gated tool (verified directly, not merely argued — see
`tests/test_mcp_client.py`'s rewritten propose/redeem/blocked-recheck tests and
`tests/test_resolve_pending_tool_use.py`'s existing coverage of the shared redeem choke):

1. **Args are authoritative-from-storage.** `apply_confirmed`'s caller (`assistant.py`) never
   accepts server/tool/args from the resolve request — it only reads `tool_use_id` and
   `decision`, and redeems the STORED entry. A card showing `transfer $100` cannot apply
   `transfer $10000` regardless of what a client or the model supplies.
2. **An unknown/already-used/stale pending entry is refused, not silently ignored.**
   `ai.tools._confirm_gate.redeem` → `PendingPlanStore.pop_pending_for_tool` returns `None` on
   a missing, already-popped, or cross-tool-name entry; the resolve path surfaces
   `unknown_token_result` to the model rather than falling through to any other args.
3. **The target tool's CURRENT permission level is re-checked at resolution time.**
   `McpClientManager.apply_confirmed` still calls `_dispatch_refusal` (unchanged) before
   firing, so a tool moved to `blocked` — or a server disabled — while its card sat awaiting a
   click is refused rather than fired, exactly as the nonce path's `apply_confirmed` did.

## Deliberate behavior change (documented, not a regression)

`McpPendingCallStore` enforced its own 24-hour TTL on a staged call (L4, added after a real
incident where "expired, already applied, or never proposed" was two-thirds false).
`PendingPlanStore`'s awaiting-approval rows never expire on a clock (ADR
`2026-07-30-approvals-never-expire`) — a human confirm card is a question put to a person, who
may legitimately answer it hours later, and every OTHER confirm card in the app already works
this way. An external-MCP confirm card therefore now behaves identically to every other confirm
card in the app instead of being a narrower, bespoke exception. Nothing else about staleness
handling changes: a NEW user message still supersedes/aborts a stale pending call
(`assistant._abort_stale_pending`, unchanged, and now applies to external-MCP cards too since
they share the same store).

## Alternatives rejected

- **Give `apply_external_mcp_call` its own dedicated exemption from the confirm gate**, so the
  redundant second approval goes away without touching the propose/apply shape. Rejected: it
  patches the symptom (double-confirm) without closing the actual model-round-trip gap the
  owner's original question raised, and it would be the one tool in the registry carrying a
  bespoke "exempt from the universal gate" carve-out — exactly the kind of per-tool special
  case `E4`'s dispatch-level gate was built to make structurally impossible to forget.
- **Keep the nonce store but make the frontend/resolve endpoint redeem it directly**, skipping
  `PendingPlanStore` entirely. Rejected: it would leave two coexisting paused-tool-use
  mechanisms with two different storage shapes, TTL policies, and redemption chokes, for no
  remaining reason — external-MCP has no requirement `PendingPlanStore`'s existing
  `tool_use_id`-keyed shape doesn't already satisfy.

## Consequences

- One fewer model-callable tool (`apply_external_mcp_call` gone); one fewer DB table
  (`mcp_pending_calls` gone, revision `0047`).
- A Telegram/Slack-style connector confirm tap on an `external_mcp_call` card, which the
  connector's own `apply_change` dispatcher had NO branch for before this change (it fell
  through to `positions_service.apply_position_change` and raised "unknown change type" — a
  pre-existing, unrelated gap), now resolves correctly through the same
  `_resolve_via_pending_tool_use` choke the `tool_permission` branch already used. Fixed as a
  direct, low-cost consequence of unifying the mechanism, not a separate initiative.
- `CLAUDE.md`'s "Security-critical areas" section still describes the OLD nonce mechanism
  ("KEPT: nonce staging that fires server-STORED args" under External MCP) and needs a
  follow-up edit to describe the new one — left to the owner/a follow-up change since
  `CLAUDE.md` is a shared, frequently-edited file other concurrent sessions may also be
  touching.
