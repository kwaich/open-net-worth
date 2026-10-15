# One confirm mechanism for every tool

- **Date:** 2026-08-25
- **Status:** Accepted
- **Supersedes:** the "deliberately NOT converted to the tool_use_id-keyed mechanism"
  paragraph in `registry.stage_write`'s docstring, and the coexistence clause in
  `ai/tools/_confirm_gate.redeem` ("BOTH this token shape and the new tool_use_id shape").
- **Extends:** RFC 2026-07-22 (pending-tool_use approval) to the 13 tools it scoped out.

## Context

Two confirm mechanisms shared one store.

**The current one** (RFC 2026-07-22): a gated tool stages its arguments under the
`tool_use_id` of the call being gated and returns the pause sentinel `(None, change)`. The
turn ends with that `tool_use_id` result-less. When the operator approves,
`assistant.resolve_pending_tool_use` calls the tool again with `{confirm_token: tool_use_id}`,
pops the stored arguments and runs *those*. The model is never in the approve→execute path
and no tool advertises the parameter.

**The older one** (2026-07-19, the propose_*/apply_* pair collapse): a tool minted a random
single-use token, returned a `pending_confirmation` result, and *told the model in its own
schema* to call it again with the token. Thirteen tools were on it — `add_positions`,
`update_positions`, `remove_positions`, `acquire_lots`, `dispose_lots`, `delete_lots`,
`undo_lot_disposals`, `record_vest`, `undo_vest`, `import_8b`, `open_option`, `close_option`,
`record_income`.

### It had not worked on the web since the day it shipped

Nothing ever handed the model the token. `stage_write`'s own result said *"wait for the user.
Their confirmation re-calls it with the token"* — but on the web nothing re-called it. Tapping
✓ made `ChangePreview.handleConfirm` fall through to a legacy branch that sent Lucius a
sentence: `Confirmed — remove Gold (spot) from the portfolio`. No token, and for the position
types not even a tool name.

So the model read its own schema (*"call it again with ONLY confirm_token from that card"*),
found it had never been given one, and did the only thing left — called the tool again with
the arguments. `should_preview_write()` was still true, so it drew **a second identical card**.
Loop. Nothing written. Reported live by the owner, 2026-08-25, with two stacked "Remove
positions" rows and Lucius reasoning *"the tool result never actually surfaced that token
value."*

The root cause is older than the RFC. Commit `344d4b81` (2026-07-19) collapsed each
propose/apply pair into one tool and **changed zero frontend files**. Before it,
`propose_position_remove` previewed and `apply_position_remove` wrote — "call the other tool"
*was* a complete handshake, which is why the confirm message never carried a token. After the
collapse, calling again re-previews unless a token comes with it. The token became
load-bearing in that commit; the frontend still sends the sentence that was sufficient before.

Three things hid it for a month: the connector path was separately wired
(`_CONFIRM_TOKEN_TOOLS` plus direct service dispatch), the ledger types *did* interpolate the
token into their text, and `stage_write`'s docstring asserted the mechanism "already works" —
an assumption, never verified on the surface where it was broken.

## Decision

**There is one staging mechanism, one redemption path and one card contract.**

`registry.stage_write` keeps its only real job — building a *resolved* preview (RFC §14.1
tier 1: the actual position/lot/vest/option/income row that will be written, re-verified
against `outcome` on approval) — and stages it exactly like `_confirm_gate.stage_generic`:
under `get_tool_use_id()`, via `stage_pending`, returning `(None, change)`.

**`stage_write` takes no tool name and no arguments** — it reads both from the dispatch
context, and that is a correctness property rather than convenience. They were parameters,
and across 18 call sites they got spelled four different ways: `input_data`, the derived
change, a hand-built dict, `active_call_input()`. Three were WRONG, because approval re-runs
the same handler and a handler re-derives (and re-validates) its change from its ARGUMENTS —
stage the change instead and the confirm fails on a card that was perfectly valid
("description is required", on a skill whose card showed a description). The lesson is
general: **a parameter a function can determine itself is a defect waiting for its turn.**

One override survives, keyword-only and rare by design: `import_8b` passes
`args={"payload": …}` so approval commits the already-extracted rows instead of re-running
the LLM extraction on the attachment ref.

**Not-previewing means WRITE, and both callers must take that branch.** A redemption and an
unattended caller are the same answer from `should_preview_write()`, and `code.py` briefly
staged unconditionally instead of asking — handing an unattended caller "nothing to bind this
confirmation to" where it should have run the script.

**A redeeming call must not preview again.** `registry.dispatch` sets `_redeeming` when it
pops a staged entry, and `should_preview_write()` answers `False` under it — checked before
any surface or permission question, because the user's ✓ is not a request for another ✓.
Without that flag the propose tools would stage a *second* identical card on approval, which
is the exact defect this ADR exists to remove, one layer down.

The card carries `grant_tool`, which is what makes it ordinary. Both surfaces already treat
that key's presence as *"resolve server-side by `tool_use_id`, and render Allow once / This
session / Always allow / Deny"* — so emitting it is the whole integration, with no per-type
branch on either surface. These 13 gain the standing-grant control they never had.

Deleted, not deprecated:

- `PendingPlanStore.stage()` / `pop_for_tool()` — the token minter and its redeemer.
- `_confirm_gate.CONFIRM_TOKEN_SCHEMA` — no tool advertises the parameter.
- The `confirm_token` field and the "call it again with ONLY confirm_token" sentence from all
  13 schemas and descriptions.
- `redeem`'s dual-shape lookup — now one `pop_pending_for_tool`.
- Five per-type branches in `connectors.chat.confirm.apply_change`, replaced by one rule:
  **a card carrying a `tool_use_id` resolves that id.**
- The dead confirm/dismiss text branches in `ChangePreview` for the migrated types.

`CONFIRM_TOKEN_KEY` survives as the internal parameter name every handler already reads.
There is no token behind it — the value is a `tool_use_id`, supplied only by the server.

## What did not change

Each tool's handler is untouched, including its redemption block and its
`outcome_mismatch_result` re-verification. `resolve_pending_tool_use` already approves by
calling `execute_tool(tool, {CONFIRM_TOKEN_KEY: tool_use_id})` — the exact channel these
handlers check — and `redeem` already read the `tool_use_id` shape. The card *bodies* are also
untouched: the resolved preview is the point of these tools, and a systematic preview-card
redesign is separate work (owner, 2026-08-25).

`additionalProperties` is in `registry._IGNORED_KEYWORDS`, so the server's approval call
passes validation with a key the schema no longer advertises. That is by existing design, not
a new exemption.

## Consequences

- A ✓ on a position, lot, vest, option or income card writes, on the web, for the first time.
- Those cards gain Always allow / This session, and honour a standing grant like every other
  tool.
- `PendingPlanStore` holds exactly one kind of entry.
- **Every confirm card in the app is on this mechanism.** The lane widened once the 13 were
  done, because the tools left behind were worse, not better: `planning.py::_proposed`,
  `envelopes.py::_proposed`, the three `skills.py` authoring tools and `code.py`'s
  save-as-skill returned `{"proposed": True}` and stored **nothing**. A ✓ there sent Lucius a
  sentence — *"Confirmed — create the \"X\" skill (call apply_skill_change)"* — and he
  re-called the apply tool with arguments he **reconstructed**. What committed was whatever
  he remembered: for a skill, a whole body retyped from context. `run_code` was a third
  mechanism again (a single-use nonce), which kept the SOURCE honest but still routed the
  approval through the model, so a card he never answered simply never ran.
- `connectors.chat.confirm.apply_change` is now literally one rule and a refusal.
  `ChangePreview.handleConfirm` is two branches (`grant_tool`, `external_mcp_call`) and a
  `console.error` — a card with neither is a bug, not a fallback, because answering it by
  asking the model is the path this removed.
- `code.py`'s `_STAGED` nonce pool, `apply_run_code` and `apply_code_skill`'s reachability
  all follow from that: nothing mints a nonce any more.
- `services/proposal_apply.APPLY_TOOL_BY_TYPE` still maps `agent_*` → `apply_agent_change`,
  which is **not a registered tool** — the Agent product was retired in `d0da1a09`. Those
  three entries were already dead before this change and are left for that lane to clear.

## Agent provenance

Found while investigating an owner screenshot of a confirm loop. The mechanism's own docstring
claimed it worked; the claim was tested against the connector path only. The lesson worth
keeping: **"a mechanism that already works" is a measurement, not a premise** — an exemption
granted on an unverified claim outlives the person who made it.
