# Mid-turn injection rides as a separate `role: "user"` message, verified against the live API

- **Date**: 2026-08-06
- **Status**: Accepted
- **Area**: Mid-turn message injection (`backend/ai/assistant.py`, `backend/ai/pending_injection.py`,
  `backend/ai/turns.py`, `backend/api/chat.py`, `backend/core/untrusted.py`,
  `frontend/src/hooks/useChat.ts`, `frontend/src/state/chatReducer.ts`)

## Decision

A message typed into a conversation while Lucius is mid-turn is delivered as a **separate**
`{"role": "user", "content": [...]}` history entry, appended immediately **after** the pure
`tool_results` message a tool round just produced — never mixed into that same message as an
extra content block. This is candidate **(A)** from
`docs/review/concurrent-conversations-security.md` finding 2. `_is_tool_result_reply`'s docstring
invariant ("`tool_results` entries are built exclusively from `_tool_result_block` and are never
mixed with a text/image block") is therefore preserved exactly, unchanged — `resolve_pending_
tool_use`, `_abort_stale_pending`, and `_turn_boundaries` needed no changes to their predicate.

## Why — validated empirically, not assumed

The RFC's original text for this section said the injected message "rides as an extra text block
appended to the `tool_results` list" (candidate B, the mixed shape) — flagged by security review
as dangerous: it breaks `resolve_pending_tool_use` (wedges the conversation permanently behind an
unresolvable approval), `_abort_stale_pending` (an armed money card is never superseded), and
`_turn_boundaries` (cost rows mis-join to bubbles on reload). The review recorded candidate (A) as
the safer alternative but flagged its own risk: two consecutive `role: "user"` messages might
themselves 400 on the provider ("roles must alternate between 'user' and 'assistant'" is a real,
documented Anthropic error string) — the task explicitly required validating this against the real
API rather than assuming either way.

**The probe.** A live call against `claude-haiku-4-5` (via the Anthropic SDK, using the instance's
own configured key, no other app code involved) built exactly the shape candidate (A) produces: a
`tool_use` → a pure `tool_result` reply message → a **second, separate** `role: "user"` message
carrying real text — and sent it to `POST /v1/messages`. Result: `200`, `stop_reason: "tool_use"`,
no error. This matches the Anthropic API skill's own current documentation ("Consecutive same-role
messages are allowed — the API combines them into a single turn"), not the stale "roles must
alternate" line that also appears in the same doc set's generic error-code table. Given both were
present in the same reference material, the live call was the deciding evidence, not either cached
claim.

**Given both shapes are viable, (A) was chosen per the task's own tie-break** ("if both work,
prefer the one that keeps the existing invariant intact") — it needed zero changes to
`_is_tool_result_reply`, the two merge sites, or their docstrings, whereas (B) would have required
loosening the predicate to "contains at least one tool_result" and rewriting the two call sites to
splice into a prefix.

## What this decision required, end to end

- **`backend/ai/pending_injection.py`** — a tiny per-session FIFO queue. Running turns use
  `enqueue`/`drain`/`has_pending`; fresh-turn residue recovery uses an exact-object
  `claim`/`ack`/`nack` handoff. Race-freedom argument (see the module's own docstring): the
  caller's registration-check-then-enqueue and the running turn's own drain-then-decide are each a
  single synchronous span with no `await` inside — on a cooperative event loop two such spans can
  only interleave whole-before-whole, so a message is provably never lost to a race between "is a
  turn running" and "the turn just finished" — **except** a turn that crashes or is cancelled
  before reaching either checkpoint, which `api/chat.py` closes separately (below).
- **`backend/ai/turns.executing_turn_id(session_id)`** — the injection-routing authority (finding
  7a). Deliberately **not** `live_sessions.is_live()`: that also reads `True` for a window around a
  turn that just parked for the user's own approval decision (`assistant.has_pending_approval`),
  and injecting into that dead end would silently drop the message with nothing left to drain it.
- **`assistant._drain_injected_messages`**, called at the two places a running turn naturally
  checks whether it has more to do: right after a tool round's results land (acceptance C1 —
  "delivered alongside the next tool result"), and once more right before the turn would otherwise
  finalize (acceptance C3 — a message arriving as the turn ends continues the turn instead of
  vanishing). Each drained message: (1) calls `_abort_stale_pending(history)` first, exactly as a
  fresh turn does at its own start — including the case where the abort supersedes an approval
  card the *same* tool round just staged, which required re-deriving whether to still show that
  card from `PendingPlanStore` rather than trusting the round's own `emitted_proposed` flag (which
  cannot know about an abort that happens after it was set); (2) sanitizes the text
  (`core.untrusted.sanitize_untrusted`) and wraps it in a fresh-per-turn, nonce-bound fence
  (`fence_untrusted_block`) — the one fence in this codebase whose polarity is inverted: it marks
  *trusted* content, which is exactly why the marker has to be unforgeable rather than merely
  documented (finding 4, acceptance C8); (3) re-binds the audit scope's `intent` (finding 9,
  acceptance C7) so a ledger write the injected message causes attributes to itself in
  `txn_audit.json`; (4) yields `message_delivered` carrying the **client's** message id, never the
  fence nonce.
- **`rehydrate_messages`** unwraps the fence back to plain text so a reloaded transcript shows the
  injected sentence as its own bubble (acceptance C7's second half), not the fence plumbing.
- **`api/chat.py`**: `POST /api/chat` checks `turns.executing_turn_id` first. Live → enqueue,
  return `202 {"accepted": "queued"}` (no 409 — the owner asked for injection, not a rejection;
  `_get_session_lock` already serializes the alternative). Not live → claims any **residue** left
  by a turn that crashed/was cancelled before draining its own queue, and passes the exact
  `QueuedMessage` objects to the assistant ahead of whatever the user just typed. The assistant
  persists every recovered visible message and corresponding private skill invocation with
  `save_session_durable` before it calls back to ack the claim or emits `message_delivered`.
  Disk, permission, or read-back verification failure therefore emits no delivery and nacks the
  exact objects. A started `_events` generator nacks in its own `finally` before failure escapes
  to `turns._drive` and releases execution ownership; the task-done callback is retained only for
  cancellation before the generator starts. `nack` prepends the claim ahead of later arrivals,
  preserving FIFO without duplicates.
- **Frontend** (`useChat.ts`, `chatReducer.ts`): `sendMessage` was unconditionally opening a
  second assistant bubble and POSTing again on every send, "deliberately not gated on streaming"
  per its own comment — that was fine before this change (the second POST just queued behind the
  first on the session lock and eventually ran for real) but would have left a **permanently stuck
  bubble** once busy-sends started returning `202 queued` with nothing to stream into it. Fixed to
  capture `busy = status === 'streaming'` once, skip opening a second bubble when busy, mark the
  new message `pending: 'queued'`, and send `client_message_id` so the server can echo it back. The
  `Message.pending` field and the `PendingChip` component already existed (built ahead of this
  lane, per the owner's brief) — nothing about the chip itself changed. `consumeEventStream` gained
  one new branch: `message_delivered` clears the named message's `pending` field. Both are covered
  by new unit tests: `frontend/src/hooks/useChat.injection.test.ts` (a busy send adds exactly one
  message, marked pending, with `client_message_id` matching; an idle send is unaffected) and
  `frontend/src/state/chatReducer.messageDelivered.test.ts` (delivery clears the named message
  only, is a no-op on an unknown id, is idempotent).

## Alternatives considered

**Candidate (B), the mixed-block shape.** Rejected per the tie-break above once (A) was confirmed
to work — not because (B) doesn't work, but because it's strictly more invasive for no
demonstrated benefit: it would touch three functions this task's own scoping (`_is_tool_result_
reply`'s docstring, `resolve_pending_tool_use`, `_turn_boundaries`) explicitly warned against
touching without a reason.

**Trusting the cached "roles must alternate" doc line and picking (B) without a live check.**
Rejected — the task explicitly required empirical validation, and the same reference material
contained a directly contradicting, more specific claim ("consecutive same-role messages are
allowed"). Guessing between two contradicting cached claims for a change whose failure mode is "the
conversation is permanently wedged" was the wrong risk trade.

**A frontend-side auto-resend timer** (if no `message_delivered` arrives within N seconds, resend
as a fresh message) as the *sole* safety net for the crash/cancellation residue case, instead of
the backend's own residue fold-in. Not built as the primary mechanism: it's a real UX affordance
worth having independently, but a purely client-side guarantee is not a *server-side* guarantee —
"never silently dropped" is asserted here as a backend property, so the backend closes the gap
itself; a frontend retry is complementary, not load-bearing, and is left out of this change's scope
(no auto-resend implemented — the stuck chip is honest, not silently wrong, on a bare network
failure while busy).

## Consequences

- The fence label `user_msg` and the nonce format (`secrets.token_hex(16)`, matching every other
  fence in `core/untrusted.py`) are now part of the model-facing contract — the system prompt
  (`ai/prompts.py`) names it explicitly, beside the existing FENCED CONTENT rule, as the one
  exception whose content is trusted.
- Mid-turn injection is **text-only** for this change — attachments, `@`-mentions, screen context,
  and slash-command detection are not threaded through the injection path (the route extracts only
  the plain-text portion of the new message when live). Sending any of those while busy is
  silently ignored on the injection path today; scoped out deliberately given the acceptance
  criteria only ever exercise plain text, not left in as an oversight.
- A future turn initiator that wants "never silently dropped" for its own messages must go through
  `pending_injection` the same way — a bespoke queue would not get the fail-loud durable
  claim/ack/nack handoff `api/chat.py` performs on residue for the enclosing session.

## Agent provenance

Decided by a Claude agent implementing the injection phase of `.claude/rfcs/concurrent-
conversations.md`, after `docs/review/concurrent-conversations-security.md` flagged the RFC's
original mixed-block design as dangerous. The task explicitly required resolving the A/B choice
with evidence rather than a guess, and to stop rather than ship a guess if the choice couldn't be
resolved safely — the live probe against the real Messages API (script + full request/response
retained in the session transcript, not reproduced here since it carries no lasting information
beyond its `200`/`stop_reason` outcome) is that evidence.
