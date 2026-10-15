# `openAssistantId` is the single source of truth for where a live round writes

- **Date**: 2026-08-10
- **Status**: Accepted
- **Area**: Lucius chat streaming (`frontend/src/state/chatSessions.ts`,
  `frontend/src/state/chatReducer.ts`, `frontend/src/hooks/useChat.ts`)

## Decision

`ConvSlice` gains one new field: `openAssistantId: string | null` — the id of the assistant
message a **live round**'s output is currently landing on, or `null` if no round is open.

It is the only mechanism that decides "where does the next SSE event's content go":

- **Set** whenever a round genuinely opens or resumes: `ensureAssistant` (a fresh bubble),
  `reviveOwningMessage` (a reconnect continuing an already-rendered tool call),
  `resolvePendingToolUse` (re-entering the message a permission card lives on).
- **Read** by every writer: `updateOpenAssistant(ownerId, updater)` looks the message up by
  this id directly — no scanning by role, no scanning by array position.
- **Cleared** at every genuine boundary, and *only* there: `turn_done` (the round is over),
  `message_delivered` (an injected message is always its own exchange — see "Why" below), and
  a send/resume that fails outright (no continuation is ever coming).

Nothing else infers "the live bubble" any more. `ensureAssistant` no longer scans for a
trailing or role-matched message; it checks whether `openAssistantId` still resolves, full
stop. `updateLastAssistant`'s reverse-scan-by-role still exists and is still correct for the
small number of call sites that mean it literally ("the last assistant message" as a
historical fact — e.g. `retryLast` dropping a failed turn) — it is simply no longer used for
routing *live* writes.

## Why

Three live incidents landed within one session, all in the same code, all with the same
shape: a client-inferred "which bubble does this belong to" guess was wrong in a case nobody
had hit yet.

1. **Sticky replay-suppression boolean.** A reconnect that replayed an already-seen tool call
   set `replaying = true`, cleared only by the *next* fresh tool call. A turn whose replayed
   tail led straight into brand-new text with no further tool call never cleared it — every
   real token after the replay was silently dropped ("It's separ" on the live stream, complete
   only after a reload).
2. **`ensureAssistant` checked only the trailing array element.** A message queued into an
   already-streaming turn is appended *after* the live bubble, in true send order, without
   touching the bubble itself. The turn's very next token then read "no live bubble at the
   end" and opened a second one after the queued message — an ORCL/PLTR answer split
   mid-sentence around a queued "and alloy?", the second half landing under the wrong
   question.
3. **No boundary signal for mid-turn injection.** An injected message rides the *same*
   server-side turn (no `turn_done` fires for it — `assistant.py`'s mid-turn injection
   splices it in at a checkpoint between rounds). `rehydrate_messages` already treats any real
   injected text as ending the prior bubble unconditionally; the live path had no equivalent
   signal, so the reverse-scan-for-streaming fix from (2) correctly found the still-open
   pre-injection bubble and kept writing an *unrelated* second question's answer into it — a
   commitment's full answer glued onto a completely different commitment's answer, no
   separation.

Each fix in isolation was correct and narrowly scoped, and each one revealed the next
inference gap the moment the owner exercised a slightly different interaction shape (queue
timing, injection, reconnect). That pattern — three "fixes" to the same underlying guess in
one session — is the actual defect: there was no single, explicit fact for "where does the
next token go." Every consumer (`ensureAssistant`, the replay-skip arming, the tool-block
revival) was deriving its own answer from a mix of array position and the `streaming` flag,
and those two signals do not, on their own, encode turn/round *boundaries* — only "does this
one message currently look mid-stream."

`openAssistantId` replaces all of that inference with one flag, set and cleared at exactly
the moments that are already architecturally meaningful (a round opening, a round ending, an
injection being drained) rather than reconstructed after the fact from message shape.

### Why not just fix `ensureAssistant` again

Bug (2)'s fix (scan for role + `streaming`, not just the trailing element) was still an
*inference* — it happened to be right until the injection case in (3) showed a scenario where
"still `streaming`" and "still the right target" diverge. A fourth interaction shape would
plausibly have broken it again. An explicit pointer set by the handful of places a round can
legitimately open, and cleared by the handful of places it legitimately ends, has no more
inference left to get wrong — the boundary list is now closed and auditable in one field's
doc comment instead of scattered across every reducer branch's reasoning about what
`streaming`/position probably mean right now.

### `streaming` (on the message) still exists, and still matters

`Message.streaming` is unchanged and still drives the typing cursor / disabled-copy UI. It is
now purely a *rendering* fact, decoupled from *routing*. `clearAllStreaming` (sweep every
message's flag) stays as the RFC §5.5 belt-and-braces safety net for a spinner that must never
be stuck, and is deliberately **not** folded into `closeOpenAssistant` everywhere — the attach
effect's natural-disconnect path (`useChat.ts`, `!cancelled` branch) calls `clearAllStreaming`
alone, without touching the pointer, because a plain network drop (not a deliberate
supersession) means the server-side turn may still be running and a later reconnect should
resume the *same* bubble, not treat the round as closed.

## Consequences

- Any new SSE event handler that produces assistant-owned content must call
  `updateOpenAssistant`, never `updateLastAssistant` — and must call `ensureAssistant` (or
  otherwise guarantee the pointer is set) first, exactly like every existing handler in
  `consumeEventStream` does.
- Anything that legitimately re-opens a round outside `consumeEventStream` (currently only
  `resolvePendingToolUse`) must call `openAssistant(ownerId, id)` explicitly before its
  resumed stream starts.
- Tests that seed a slice with a pre-existing `streaming: true` assistant message to simulate
  "there's already an open bubble" must also seed `openAssistantId` to match — a `streaming`
  flag with no pointer is now simply an incomplete seed, not a state the reducer will infer
  its way around.

## Agent provenance

Decided by a Claude agent mid-session, after the owner pushed back explicitly on a string of
narrow patches to the same mechanism ("i feel like you are adding fixes and patches / not
fixing the proper architecture") following three live-reproduced incidents in one sitting,
each traced against the real session transcript on disk rather than guessed from the
screenshots alone.
