---
title: A silent Claude Code CLI is abandoned after two minutes and retried once
status: Accepted
date: 2026-10-14
deciders: Repo owner, Claude
amends: 2026-07-22-claude-code-cli-provider, 2026-08-01-claude-cli-live-sessions
---

# A silent Claude Code CLI is abandoned after two minutes and retried once

## Context

Persona QA round 5 (2026-10-14): a brand-new user's first message, with a screenshot, produced
no output for the whole 300 s turn ceiling, then failed with "Claude Code CLI timed out after
300s (raise ONW_CLAUDE_CLI_TIMEOUT if this is expected)". Retry answered in 35 s. The only clock
on a CLI round was that one whole-turn ceiling, so a wedged child cost five minutes before
anyone was told, and the message spoke of environment variables to a person who has never heard
of one.

## Decision

1. **A stall watchdog beside the ceiling.** `cli_live_session.LiveSession.events()` and the
   one-shot `ClaudeCliProvider._events` bound the gap between stream events, the first event
   included (`ONW_CLAUDE_CLI_STALL`, default 120 s, Claude only; other transports opt in by
   setting `STALL_DEFAULT`). The ceiling stays for a child that keeps talking.
2. **One silent retry.** `stream_chat` re-runs a STALLED attempt once on a fresh process when
   nothing has reached the user yet (no text, thinking or tool call). Past that, or once
   something was shown, it fails: retrying after output would duplicate it. The retry has its
   own budget, separate from the truncation retries.
3. **A typed timeout, a plain sentence.** Every CLI timeout (Claude, Codex, ceiling or stall)
   raises `core.errors.ProviderTimeoutError`; `api/chat._friendly_error` maps it to "Lucius
   didn't answer in time. Try again." The technical text, env var included, stays in the log.

## Consequences

- Worst case for a wedged first turn is about four minutes of silence in total, with a
  "Still working…" line after ten seconds, instead of five minutes and then a failure.
- A model that legitimately thinks silently for over two minutes without any stream event is
  abandoned; raise `ONW_CLAUDE_CLI_STALL`. Streaming thinking deltas count as events, so this
  has not been seen outside a wedged child.
