---
title: A chat connector accepts a second message mid-turn, and can show what it is doing
status: Accepted
date: 2026-08-23
deciders: owner + implementing Claude agent
---

# A chat connector accepts a second message mid-turn, and can show what it is doing

## Context

Owner, 2026-08-23: *"let's do mid turn messages support -> basically it's supporting multiple
message, right like web? and do the tool call streaming too, but keep it behind a config."*

Two gaps, both found while fixing the confirm-tap bug, both rooted in the same thing: the
connector path renders and routes turns itself instead of sharing the web path's machinery.

**No running-turn check existed.** `executing_turn_id` appears in `api/chat.py` and nowhere
under `connectors/`. A second Telegram message hit the per-session `asyncio.Lock` in
`assistant.run`, waited, and ran as its own turn afterwards. Nothing was lost — the lock
prevented corruption — but a correction ("no, just June") could never reach the turn it was
correcting, because by the time it ran the first turn had already finished.

**Tool activity was dropped.** `pump_channel_turn` handled six event types and
`tool_use_start` was not among them, so an eight-tool answer showed `typing…` and nothing
else — indistinguishable from a stall. The web client has drawn these all along
(`chatReducer.ts`).

## Decision — mid-turn injection

Route on the connector side exactly as `api/chat.py` does, because both feed the **same**
queue and the **same** drain (`assistant._drain_injected_messages`, keyed by session id).
Three rules were copied deliberately, and each is load-bearing:

1. **Gated on the same `chat_multi_turn_enabled` profile flag.** The DRAIN is gated on it
   (`assistant.py`), so a connector-only toggle could enqueue into a drain that is off —
   a black hole. One switch for "Lucius accepts a second message while working", not one
   per transport.
2. **Keyed by SESSION, never by channel.** `turn_registry` is keyed `(connector, channel_id)`
   because `/stop` is a channel gesture. Injection is a session question, and one channel can
   hold several sessions — an untrusted workspace keys them per sender
   (`chat.run.session_key_for`). Routing on the channel key would queue sender B's message
   onto sender A's session, where nothing would ever drain it. `turn_registry` therefore
   gained a SECOND index, `_active_by_session`, and `is_running_for_session()` — the
   connector twin of `turns.executing_turn_id`.
3. **Re-checked after enqueueing, with no `await` between.** Single event loop, so both spans
   are atomic; if the turn ended in the gap, fall through and start a normal turn, which
   drains the queue at its own first checkpoint.

### Two traps worth recording

**A module-level `from backend.ai import pending_injection` in `chat/run.py` deadlocks the
package.** Verified directly: `import backend.connectors.chat.run` hangs. `backend.ai` pulls
in the assistant, which reaches back into `connectors`. The import is therefore made inside
the function, the same deferral `session_title` already uses for its own `ai` import.

**A local `from backend.connectors import turn_registry` inside `handle_inbound` shadows the
module-level name for the WHOLE function body.** It sat in the `/stop` branch; once the
router referenced `turn_registry` earlier in the same function, every non-`/stop` message
raised `UnboundLocalError`. The local import was removed, not the module one.

## Decision — tool activity, behind a toggle

`pump_channel_turn` renders `tool_use_start` as a **separate, edited-in-place status
message**, deleted the moment real output lands.

- **Separate from the prose stream** because the two interleave (talk, call tools, talk
  again); editing one message for both would make the answer flicker between prose and
  status.
- **Deleted on first text / card / turn_done**, so the finished conversation reads exactly as
  it did before this existed. Nothing new is left in the transcript.
- **Label from `display.label`** (`ai/tools/display.py`), resolved server-side, so the phone
  and the web app share one vocabulary instead of each inventing its own.
- **Throttled by the existing `_STREAM_INTERVAL`** (1.2s), inside Telegram's ~1 edit/sec
  envelope, and capped at `_TOOL_STREAM_MAX_LINES` (6) — a long turn should read as "what is
  it doing NOW", not as a scrolling audit log.
- **Asked of the ADAPTER** (`tool_stream_enabled()`, duck-typed like `supports_streaming`),
  so the pump stays channel-neutral and a connector without the toggle simply opts out. A
  config read that raises is treated as off, never as an error.

Stored as `tool_stream` on `TelegramConfigStore`, **default on**, toggled by `/toolstream`
(with the same inline on/off keyboard `/streaming` uses). Behind a config at the owner's
explicit request: it costs one extra message per turn, and quiet is a fair preference.

## Consequences

- Both features are in the SHARED pump and the SHARED inbound seam, so Slack/Discord/WhatsApp
  inherit them the day they are added.
- Mid-turn injection is OFF by default, because its flag already was — turning it on is one
  profile setting and now changes web and connectors together.
- `turn_registry` carries two indexes for one task. They are cleaned up independently and
  each only clears when it still points at the same task, so a newer turn is never clobbered.
- A connector turn still does not go through `ai/turns.py`; this borrows the injection
  machinery without unifying the lifecycles. That unification remains open — see ADR
  `2026-08-22-a-connector-confirm-tap-is-a-turn-not-a-side-call`.

## Evidence

`tests/test_connector_mid_turn_injection.py` (5) and `tests/test_connector_tool_stream.py`
(4). The injection file includes an END-TO-END case driving the real `pump_channel_turn`
rather than a stubbed `process_message` — without it, the production registration could be
missing while every stubbed test still passed, which is exactly the gap that made the
routing check dead code on the first attempt.
