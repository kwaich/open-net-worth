# ADR: Multi-channel message formatting + Telegram rich messages, streaming, artifacts & commands

**Status**: Accepted
**Date**: 2026-07-20
**Commits**: `585e5691` (formatting/rich/streaming/artifacts), `0f10613d` (/model, /status, /cost), `8738023c` (drop /clear)
**Provenance**: Built with Claude Opus 4.8, owner-directed, benchmarked against Hermes Agent (`NousResearch/hermes-agent`, `plugins/platforms/telegram/adapter.py`).

## Context

Outbound messages to chat channels were sent as **raw markdown**. Only the Telegram connector translated anything (markdown → a limited HTML subset); WhatsApp/Slack/Discord shipped the model's literal `**bold**` and `[label](url)`. There was no shared formatting layer, no per-channel length/chunking discipline, no way to render tables, and artifacts (`create_artifact`) never reached a connector at all. The owner runs real chat on Telegram **and** WhatsApp + other channels, so this was a live quality problem, not a hypothetical.

We audited Hermes Agent's Telegram adapter and adopted its patterns where they fit our stack, deliberately diverging where our constraints differ (notably: HTML legacy path, not MarkdownV2).

## Decision

### 1. Shared multi-channel formatter — `connectors/msgfmt.py`
One canonical markdown → per-channel dialect renderer. The assistant emits one markdown string; each channel renders to its own flavour:

| markdown | Telegram | WhatsApp | Slack | Discord | email/matrix |
|---|---|---|---|---|---|
| `**bold**` | `<b>` | `*b*` | `*b*` | `**b**` | `<strong>` |
| `_italic_` | `<i>` | `_i_` | `_i_` | `*i*` | `<em>` |
| `[t](u)` | `<a href>` | `t (u)` | `<u\|t>` | `[t](u)` | `<a href>` |
| code / fence | `<code>`/`<pre>` | `` ` ``/```` ``` ```` | same | same | `<code>`/`<pre>` |

Slack entities escaped; code spans/blocks protected from inline processing; capability flags (`links`/`code`) degrade gracefully. Unknown target → `plain` (markers stripped), never raw markdown.

### 2. `BaseChannelAdapter` — `connectors/base.py`
The single outbound contract every channel adapter subclasses (Hermes's `BasePlatformAdapter` shape). Owns: **format** (→ msgfmt), **length** (per-platform cap + UTF-16 counting for Telegram), **tag/fence-safe chunking** (close+reopen open tags/fences across a split — the fix for the mid-`<a>` split → Telegram 400), and **capability flags**. A subclass declares flags + implements `_deliver`; inherits `format_message`/`prepare`/`send_text`. All connectors (Telegram/WhatsApp/Slack/Discord/email) migrated onto it.

### 3. Telegram rich messages (Bot API 10.2 `sendRichMessage`)
Copies Hermes's **construct-branch** exactly (`_needs_rich_rendering`): the rich endpoint is reserved for constructs the legacy path degrades — **pipe tables, GFM task lists, `<details>`, block math**. Ordinary replies (prose, bold/italic, simple lists) stay on the **legacy HTML path** for consistent typography. Details:
- Rich body is **rendered HTML** (`InputRichMessage.html`), not the raw-markdown field — one HTML rendering philosophy across legacy + rich, and HTML's 3-char escaping is more reliable than markdown's for LLM output.
- **Opt-in flag** (default on for us — the owner wants tables) + **capability latch** (off after a failure) + fallback to legacy HTML.
- **Client-safety guards** (`rich_unsafe`): skip rich for math-inside-`<details>` (crashes TDesktop 6.9.1), CJK (rich garble), or over the 32 768-char cap.

### 4. Artifacts delivered as files
On a connector turn, the `artifact` SSE event (dropped before) is handled: read the stored bytes and `send_document` them as a native file attachment (Hermes's model — owner chose **file over photo/rasterization**). Channels without file support degrade to a one-line text note.

### 5. Live streaming (edit-in-place)
Hermes's pattern on our HTML path (streaming drafts excluded per owner). On each `text_delta`, throttle to ~1.2s and `editMessageText` in place; finalize at turn end. Finalization uses **`editMessageText` `rich_message`** to morph the preview into a rich table **in place** (no delete, no flicker); delete+resend only as a fallback. Balance-safe because our HTML converter emits balanced tags even for partial markdown. Honors `retry_after` flood backoff; stops near the 4096 cap. **Telegram-only** (`supports_streaming`); user-toggleable via `/streaming`.

### 6. Commands + `setMyCommands`
Registered the command list so Telegram shows the `/` autocomplete panel (was missing). Added **`/model`** (picker of configured models + `/model <name>` for any; persists via `ModelConfigStore`), **`/status`** (model/provider/effort/streaming + today's cost), **`/cost`** (today + 30-day spend), **`/streaming`** (toggle live streaming). Continuous "typing…" during long turns is automatic (a background loop, not a command). Consolidated `/new` + `/clear` → just `/new`.

## Key decisions & rationale

- **HTML, not MarkdownV2, for the legacy path.** HTML escapes 3 chars (`< > &`); MarkdownV2 requires ~18 escaped everywhere or the message 400s — a minefield for punctuation-heavy LLM output. Hermes uses MarkdownV2 (and pays for it with a dedicated escaper); we chose the more reliable path. Verified our converter is *balance-safe* on partial markdown, so it's also correct for streaming.
- **Construct-branch, not always-rich.** Rich messages render with different typography and are hard to copy as plain text; ordinary replies stay on the legacy path (Hermes's reasoning, adopted).
- **Rich via HTML field, not markdown field.** One rendering philosophy + more reliable escaping (owner directive).
- **In-place rich finalize** (`editMessageText rich_message`) — no delete/flicker, content preserved.
- **Lean command set.** Hermes ships ~70 commands (it's a general agent shell); a finance bot with 70 slash commands is worse UX.

## Hermes parity — what we deliberately did NOT do
- **`/yolo`** (skip dangerous-command approvals) — defeats the money-safety confirm gate. Hard no.
- **Streaming drafts (`sendRichMessageDraft`)** — Hermes ships it **disabled by default** (Mac/Desktop overlay bugs); our edit-in-place is the equivalent they actually run.
- **Forum topics / reply threading** — structurally N/A for a 1:1 DM bot.
- **TUI toggles, `/moa`, `/personality`, billing, `/browser`, `/codex-runtime`** — not our domain.

## Consequences
- All channels now render bold/links/code correctly; Telegram renders native tables/task-lists/`<details>`/math and streams replies live.
- Artifacts reach chat users as downloadable files.
- Model/cost/status are controllable + visible from Telegram.
- New per-channel capability flags (`supports_streaming`/`_documents`/`_media`/`_typing`) make adding a channel a matter of declaring flags + a transport method.
- **Deferred/known:** inline `$$math$$` mid-sentence renders literally (block math on its own line works); WhatsApp/Slack/Discord don't yet implement `send_document`/streaming (text-note / buffer-and-send fallback); the `/` menu + new commands take effect on the next bot restart.

## Testing
`test_msgfmt`, `test_connector_base`, `test_telegram_formatter`, `test_telegram_rich`, `test_telegram_streaming`, `test_telegram_gaps`, `test_telegram_commands`, `test_connector_artifact`, `test_outbound_channels` (multipart email) — 260+ connector tests, imports + 5/5 import-linter contracts green. Live-verified end-to-end on the owner's Telegram (formatting showcase, native tables incl. a 12-column transactions table, artifact-as-file, edit-in-place streaming → rich finalize, `/model`/`/status` against live config).
