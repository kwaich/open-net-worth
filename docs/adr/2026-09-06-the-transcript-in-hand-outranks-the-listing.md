---
title: The transcript in hand outranks the listing; a refused approval card is terminal
status: Accepted
date: 2026-09-06
deciders: orchestrating Claude agent + repo owner
---

# 2026-09-06. The transcript in hand outranks the listing; a refused approval card is terminal

## Context

On 2026-09-06 the owner's instance briefly served two divergent copies of its database on one
port (see `2026-09-06-database-ahead-of-code-is-refused-untouched.md`). The chat client had no
defect of its own in that story, but it amplified the split into three visible failures on a
phone, and each one traced back to the same design assumption: that the server's *conversation
listing* is at least as truthful as the *transcript the client already holds*.

1. The moment a turn ended, the panel painted the empty-conversation greeting over a
   conversation with six messages on screen. `isHomeEmpty` read only
   `activeConversation.messageCount` from the listing, which a stale copy reported as `0`. The
   greeting is suppressed while streaming, so the flip landed exactly when the answer arrived.
2. The conversation list refresh that fires when a turn leaves the live set replaced the local
   row (count 6, real title) with the server's (count 0, "New conversation"), so the thread was
   unrecognisable in the history menu.
3. The user tapped "Allow once" on an approval card; the server could not redeem it and
   streamed the card-scoped error. The reducer un-painted the optimistic tick and
   `ChangePreview` re-armed the button. A second tap reproduced the refusal. A refused
   `tool_use_id` can never be redeemed (`2026-07-30-approvals-never-expire.md`,
   `2026-08-25-one-confirm-mechanism-for-every-tool.md`), so the re-armed button was a lie.

## Decision

- **A listing may never shrink a conversation this client has content for.**
  `useConversations.mergeServerRows(prev, rows, { keepLocalOnly })` is the one merge for every
  listing fetch. Server rows win for everything except: a server row with `messageCount === 0`
  for an id whose local row has `messageCount > 0` keeps the local count and title.
  `keepLocalOnly` answers a separate question — whether rows the server did not mention survive
  — and stays `true` only for the live-set refresh (an optimistic adopt the server has not caught
  up on). Boot and `switchConversation`'s authoritative refetch pass `false`, so a conversation
  deleted on another device is still evicted from the boot cache.
- **A hydrated slice with messages vetoes the greeting.** `computeHomeEmpty` returns `false`
  whenever `hydrated && sliceMessageCount > 0`, whatever the listing says; otherwise the
  first-paint rule (listing count, available synchronously from the boot cache) is unchanged.
  The veto is one-directional, so the layout flip the original rule was written to prevent —
  bottom-pinned composer snapping to centered once hydration confirms zero — cannot return.
- **A refused approval card is terminal.** `change.refused === true` renders one status row,
  `Not run — can't be reused.` (`role="status"`, the existing `.change-confirmed` row in the
  muted tone the Denied row already uses), in place of the buttons of every card variant: the
  grant split button, the external MCP card and the code-run three-way row. The proposal body
  stays visible; the message-level error below the card carries the why. No action lives on the
  row — the composer is where the user asks again.

## Consequences

- A stale or partial listing can no longer blank a conversation the user is looking at, nor
  rename it under them. The cost is that a genuine server-side shrink to zero of a conversation
  this client still holds is not reflected until the client's own slice changes; a conversation
  never legitimately shrinks to zero while the client has messages for it, so nothing real is
  lost.
- The greeting decision now has two inputs. `homeEmpty.ts` is a pure function with its own
  tests so the precedence is explicit rather than rediscovered from `ChatPanel`.
- A refused card cannot be retapped. Recovering the action is a new proposal from Lucius, which
  is the only path the backend actually supports.

## Alternatives considered

- **Trust the listing and fix the server instead.** The server-side cause was fixed the same
  day, but a client that repaints a live conversation as empty on any stale read is fragile
  against every future cause (a slow replica, a cache, a partial outage). The client already
  holds the ground truth; using it is cheaper than perfecting every source of the listing.
- **Keep every local row on every merge.** Rejected: at boot the listing is authoritative for
  existence, and keeping unknown rows would resurrect conversations deleted elsewhere forever,
  since the boot cache is rewritten from state.
- **Re-arm the refused card but with a warning.** Rejected: the button would still do nothing,
  and the pattern in this file is that a resolved card shows a status row, not a second chance.
- **Add a "Propose again" button on the refused row.** Rejected for now: no sibling terminal row
  carries an action, and the product rule is that the composer is where the user asks again.

## Agent provenance

Diagnosed by the orchestrating agent from the server log and the live listing on 2026-09-06;
implemented on `worktree-fix-db-ahead-and-approval-card` by two implementer agents (a
senior-engineer lane for the approval card, a junior-engineer lane for the merge and the
greeting), reviewed by code-reviewer, ui-designer (copy and `role="status"`) and qa-engineer
lanes. Pinned by `frontend/src/hooks/useConversations.merge.test.ts`,
`frontend/src/components/chat/homeEmpty.test.ts`,
`frontend/src/components/chat/ChangePreview.refused.test.tsx`,
`frontend/src/state/chatReducer.refused.test.ts` and the rewritten refused case in
`frontend/src/components/chat/ChangePreview.grant.test.tsx`; each was confirmed to fail against
the pre-fix code.
