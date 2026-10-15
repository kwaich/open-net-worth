# A failed session listing is not an empty one — and the chat boots from the saved pointer

- **Date**: 2026-08-06
- **Status**: Accepted
- **Area**: Frontend chat boot (`frontend/src/hooks/useConversations.ts`, `useChat.ts`)

## Decision

Two rules for how the Lucius panel comes up on a cold boot:

1. **`apiFetchSessions()` returns `null` when the listing could not be read**, and `null` never
   mints a conversation. Only a confirmed-empty list (`[]` on a 2xx) means "this account has no
   conversations". The one exception: a failure with *nothing* saved locally still mints, because
   the composer needs a real id to write into and there is no previous thread to strand.
2. **The active conversation and the conversation list are seeded synchronously from
   `localStorage`** (`lucius_active_conversation`, and the new `lucius_conversation_list` — a
   bounded, metadata-only cache of the last server listing). `GET /api/sessions` reconciles both;
   it is no longer on the path to the first paint.

Consequently `useChat`'s `hydrated` requires a **non-empty** conversation id:
`Boolean(conversationId) && hydratedConvId === conversationId`.

## Why

Reported by the owner, 2026-08-06: *"why do we create a new conversation every time the Lucius
chat is loading, before loading the old conversation? especially on mobile."* Two separate
defects wore the same face.

**The flash.** `activeId` and `conversations` started empty, and the saved pointer was read only
*inside* the `/api/sessions` `.then()`. So the first paint had no conversation: `luciusChat`
collapsed it to the `''` sentinel, `loadMessages('')` short-circuits to `null` — which satisfied
`hydratedConvId === conversationId`, since `'' === ''`. The panel therefore rendered a
**fully-hydrated empty conversation**: the header read "New conversation" (`ChatPanel`'s
`activeConversation?.title ?? 'New conversation'`), the empty hero painted, and the starter
suggestions fired a model call against `session_id: ''`. It lasted exactly one `/api/sessions`
round trip — invisible against localhost, the entire screen for a visible beat on a phone, where
Lucius is a full-screen destination and the network is slower.

Nothing needed that request. The messages were already in IndexedDB, addressed by the id sitting
in `localStorage`. The listing is for the *switcher*, and the switcher is not the first paint.

**The ghost.** `apiFetchSessions` returned `[]` for a 401, a 502, a timeout, and a dropped
connection alike, and the boot effect reads `[]` as "mint a new conversation": it PATCHed a fresh
UUID (creating a real server-side index row via `upsert_session_meta`) and **overwrote
`lucius_active_conversation`**. So a single failed request did not just show the wrong thing for a
moment — it stranded the real thread permanently, since the next boot started from the pointer the
failure had left behind, and it left a "New conversation" row on the server. Mobile is where this
bites: resumed background tabs, cellular, a token refresh mid-flight.

This is the same scar as
`2026-08-01-failed-statement-metadata-is-recorded-not-cached-as-fact`: a swallowed failure that
returns the empty value is indistinguishable from the fact, and the caller then acts on the fact.
**Absence must be distinguishable from failure at the seam that knows.**

## Consequences

- A failed listing now degrades to "your last-known list, with your last conversation open"
  instead of "a brand-new empty conversation".
- Conversation *titles* are now in `localStorage` (bounded to `MAX_CONVS`, metadata only). No new
  exposure class: full transcripts already live in IndexedDB on the same origin.
- `updateConversationMeta` is now identity-stable when nothing changed. It fires on every messages
  change — i.e. every streamed token — and previously returned a fresh array each time, re-rendering
  every chat consumer for no change; with a list cache attached to that state, it would also have
  rewritten `localStorage` per token.
- `switchConversation`'s reconcile fetch null-guards, so a failed listing there keeps the
  optimistic row rather than blanking the switcher.

## Not addressed

Server-side "New conversation" rows already minted by past failures are left in place; they are
harmless empty sessions and deletable from the switcher. No migration is shipped for them.

## Agent provenance

Diagnosed and implemented by Claude (Opus 5) at the owner's request, 2026-08-06, from the report
quoted above. Root cause traced through `useConversations` → `luciusChat` → `useChat` →
`chatStore` before any change was made; both defects are pinned by regression tests
(`useConversations.boot.test.ts`, `useChat.emptyConv.test.ts`).
