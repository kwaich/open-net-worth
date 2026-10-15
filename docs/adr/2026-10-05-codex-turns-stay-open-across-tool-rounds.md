---
title: A Codex turn stays open across the host's tool rounds
status: Accepted
date: 2026-10-05
deciders: Repo owner, Claude
amends: 2026-10-03-codex-tools-are-native-calls-the-host-runs, 2026-08-01-codex-app-server-transport
---

# A Codex turn stays open across the host's tool rounds

## Context

The host owns the tool loop: `assistant.run` dispatches every call through the service choke
points, `tool_permissions`, confirm staging and audit. Until now that also meant **one Codex
thread per host round**. At the model's first native tool call the app-server client declined
the `item/tool/call` request, interrupted the turn and released the thread; the next round
started a FRESH ephemeral thread whose only input was the whole conversation flattened into one
text message — the model's own earlier calls as `[called X (id …) with …]` prose, results as
`| `-quoted lines, and its reasoning dropped (thinking blocks render as nothing).

Live incident, 2026-10-05 08:30 (Weekly Report automation, `gpt-6.1-sol`): the model read the
report template with `read_skill_file` **67 times in 20 minutes**, never called `create_artifact`,
and the turn was killed by the 20-minute ceiling. Its reasoning shows why: "history that was
pasted weirdly", "store the template … once I get the response object". Dynamic tools reach the
model through **code mode** (ADR 2026-10-03 §5) — it writes a program that awaits our tools —
and the host killed that program at its first `await`, every round. The program never received
a result; the template only existed as quoted text the next program could not read; so it wrote
the same program again. The Claude CLI transport did not loop on the same automation (Oct 1)
because its live session keeps the conversation in one process.

## Decision

1. **A native round PARKS instead of ending the turn.** After the first call (plus siblings in
   the 0.4 s grace) the round yields the calls and returns, leaving the `item/tool/call`
   requests unanswered and the thread open (`codex_live_turn.LiveTurn`). The provider binds the
   tool_use ids it mints to those requests.
2. **The next round CLAIMS it** (`codex_live_turn.claim`) when — and only when — the newest
   assistant message's calls are exactly one parked turn's calls, the following message carries
   all of their results (plus any text the host merged in after them), only plain user text
   follows, and the thread was started with this round's tools, instructions, model and effort
   (`turn_key`). The results are sent as the real answers (`DynamicToolCallResponse`,
   `success: !is_error`, text → `inputText`, base64 images → `inputImage` data URLs). Text after
   the results — an activated skill's body, which `skill_runtime.provider_messages` merges into
   the same user message — reaches the turn through `turn/steer`.
3. **Anything else fails the claim and closes the parked turn** (pending requests declined,
   turn interrupted, thread released): a missing result (a call awaiting approval), a new user
   message, a different tool set (`find_tools`), a changed system prompt. The round then starts
   a fresh thread from the transcript exactly as before, so `assistant.run`'s persisted
   transcript stays the one authority — the divergence concern of ADR 2026-08-01 is answered
   by the claim test, not by discarding the thread every round. A new user turn always starts a
   fresh thread.
4. **Unusable calls are answered in-thread** (`success: false`, "Not run: … call only the tools
   you were given") so the model can correct itself, instead of being surfaced as
   `UNPARSED_CALL_NOTICE` text.
5. **Usage is billed per round as the delta of the thread's `total`**, not the latest `last`
   (one round can make several model requests; `last` undercounted them).
6. Bounds: a parked turn expires after `ONW_CODEX_PARK_SECONDS` (default 900 s) and at most 16
   wait at once (oldest closed first). `ONW_CODEX_LIVE_TURNS=0` restores one thread per round.
   The per-round model timeout (`ONW_CODEX_APP_SERVER_TIMEOUT`) is unchanged; a resume that
   fails before content is retried on a fresh thread by the existing app-server retry.

Measured against codex-cli 0.160.0 before building: an answer sent 150 s after the call is
accepted and the program continues; a `turn/steer` sent while the call is pending is honoured.

## Consequences

- Same automation, same model, after the change (copy of the owner's book): `read_skill_file`
  once, `create_artifact` at ~2.5 min, delivered to Telegram, 172 s total; 36 rounds continued
  in place, 3 threads started (the first, one for `find_tools` growing the tool set, one for a
  skill activation before merged text was handled).
- `[called …]` / `[result of … #nonce]` transcript framing now only reaches the model for
  history from EARLIER turns, or after a fallback.
- `AppServerClient.run_turn` keeps the one-shot (interrupt-and-capture) behaviour for callers
  that are not a tool loop; it is now a thin wrapper over `LiveTurn` with `park=False`.
- Not done: reusing a thread ACROSS user turns (the Claude CLI live session does this, keyed by
  a prefix fingerprint). Within-turn reuse is what fixed the loop; cross-turn reuse would mostly
  save prompt-cache misses.
