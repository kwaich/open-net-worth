---
title: An Antigravity CLI chat turn reuses a live session and sends its prompt over stdin
status: Accepted
date: 2026-10-05
deciders: senior-engineer lane (Claude) on the orchestrating agent's brief
---

# An Antigravity CLI chat turn reuses a live session and sends its prompt over stdin

## Context

`AntigravityCliProvider.stream_chat` spawned a fresh `agy -p <whole flattened transcript>` process
on every model round. Inside one user turn of N tool rounds the model was therefore re-handed its
own history as one pasted text block each round, with its earlier reasoning dropped — the exact
shape that produced a 20-minute runaway loop on the Codex transport (the model re-derived its plan
every round and re-read the same file 67 times). The prompt also rode as an **argv element**, so a
long transcript would hit the OS `ARG_MAX` (about 1 MB on macOS).

The Claude CLI transport already solved the same problem
(`2026-08-01-claude-cli-live-sessions`): one live process per conversation, keyed by a fingerprint
of the exact transcript prefix it has consumed, sending only the new messages each round. The
Antigravity headless contract (https://antigravity.google/docs/cli/headless/) supports it:
`--input-format stream-json` reads one `{"event": "user", "message": {"content": ...}}` per stdin
line, `--output-format stream-json` answers with `init`, `step_update` and one `result` event per
turn, and the process stays up until stdin closes.

**This was built against the documented contract without a live binary** — `agy` was not installed
or loggable on the build machine. It is verified only against a fake `agy` that speaks the
documented protocol (`tests/test_antigravity_cli_provider.py`). Every inferred detail is listed
under Consequences.

## Decision

We will run every Antigravity chat round on a **live `agy --input-format stream-json
--output-format stream-json` process per conversation** (no `-p`: the documented streaming
example omits it, `-p` takes a prompt value, and a `-p` prompt is dropped in streaming mode), with the same session discipline as
Claude:

- A session is keyed by the spawn argv **plus a hash of the system prompt** (`agy` has no
  system-prompt flag, so the instructions — tool manifest included — ride in the first message; a
  changed prompt or manifest is a different key and gets a fresh process) and by the exact
  transcript prefix it has consumed. A fold, decay, checkpoint restore or edit breaks the prefix
  and the round starts a fresh process with the full transcript.
- The first round sends the full flattened transcript with the `# System instructions` header
  (the content the one-shot path always sent); later rounds send only the pending non-assistant
  messages, normally the tool results. Assistant messages are never re-sent.
- The prompt always travels **over stdin, never argv** — on the session path, with sessions
  switched off, and for `complete` / `stream_complete`, which stay one-shot but now speak
  stream-json too.
- `step_update.text_delta` streams through the existing `ToolCallScanner` (tool calls stay on the
  `<tool_call>` text protocol); `result.usage` maps to the provider's usage event with the
  existing `cost_basis` / cost logic — as the PER-ROUND DELTA, because the docs say the usage
  counters "track the cumulative session" (the session remembers the last totals); `result` ends a round. A round with no `result`, or a
  `status` other than `SUCCESS`, retires the session; a REUSED session that fails before anything
  was shown is retried once on a brand-new process (never another idle pooled session). A failed round's
  held-back text is not flushed. A timeout is never retried.
- `ONW_ANTIGRAVITY_CLI_SESSIONS=0` returns to a process per round (still stdin);
  `ONW_ANTIGRAVITY_CLI_SESSION_IDLE` (default 30 min) reaps idle sessions;
  `ONW_ANTIGRAVITY_CLI_TIMEOUT` (default 600 s) bounds a round, as before. The two-slot
  concurrency semaphore is held for the duration of a round, not while a session idles.
- The transport-agnostic half of `claude_live_session` (fingerprinting, `SessionPool`, prefix
  matching, pending-message rendering, idle reaping, acquire/release, the `events()` reader) moves
  to `backend/ai/models/cli_live_session.py`. `claude_live_session` and
  `antigravity_live_session` are thin transport modules that supply the wire format, the
  turn-terminating event and their env knobs; the Claude module's public names are unchanged.

`--dangerously-skip-permissions` is deliberately **unchanged** (flagged separately as a security
item) and no permission is added.

## Consequences

### Positive

- Within one user turn the model keeps its own conversation across tool rounds instead of
  re-reading a pasted copy of it, and the CLI's prompt cache can serve the prior context.
- A transcript beyond `ARG_MAX` no longer fails at spawn.
- One shared implementation of the prefix-matching rule: a fix to it lands in both transports.
- A latent bug in the old path disappears: it passed the `(text, images)` tuple returned by
  `flatten_messages_with_images` where a string was expected, so the prompt carried a tuple repr.

### Negative

- A live `agy` process per active chat, uncapped in count like Claude's, reaped when idle.
- Guessed, not observed (verify against the real binary before relying on them):
  - the idle-session lifetime — `--print-timeout` (default 5m) may end the process itself, in
    which case the session dies, is reaped, and the next round starts fresh (safe, but loses the
    reuse);
  - that `text_delta` is incremental per step and appears only for model text (a thinking step,
    or an `agy`-internal tool step, might carry one); the `result.response` stands in when a round
    streamed no delta;
  - that `usage.input_tokens` / `cache_read_tokens` mean what OpenAI-style cost accounting
    assumes (mapped as the one-shot path always did, now as per-round deltas of the documented
    session-cumulative counters; a counter that goes down is taken as a restart); `thinking_tokens`
    is not billed separately;
  - the streaming argv: it follows the documented example exactly (`--input-format stream-json
    --output-format stream-json`, no `-p`) — the one place a wrong guess would fail every run;
  - images: the documented user event carries text only, so an attachment keeps its
    `[attached image N]` marker but its bytes are not forwarded (the old path forwarded nothing
    either);
  - that stdin stays open between rounds without `agy` treating idle as end-of-session.
- The session runs in a scratch working directory rather than the app's cwd.

### Follow-ups

- Run once against a real `agy` and confirm the guessed items above; adjust the session key or
  fall back to `ONW_ANTIGRAVITY_CLI_SESSIONS=0` if idle stdin is not honoured.
- Revisit `--dangerously-skip-permissions` (separate security item).

## Alternatives considered

- `--conversation <id>` resume with a fresh process per round — rejected: still pays start-up and
  trusts the CLI's persisted conversation, which our fold / restore / edit rewrites would silently
  diverge from; prefix-keying makes that structurally impossible.
- A separate `antigravity_live_session` with its own copy of the pool — rejected: two copies of
  the invalidation rule would drift; the rule is the load-bearing part.
- Keep one-shot but move the prompt to stdin only — rejected: fixes `ARG_MAX` but not the
  per-round re-pasted history that caused the Codex runaway.
- Pass `--input-format text` for one-shot — rejected: the contract documents stdin prompts only
  for stream-json input.

## Agent provenance

- **Decided by:** senior-engineer lane (Claude), on the orchestrating agent's brief.
- **Lane / branch:** `worktree-agent-a33a27ca4bb31112f`.
- **Alternatives weighed:** `--conversation` resume; duplicated session module; stdin-only
  one-shot; `--input-format text`.
- **Driving artifacts:** `2026-08-01-claude-cli-live-sessions`; the Codex runaway-loop diagnosis;
  the Antigravity headless docs.
- **Why this way (one line):** reuse the proven prefix-keyed live-session design so the model
  keeps its own history across tool rounds, with the shared half factored out rather than forked.
