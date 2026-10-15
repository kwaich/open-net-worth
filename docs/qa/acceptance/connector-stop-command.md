# Acceptance — `/stop` to cancel an in-flight connector turn

Covers the `/stop` command on the chat connector (Telegram — the only bidirectional chat
connector as of the 2026-08 channels scope-down; Slack/Discord/WhatsApp were removed), backed by
`connectors/turn_registry.py`, the OpenAI-compatible provider stream teardown, and Telegram
concurrent dispatch. ADR: `docs/adr/2026-07-20-connector-stop-command.md`.

## Cancellation mechanism (turn registry)

- **GIVEN** a turn is running for a channel and its task is registered
  **WHEN** `turn_registry.stop(connector, channel)` is called
  **THEN** the active turn task is cancelled (raises `CancelledError`), and `stop()` returns True.
- **GIVEN** a pending debounce/flush task (Telegram split-message batching) not yet launched
  **WHEN** `/stop` fires **THEN** the pending flush is cancelled and never launches a turn.
- **GIVEN** nothing running for a channel **WHEN** `/stop` fires **THEN** `stop()` returns False.
- **GIVEN** turn A registered then superseded by turn B on the same channel
  **WHEN** A's late `clear_active` runs **THEN** it is a no-op (B stays active; never clobbered).
- Channels are isolated; the connector key is case-insensitive.

## Routing (generic chat seam — provider-agnostic, exercised via Telegram)

- **GIVEN** an allowed sender sends `/stop`
  **THEN** `handle_inbound` cancels this channel's turn, replies "⏹ Stopped." (or "Nothing is
  running to stop." if none), and **does NOT start a new turn** (no rate-limit token, no session).
- **GIVEN** a normal message **THEN** a turn runs and nothing is cancelled.
- Only the explicit command triggers: `/stop`, `/STOP`, `/stop@bot`, `/stop <args>` → yes; bare
  `stop` / `please stop` / `abort` / `/status` / `what does /stop do?` → no (owner: /stop only).

## Telegram

- `/stop` is registered as a `CommandHandler` and appears in `BOT_COMMANDS` + `/help`.
- The bot runs with `concurrent_updates(True)` so `/stop` is dispatched WHILE a turn generates
  (not queued behind it). *(Requires bot-level QA — not unit-testable against the polling loop.)*
- `handle_stop` cancels the active turn + pending debounce for that chat and replies.

## LLM actually stops (provider teardown)

- **GIVEN** an OpenAI-compatible (OpenRouter/GLM) turn streaming
  **WHEN** the turn generator is `aclose()`d (what cancellation triggers)
  **THEN** the upstream stream's `close()` is called — generation and billing stop.
  Pinned by `tests/test_openai_provider_images.py::test_stream_chat_closes_upstream_stream_on_early_aclose`.

## Evidence

- `tests/test_turn_registry.py` — 6 tests (cancellation, debounce, isolation, no-clobber).
- `tests/test_connector_stop.py` — routing + `_is_stop_command` classification + Telegram command.
- Provider teardown: `tests/test_openai_provider_images.py`.

## Status

- [x] Registry + provider teardown + routing — unit/integration tested
- [x] Telegram command registered + concurrent dispatch enabled
- [ ] **Bot-level QA of live Telegram concurrent `/stop` mid-generation** — NOT yet run (needs a
      running bot; cannot be unit-tested)
- [ ] Photo/document-turn `/stop` on Telegram — works via concurrent dispatch but not test-covered
