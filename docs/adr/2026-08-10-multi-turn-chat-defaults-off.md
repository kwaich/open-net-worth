# Multi-turn chat defaults OFF — one message in flight at a time

- **Date**: 2026-08-10
- **Status**: Accepted
- **Area**: Lucius chat turn lifecycle (`backend/api/chat.py`, `backend/ai/assistant.py`,
  `backend/services/profile.py`, `frontend/src/components/chat/lucius/Composer.tsx`,
  `frontend/src/hooks/useChat.ts`)

## Decision

New profile setting `chat_multi_turn_enabled`, default **`False`**.

- **OFF (default):** sending a message while a turn is already running for that
  conversation is refused, not queued. The composer disables Send (Stop stays available
  and unaffected) the instant the conversation is busy; `POST /api/chat` returns `409` as
  a server-side backstop for anything that reaches it anyway (a stale tab, a race, a
  direct API call). Nothing is ever added to `pending_injection`'s queue while OFF.
- **ON:** restores the concurrent-conversations RFC's original mid-turn injection — a
  busy send is queued and spliced into the running turn at its next natural checkpoint,
  exactly as it worked before this ADR.

## Why

The owner asked for "one turn only" as the default. The first implementation of that
request enqueued a busy send exactly as before, but told the running turn's own loop to
never drain the queue. That is unsafe on its own: the only other thing that ever reads
`pending_injection`'s queue is residue-recovery, and it runs *once*, at the very start of
the *next* `POST /api/chat` for that session. Nothing triggers that next POST
automatically — it requires the user to type and send something else. A message sent
while OFF-but-still-queuing therefore sat in "Queued — Lucius will read this at its next
step" **forever**, with no error, no timeout, and no way to notice it wasn't coming. This
shipped briefly (the dev server hot-reloads on save) and was caught live within minutes —
see the "Agent provenance" section.

The fix is not "build an auto-delivery mechanism" — it's simpler: **never let anything
enter the queue while OFF.** If a send while busy is refused outright instead of queued,
there is nothing left for any later turn to forget to pick back up, and no delivery
mechanism is needed at all. This also happens to match what the owner actually described
wanting ("user can't send new message yet") — the pre-RFC hard guard the
concurrent-conversations work replaced, restored as an explicit, opt-out choice instead
of the only option.

### Two enforcement layers, not one

1. **Frontend (primary UX):** `Composer.tsx`'s Send button is disabled while
   `(streaming || sending) && !multiTurnEnabled`; `useChat.ts`'s `sendMessage` silently
   no-ops under the same condition (no error surfaced — matching the exact pre-RFC
   `if (streaming) return` guard, not a toast or an error bubble). Typing stays open;
   only the send action is blocked. Stop is unaffected either way — cancelling a running
   turn is always safe, only starting a *second* one is what OFF prevents.
2. **Backend (defense in depth):** `api/chat.py` raises `409` for the same condition
   before it would otherwise call `pending_injection.enqueue`. This is the backstop for
   whatever the frontend gate cannot reach (a stale browser tab, a second device, a
   direct API call) — normal use should never trigger it.

`assistant.py`'s own mid-turn-drain gate (added first, kept) is now redundant once layer
1 holds — the queue is always empty while OFF, so there is nothing to drain either way —
but it stays as a third, cheap belt-and-braces layer rather than being ripped out.

## What this does NOT change

- The mid-turn injection *mechanism* itself (`_drain_injected_messages`, the fence
  nonce, `pending_injection`, residue-recovery) is unchanged and fully intact — ON
  restores it exactly. This ADR only changes the *default* and adds the refusal gate.
- `ConvSlice.queuedMessages` (the separate list a message sent while busy lives in
  client-side until delivery — see the true-timeline-ordering fix earlier the same day)
  is unaffected: with OFF as the default, that list should simply stay empty in normal
  use, since nothing gets there without Send being enabled first.

## Consequences

- A conversation with multi-turn OFF genuinely processes one message at a time — no
  silent queuing, no messages that can go stale waiting for a turn that already ended.
- Anyone who wants the old always-on injection behavior back opts in per-profile
  (Settings → AI providers → Chat → "Multi-turn chat").
- Any FUTURE feature that wants to deliver something automatically once a turn ends
  (rather than only ever refusing while one is in flight) still needs the auto-delivery
  trigger that was scoped out here — this ADR deliberately does not build it, because
  refusing outright removes the need for it entirely for this feature.

## Agent provenance

Decided by a Claude agent mid-session. The first version of this feature (queue-and-
withhold-drain, default flipped to OFF immediately) was live-tested by the owner within
minutes of shipping and produced exactly the "stuck in Queued forever" failure predicted
above — caught from real screenshots, traced to the missing auto-delivery trigger, and
corrected by redesigning around "refuse, don't queue" instead of building that trigger.
The owner's own words mid-fix: "when multi turn is off, user can't send new message yet"
— which is the refusal design, not the withhold-and-hope-something-delivers-it design
this ADR replaces.
