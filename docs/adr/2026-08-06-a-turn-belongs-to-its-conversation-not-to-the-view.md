# A turn belongs to its conversation, not to the view

- **Date**: 2026-08-06
- **Status**: Accepted
- **Area**: Lucius chat streaming (`frontend/src/state/chatSessions.ts`, `frontend/src/hooks/useChat.ts`,
  `frontend/src/state/luciusChat.tsx`, `backend/api/chat.py`)

## Decision

Three rules, in force from the concurrent-conversations change
(`.claude/rfcs/concurrent-conversations.md`):

1. **Streaming state is stored per conversation, and the view is a selector over it — never its
   owner.** `frontend/src/state/chatSessions.ts` holds a slice per conversation id (messages,
   status, abort controller, suggestions). The panel, the dock and the mobile shell read the slice
   for the conversation they are showing. There is no "current conversation's messages" variable.

2. **An event's owner is captured when the request starts and is never re-read from the view.**
   `consumeEventStream` takes an explicit `ownerId`. Every reducer write goes to that slice.

3. **A write to a slice whose id does not match the event's owner is REFUSED and logged, not
   applied.** The stream is already routed by owner; this check is the second wall. From Phase 2
   the server stamps `session_id` and `turn_id` on every event, so the check verifies against
   server-supplied data rather than a closure variable, and an event from a superseded turn is
   discarded rather than rendered.

## Why

Lucius had exactly one `useChat()` instance for the whole app (`luciusChat.tsx:105`),
parameterized by whichever conversation was on screen. Its SSE reducer wrote through
`updateLastAssistant`, which finds the last assistant message **in the state currently held** — so
it wrote into the conversation being *viewed*, not the conversation that *owned the request*.

That defect was invisible because it was covered by guards: `ChatPanel.tsx:540-548` and
`LuciusDock.tsx:355-362` hard-returned on `streaming`, so you could not switch conversations or
start a new one while a turn ran. The guards read as a product limitation ("one chat at a time")
and were in fact load-bearing correctness machinery. Remove them without fixing ownership and
conversation A's tokens land on conversation B's bubble.

The owner's complaint was the product limitation — a ninety-second turn blocks the whole
assistant, and you cannot even go *read* another thread. Fixing that required fixing the ownership
error underneath it, which is the real content of this ADR.

A latent instance of the same error was found and fixed alongside: `luciusChat.tsx:83-88` built
`onMessagesUpdate` closing over `conversationId`, so under concurrency it would stamp the viewed
conversation's metadata with the streaming conversation's message count and title. It was harmless
only because concurrency was impossible.

### Why an external store rather than React state

The obvious implementation is `useState<Map<convId, ConvSlice>>` in the existing provider. It is
correct and it was rejected on cost: a new Map identity per token re-renders every consumer of the
context — dock, panel, mobile shell, every message bubble. The whole point of the change is to
have several token streams running at once, so the fan-out is multiplied by the number of live
turns. An external store consumed via `useSyncExternalStore(subscribe(id), …)` notifies only that
slice's subscribers. Barely more code; removes the storm.

### What this does NOT change

One in-flight turn per conversation (INV-1) still holds — the transcript is a whole-file rewrite
(`assistant.py:398-410`) and cannot take two concurrent writers. This was already enforced
server-side by `_get_session_lock()` (`assistant.py:378`), which we discovered rather than added.
There is **no cap** on how many *different* conversations may run at once (owner decision,
2026-08-06); `backend/db/json_store.py` already holds a per-path write lock across the full
read-modify-write cycle, so concurrent tool writes cannot lose updates.

## Consequences

- Any new chat surface reads a slice by conversation id. Reaching for "the messages" without an id
  is the bug this ADR exists to prevent.
- New reducers must take an `ownerId`. A reducer that closes over the active conversation is
  reintroducing the defect.
- The guards are gone; switching and starting are never blocked. Anything that wants to block them
  again is almost certainly papering over an ownership bug instead of fixing it.

## Agent provenance

Decided by a Claude agent (orchestrator, branch `feature/concurrent-conversations`) while scoping
the owner's question "are we not supporting switch chats and have multiple chats ongoing at the
same time for Lucius?". The prior art was inspected rather than recalled: Claude Code's on-disk
session store on this machine stamps `sessionId` on **every record** in its append-only per-session
JSONL, which is what rule 3 above is copied from — ownership travelling with the data means a
record cannot be applied to the wrong session even when routing is wrong.

The judgement call worth recording: the first draft of the RFC justified a concurrency cap partly
on data safety. That was wrong, and reading `json_store.py` disproved it — the per-path write lock
already prevents lost updates. The cap would only ever have been a cost throttle, and the owner
declined one. Recorded because "we added a limit for safety" is exactly the kind of claim that
survives unexamined for years once it is in a comment.
