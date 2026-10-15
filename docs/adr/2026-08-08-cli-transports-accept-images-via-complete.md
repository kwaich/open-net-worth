# ADR — Correction: both CLI transports DO accept images via `complete()`

- **Date:** 2026-08-08
- **Status:** Superseded by [2026-08-15-cli-stream-chat-carries-native-images](2026-08-15-cli-stream-chat-carries-native-images.md)
- **Agent provenance:** built by an agent after the owner reported `statement_vision`
  refusing a scanned PDF while pinned to `claude_cli:sonnet` ("my fucking model is
  sonnet!"), and pinning the `chat_vision` extractor to `x-ai/grok-4.5` (a different task)
  did not fix it. Investigating the refusal message led to re-testing the claim it was
  based on, empirically, against the real `claude` and `codex` binaries on the machine —
  not the 2.1.216-era testing the original ADRs cited.
- **Corrects:** `docs/adr/2026-07-22-claude-code-cli-provider.md` §"Vision" (item 4),
  `docs/adr/2026-07-22-codex-cli-provider.md` §"Vision"

## Context

Both CLI-provider ADRs stated flatly that the headless CLI "takes no inline base64
images" / "vision is refused loudly", and `services/vision_capability.py` encoded this as
one blanket fact about the *transport*: `claude_cli` and `codex_cli` were both listed in
`_IMAGE_INCAPABLE_TRANSPORTS`, vetoing every image-bearing call on either — `complete()`
(statement vision, the `chat_vision` extractor) and `stream_chat()` (the live chat path)
alike — regardless of the model behind them.

That claim was never actually re-verified against a real CLI invocation carrying an image;
it was inferred from the CLIs having no obvious flag for it. It is wrong for `complete()`
on both transports:

- `claude -p --input-format stream-json` accepts a `content` array on the user message with
  an `{"type": "image", "source": {...}}` block — hand-tested: fed a 1×1 PNG this way, the
  real model answered describing its color, billed normally.
- `codex exec -i <file>... -- <prompt>` reads an attached image file — hand-tested the same
  way, same result. (`-i` is a greedy multi-value flag; the trailing `--` is required to
  stop it swallowing the prompt/`-`-for-stdin argument that follows.)

Neither CLI's **streaming, multi-turn chat path** carries an image, though: both flatten
the whole conversation to one text prompt (`cli_tool_protocol.flatten_messages`) with no
per-message content-block channel wired up, and `codex`'s preferred `app-server` transport
(JSON-RPC over stdio) has no image parameter implemented in this codebase either. So the
transport is not "blind" — the *single-shot call shape* was just never taught to carry a
picture, and `complete()` and `stream_chat()` differ on this CLI-by-CLI, not
transport-by-transport.

## Decision

Split the one blanket fact into two, keyed to the entry point that's actually asking
(`services/vision_capability.py`):

- `_STREAM_CHAT_INCAPABLE_TRANSPORTS = {"claude_cli", "codex_cli"}` — unchanged from before,
  still correctly vetoes the live chat path.
- `_COMPLETE_INCAPABLE_TRANSPORTS = frozenset()` — neither CLI is blind here anymore.

`supports_vision(model_ref, via="stream_chat" | "complete")` threads the caller's entry
point through to the right set. `services/statements/tiers._assert_vision_model_available`
(statement import) and `resolve_extractor_ref` (the `chat_vision` fallback, which only ever
drives `complete()`) both now check `via="complete"`. `api/chat.py::_vision_gap` (deciding
whether the *live* chat model needs the extractor fallback) keeps the default
`via="stream_chat"` — unaffected.

`ai/models/claude_cli_provider.complete` and `ai/models/codex_cli_provider.complete` no
longer raise on `images=`: the former sends an image content block on the same stdin NDJSON
message it already builds; the latter writes each image to a file in its per-call scratch
dir and calls `exec -i <files> -- -` (this always forces the `exec` sub-transport, bypassing
`app-server`, since app-server has no image wiring here).

## Consequences

- A `statement_vision` or `chat_vision` task pinned to a CLI-transport model (any of the
  `sonnet`/`opus`/`haiku` / `gpt-5.6-*` rows shown under "Claude Code CLI" / "Codex CLI" in
  the model picker) now actually works, instead of failing with a message telling the owner
  to do something that would not have fixed it either (every model on that transport was
  equally blind).
- Pasting an image directly into a live chat conversation whose active model is a CLI
  transport is UNCHANGED — still routed through the `chat_vision` extractor
  (`image_extract.py`), because `stream_chat` genuinely still can't carry one. This is the
  correct behavior for that path, not a residual bug.
- `tests/test_vision_capability.py`, `tests/test_claude_cli_provider.py`,
  `tests/test_codex_cli_provider.py` updated to assert the corrected, split behavior.

## Not done

Wiring an image into `stream_chat` on either transport (multi-turn NDJSON content blocks
for `claude_cli`; `codex app-server`'s JSON-RPC protocol has no image field implemented
here, and its `exec` fallback's `-i` is a single-shot flag, not a session primitive) — the
`chat_vision` extractor is a working, deliberately-kept fallback for that case, not a stopgap
this ADR obsoletes.
