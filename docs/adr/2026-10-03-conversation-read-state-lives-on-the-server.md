---
title: Conversation read state lives on the server, not in one browser
status: Accepted
date: 2026-10-03
deciders: Claude (implementer), acting on an owner bug report
---

# Conversation read state lives on the server, not in one browser

## Context

The sidebar's "New reply" / "Failed" marker asked "did a turn finish since *this browser* last
opened the conversation?". The "last opened" half lived in `localStorage`
(`lucius_conversation_last_viewed`), so it was per browser and per origin.

Owner report (2026-10-03): the marker kept appearing, "sometimes even when I read it in another
client". Three separate causes, found by tracing the status derivation:

1. Reading a reply on a phone, in Telegram or in another window wrote nothing the server — or any
   other client — could see, so it could never clear the marker here.
2. The desktop app deliberately runs its web content in an in-memory partition (`desktop/README.md`:
   "Sessions are isolated in memory"), on a random loopback port. Its `localStorage` is empty on
   every launch, so every conversation with a finished turn was unread again after every relaunch.
3. The in-memory "turn finished" flag was set even for the conversation on screen and only cleared
   when a conversation was *entered*, never *left* — so switching away from a reply you had just
   watched arrive flagged it, until a reload.

(2) is a security choice, not a bug: persisting a signed-in session to disk is out of scope here.

## Decision

We will keep the read stamp on the session meta as `last_read_at` (server clock, ISO-8601 UTC) and
serve it through `GET /api/sessions` beside `last_turn_at`.

- `POST /api/sessions/read {ids}` (1–200) stamps existing sessions and returns the stamp stored per
  id. `SessionStore.mark_read` never creates a row (an unsent draft is skipped), never moves a
  stamp backwards, and never touches `updated_at` — reading is not activity, and that field sorts
  the list.
- "Unread" is `last_turn_status ∈ {ok, error}` **and** `last_turn_at` after the later of the
  server's `last_read_at` and this browser's own optimistic stamp. `last_turn_at`, not
  `updated_at`: the person's own message moves `updated_at` but is not a reply.
- Both clocks in that comparison are the server's. The client adopts the server's stamp from the
  response in place of its local one; the local stamp is only the instant, offline fallback.
- A listing that predates a mark can land after it, so `mergeServerRows` keeps the later
  `lastReadAt`.
- Timestamps are compared as instants (`utils/isoTime.ts`), never as strings — the browser writes
  `…Z`, Python writes `…+00:00`, and `+` sorts before `.`.
- The client marks read on switch-in, on switch-out, and (debounced) as the viewed transcript
  settles; the debounce lets a turn's own end-of-turn write land first.
- The window re-reads the listing when it regains focus, so a read made elsewhere reaches an
  already-open window without a reload.
- The Lucius history rail gets a **Mark all read** action per group; it clears every unread row in
  the group, not just the visible ones, and never a `Working…` / `Needs you` one.

## Consequences

### Positive

- Reading anywhere clears the marker everywhere; a relaunched desktop app no longer re-flags
  history.
- The marker no longer depends on any one clock or on browser storage surviving.
- Automation runs, which are rarely opened, can be cleared in one action.

### Negative

- A "read" is now a server write (one small POST per switch, debounced while viewing).
- Read state is per *server*, not per person-per-device: two people sharing one instance share it.
  That matches the product — one book, one owner — but is a decision, not an accident.
- Cross-device propagation is eventual for an open window (next focus / switch), not pushed.

### Follow-ups

- The collapsed history hover list and the dock's `ConversationMenu` still have no Mark-all-read
  control; they read the same state.

## Alternatives considered

- Persist the desktop partition (`persist:`) — rejected: it is a deliberate isolation choice, and
  the random port would key a fresh storage bucket every launch anyway.
- Pin a stable desktop port so `localStorage` survives — rejected: still per-browser, so it fixes
  (2) only and leaves (1).
- Push read state over `GET /api/events` — rejected for now: eventual consistency on focus is
  enough, and it keeps session meta out of the data-change bus.
- Drop the in-memory "done" flag and rely on the server record alone — rejected: it delays a
  background turn's marker by up to a live-poll interval (4s).

## Agent provenance

- **Decided by:** Claude, implementer, working directly with the repo owner.
- **Lane / branch:** `main`, uncommitted working tree.
- **Alternatives weighed:** persist desktop storage vs. stable desktop port vs. event-bus push vs.
  server-side `last_read_at` (chosen).
- **Driving artifacts:** owner bug report with a screenshot of the Lucius history rail;
  `desktop/README.md` (sessions isolated in memory); `frontend/src/state/luciusChat.tsx`
  `resolveRowStatus`.
- **Why this way (one line):** "read" belongs to the person, so it has to live where every client
  can see it.
