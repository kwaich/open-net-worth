# A Claude CLI turn reuses a live session, keyed by the transcript prefix

- **Date**: 2026-08-01
- **Status**: Accepted
- **Area**: AI transports (`backend/ai/models/claude_live_session.py`)
- **Amends**: `2026-07-22-claude-code-cli-provider` (process model only)

## Decision

A `claude -p` process is kept **alive across the turns of one conversation** instead of being
spawned per model turn. Only the messages the session has not already seen are sent.

A session is keyed by **the exact prefix of the transcript it has consumed** (a SHA-256 per
message over role + rendered content). Every turn re-checks that the incoming `messages` still
begin with that prefix. If they do not, no session matches, a fresh process is spawned and the
full transcript is re-sent — which is exactly the pre-session behaviour.

Sessions are **uncapped in count** (owner's call): a live chat keeps its process. They are
reaped when idle (`ONW_CLAUDE_CLI_SESSION_IDLE`, default 30 min). `ONW_CLAUDE_CLI_SESSIONS=0`
returns the transport to a process per turn.

## Why

Measured on `claude` 2.1.220: a second message sent into an already-running process cost
**2 input tokens, with 2,690 read from cache**. Today every turn re-sends the whole transcript
as fresh input and pays CLI start-up on top. `--max-turns 1` was verified to apply per message,
so every existing flag is kept and only the process lifetime changes.

OpenClaw reuses the CLI the same way (`ClaudeLiveSession`, "to avoid reinitializing context per
turn") and reports that Anthropic staff confirmed OpenClaw-style `claude -p` reuse is
sanctioned.

Unlike Codex's `app-server`, which multiplexes many threads over one process, `claude -p
--input-format stream-json` is one process per conversation. Reuse therefore *necessarily*
means a live process per chat — there is no shared-process option to choose instead.

## The hazard this is designed against

Once the CLI holds the conversation it, not `assistant.run`, is the authority on what the model
has seen. Our transcript is **not append-only**: context folding, decay, checkpoint restore and
rehydration all rewrite history. A session that survived such a rewrite would answer from a
history we no longer hold, and nothing would surface the divergence.

Prefix-keying makes that failure **structurally impossible rather than merely unlikely**. We
never decide to invalidate a session; a rewrite changes the fingerprints, the match fails, and
the turn rebuilds. The rejected alternative — an explicit `invalidate_session(chat_id)` call at
each rewrite site — would have to be remembered at every future rewrite site, and the failure
mode of forgetting one is silent wrong answers.

Assistant messages are never re-sent: the CLI generated them, so it already holds them, and the
matcher steps over them.

## Consequences

- A failed turn (no `result` event) **retires** its session rather than reusing a poisoned one.
- `MAX_THINKING_TOKENS` rides in the env, not argv, so the session key carries a
  `__thinking_off__` discriminator — otherwise an `effort="none"` turn could be served by a
  session spawned with thinking on.
- Sessions are per event loop, like the existing semaphore, for the same reason.
- A chat whose session was reaped or invalidated pays exactly what it pays today. There is no
  correctness cliff, only a cost one.
