---
title: A connector confirm tap is a TURN, rendered by the channel — not a side call
status: Accepted
date: 2026-08-22
deciders: owner + implementing Claude agent
---

# A connector confirm tap is a TURN, rendered by the channel — not a side call

## Context

Owner report, 2026-08-22, on Telegram:

> "after approved permissions, it doesn't send anything else, or completion stream or
> something, i have to ask again"
> "and it seems like i just need to approve one permission then the rest are approved
> automatically too"

Two symptoms, one root cause.

`connectors/chat/confirm.py::_resolve_via_pending_tool_use` called
`assistant.resolve_pending_tool_use` **directly**, from inside the python-telegram-bot
button-tap handler:

```python
async for event in assistant.resolve_pending_tool_use(session_id, tool_use_id, decision, system):
    if etype == "tool_done" and result is None:
        result = ...
    # every other event discarded
```

That is a full model turn — the redeemed tool's real result is appended to the paused
`tool_use` and the model is called again on the complete transcript — driven as if it were
an ordinary function call. Two things follow.

**1. The confirm gate was disarmed for the rest of the turn.** CLAUDE.md already states the
invariant: *"NEVER a bare `asyncio.create_task` around `assistant.run`: `_drive` binds the
cost surface as its first statement, and the confirm gate fails OPEN without it."* This call
site is the same violation by a different route — a tap arrives on a fresh PTB task, so the
`cost_surface` ContextVar the inbound turn set (`connectors/processor.py`) is not in scope,
and nothing on the tap path set one. `ai/_confirm_gate.py::can_render_card(None)` returns
False (fail-closed as a *capability* check), `should_gate` therefore returns False, and
**every remaining gated tool in the resumed turn executed with no approval card**.

Reproduced end to end before the fix (`tests/test_telegram_confirm_resume_e2e.py`): approve
one `create_payees` call, and the resumed turn's *second* `create_payees` call — a distinct
write the user never saw — committed silently.

    assert 'Toast Box' not in {'Kopitiam', 'Toast Box'}

This is not a permission-model bug. `grants.record_grant("once")` correctly stores nothing;
the level lookup is correct; the owner's `once` really did mean once. The gate simply was
not consulted with enough context to say yes.

**2. Nothing the resumed turn produced reached the chat.** The loop above captured the first
`tool_done` and dropped every `text_delta`, `proposed_change` and `artifact`. That was known
and documented as a scope limit ("not (yet) forwarded as a second connector message"), but
the second symptom above is what it costs in practice: after tapping ✓ the user gets an ack
and silence, and has to ask again. It also means a *further* permission card raised by the
resumed turn is invisible — so even with the gate armed, the turn would deadlock without
this half.

The web path never had either problem: `api/chat.py`'s resolve endpoint spawns the same
generator through `turns.spawn(..., cost_surface="web")` and streams its events to the
browser over SSE. The connector path was the odd one out.

## Decision

**A confirm tap resumes a turn, and a resumed turn is rendered by the channel it belongs
to — through the same code path an inbound message uses.**

Concretely:

- `connectors/processor.py::pump_channel_turn` is extracted from `process_message`. It owns
  the channel-facing half of *any* turn: binding the cost surface, registering with
  `turn_registry` (so `/stop` reaches a resumed turn too), typing indicators, prose
  streaming/flushing, proposed-change cards, artifacts, and `turn_done`.
- `connectors/processor.py::resume_pending_tool_use` is the connector twin of the web
  resolve endpoint: it drives `assistant.resolve_pending_tool_use` through that pump and
  returns only the redeemed call's own result, which the confirm seam still inspects to
  decide *applied* vs *failed*.
- `confirm.handle_confirm_action` takes an `adapter=`, and builds a `ChannelTarget`
  **only after the IDOR proposer check has passed** — a resumed turn renders to that
  channel, so an unverified target would be a way to redirect one conversation's output
  into another.

The surface is bound **once, in the pump, for whichever turn is being pumped**. That is the
load-bearing line; everything else is delivery.

### Why not `turns.run_inline`

The stated invariant says every turn goes through `ai/turns.py`. Connector turns already do
not — `process_message` binds the surface itself and tracks cancellation through
`connectors/turn_registry.py` rather than `turns`. Converting the whole connector lane to
`turns.run_inline` is a real change to cancellation and live-episode semantics and is *not*
what this bug needs. The decision here is narrower and strictly consistent: **make the
resumed turn behave exactly like the inbound turn on the same channel.** Unifying the
connector lane with `turns.py` remains open, and is the right place to reconsider it.

### A committed write is never reported as a failure

`resume_pending_tool_use` catches a failure of the *resumed model round* separately: if the
redeemed call already committed (we saw its `tool_done`), the exception is reported to the
channel on its own line and the real result is still returned. Letting it propagate would
render a landed write as "Failed to apply" — the one thing a confirm acknowledgement must
not get wrong.

### An `error` event is spoken aloud

A turn can fail *without raising* — `assistant.run` maps a provider/tool failure into an
`error` event and completes normally. Nothing rendered that event, so the channel went
quiet, which on a phone is indistinguishable from "still thinking" and reproduces the same
"it doesn't send anything else" symptom. The pump now reports it.

## Consequences

- The permission bypass is closed for every chat connector at once, because the fix is in
  the shared pump and the shared confirm seam, not in Telegram's handler.
- A resumed turn can now raise its own permission card, stream prose, deliver artifacts,
  and be stopped with `/stop` — it is a first-class turn on that channel.
- `apply_change` / `_handle_grant_action` / `_handle_deny_action` / `_apply_ledger_confirm`
  grew a **keyword-only** `target=`. Keyword-only deliberately: several existing tests stub
  `apply_change` positionally, and a silently-shifted positional argument in the write-apply
  path is exactly the class of change that should fail loudly at the call site instead.
- The web/SSE path is untouched — `target=None` keeps the original drain, because
  `api/chat.py` already owns rendering there.

## Evidence

`tests/test_telegram_confirm_resume_e2e.py` drives the real path end to end —
`handle_message` → `handle_inbound` → `process_message` → a real `assistant.run` tool loop →
the real `registry._permission_gate` → the real Telegram inline keyboard → `handle_callback`
→ `handle_confirm_action` → `resolve_pending_tool_use`. Only the LLM and the Telegram HTTP
transport are stubbed. All four cases fail on the pre-fix code and pass after:

1. a second gated write in the resumed turn still raises its own card and does not commit;
2. the resumed turn's answer is delivered to the chat;
3. the gate is consulted on a card-capable surface (the mechanism, pinned directly);
4. a failing resumed turn says so, and still reports the committed write truthfully.
