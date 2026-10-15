---
title: /stop command to cancel an in-flight connector turn (Telegram + generic chat)
status: Accepted
date: 2026-07-20
deciders: implementing agent (Claude) + repo owner
---

# /stop command to cancel an in-flight connector turn

## Context

Real incident (2026-07-20): the owner accidentally sent a costly message over Telegram ("return
all my July transactions") and **could not stop it** — the model streamed a large response and
kept billing while nothing the owner sent could interrupt it.

Two root causes, confirmed by reading the code:
1. **The turn couldn't be reached to cancel it.** The Telegram bot's `Application` ran with the
   default *sequential* update dispatch, and the common message path awaited the turn inline in
   the handler. So the dispatcher was busy for the whole turn — any `/stop` sent mid-turn queued
   *behind* it and fired only after it finished.
2. **Even if reached, cancelling the client stream didn't stop the LLM.** The OpenAI-compatible
   provider (`stream_chat`, the OpenRouter/GLM path) consumed the upstream stream with a bare
   `async for` — no `async with`/close — so a disconnect abandoned the request to GC/backpressure
   instead of tearing it down (unlike `anthropic_provider`, which uses `async with`).

The two comparable open-source assistants have the **same class of unsolved bugs**: OpenClaw
(#20600 interrupts-during-generation, #27332 abort-hits-gap-between-runs, #51046 stop-should-
cancel-debounce) and Hermes (#26813 `/stop` fed as steer text, #22176 doesn't halt). Both use a
*cooperative* interrupt flag polled *between tool calls* — which by construction cannot stop a
single long generation, exactly the runaway-dump failure mode.

## Decision

We will add an explicit **`/stop`** command that cancels the in-flight turn for the sender's own
channel, and make the turn actually *reachable* and the LLM call actually *stoppable*:

1. **`connectors/turn_registry.py`** — a process-local registry keyed by `(connector, channel)`
   tracking the active turn's asyncio task and any pending debounce/flush task. `stop()` cancels
   both. Cancelling raises `CancelledError` into `assistant.run`, whose `finally` `aclose()`s the
   turn generator → the provider stream is torn down → generation **and billing** stop.
2. **`openai_provider.stream_chat`** closes the upstream stream on any early exit
   (disconnect/cancel/error) via a `try/except BaseException: close(); raise`, mirroring
   `anthropic_provider`'s `async with`. This is the hard-cancel that OpenClaw/Hermes lack.
3. **Telegram** runs with `Application.builder().concurrent_updates(True)` so a `/stop` update is
   dispatched concurrently with the running turn instead of queuing behind it, plus a
   `CommandHandler("stop", handle_stop)`. The debounce flush task is registered so `/stop` cancels
   a not-yet-launched batched turn too (OpenClaw #51046).
4. **Generic chat** (`chat.run.handle_inbound`, used by Slack/Discord/WhatsApp — each inbound is a
   separate concurrent webhook request) detects `/stop` before the rate-limit/session setup and
   cancels instead of starting a turn.

Scope: the explicit `/stop` command **only** — bare "stop"/"abort" natural-language text is *not*
a trigger (too easy to hit mid-conversation). One sender stops only their own channel's turn.

## Consequences

### Positive
- A costly/runaway turn can be stopped mid-generation over any chat connector; the LLM call is
  actually cancelled (tokens stop), not just the visible output — a *harder* stop than
  OpenClaw/Hermes achieve.
- `concurrent_updates(True)` also fixes head-of-line blocking generally: one user's long turn no
  longer blocks other users' messages on the same bot.

### Negative
- `concurrent_updates(True)` changes Telegram's global dispatch to concurrent. The `_text_batches`
  debounce critical section is race-free (no `await` between its read and write, so cooperative
  scheduling keeps it atomic) and same-session turns are serialized by `assistant.run`'s lock, but
  it is a live-behavior change that can't be fully unit-tested against the polling loop — needs
  bot QA.
- **Known gap** (documented, matches OpenClaw #27332): the microsecond window between a debounce
  task spawning a turn and that turn registering as active — a `/stop` landing exactly there finds
  nothing to cancel. Re-sending `/stop` cancels it once registered. Deliberately not closed with an
  abort flag (a flag correct for "drain the queued run" also wrongly skips a fresh message sent
  right after the stop — the note_input/consume_abort conflict).
- Telegram photo/document turns still run inline via `process_message` (not through the
  fire-and-forget seam); `/stop` reaches them via `concurrent_updates`, and they're registered like
  any turn, so cancellation works — but this wasn't exercised by a test.

### Follow-ups
- Bot-level QA of the Telegram concurrent `/stop` path (can't be unit-tested).
- Optional: a hard per-turn cost/token ceiling (auto-abort a runaway even with no human watching)
  — flagged, out of scope here.
- Shared-workspace nuance: the registry keys by `channel_id`, so on an untrusted shared channel a
  `/stop` cancels whatever turn is active on that channel (not per-sender). Acceptable for v1
  (private operator bots key channel==user); revisit if shared-channel concurrency matters.

## Alternatives considered
- **Fire-and-forget the turn as a background task (keep sequential dispatch)** — rejected: it
  requires restructuring `_dispatch_text`/`handle_photo` into detached tasks with in-task error
  handling and task-reference bookkeeping, interacting with the debounce; more code and more edge
  cases than `concurrent_updates`, whose only real risk (the batch race) is moot because the
  critical section has no `await`.
- **Cooperative interrupt flag polled between tool calls** (OpenClaw/Hermes model) — rejected: it
  cannot stop a single long generation, which *is* the incident.
- **Bare natural-language stop triggers** ("stop"/"abort") — rejected for now (owner: `/stop`
  only); too easy to fire by accident mid-conversation.
- **Abort flag to close the gap-between-runs race** — rejected: no correct semantics that both
  drains a queued run and lets a fresh post-stop message through (documented as a known gap).

## Agent provenance

- **Decided by:** implementing agent (Claude), interactive session
- **Lane / branch:** `feat/batch-aware-dedup`
- **Alternatives weighed:** concurrent_updates vs. fire-and-forget tasks; hard-cancel vs.
  cooperative flag; `/stop`-only vs. NL triggers; abort-flag vs. documented gap.
- **Driving artifacts:** owner cost incident; comparative research into OpenClaw (#20600/#27332/
  #51046) and Hermes (#26813/#22176) open interrupt bugs + the Claude-Code-vs-Hermes cancellation
  analysis.
- **Why this way (one line):** make the stop message *reachable* (concurrent dispatch) and the LLM
  call *actually stoppable* (provider stream teardown) — the two things the comparable projects
  still get wrong.
