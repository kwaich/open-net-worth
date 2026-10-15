---
title: Interactive channels share one provider-pinned conversation turn
status: Accepted
date: 2026-08-15
deciders: orchestrating Codex agent + repo owner
---

# Interactive channels share one provider-pinned conversation turn

## Context

Web chat and Telegram both represented conversations, but they did not enter Lucius through the
same turn boundary. Web selected and snapshotted a model, checked its streaming image capability,
and used the configured `chat_vision` reader when needed. Telegram called the provider runner
directly and passed base64 image blocks through unchanged. A Claude CLI conversation therefore
read an image on web, through the `complete()` fallback, while the same image became an omitted
placeholder on Telegram's `stream_chat()` path.

Telegram's model picker also reduced selections to a friendly model id in places. A name such as
`sonnet` does not identify a provider: it may mean `claude_cli:sonnet`, an OpenRouter model, or a
future provider's model with the same display name. Persisting the bare name lets a later default
provider change silently retarget an existing conversation.

The existing generic chat-channel RFC correctly requires thin transport adapters, but its shared
`connectors.processor` still owned provider invocation and did not include the web attachment and
model-selection policy. The result was a shared connector path, not a shared conversation path.

## Decision

We will give every interactive surface one channel-neutral conversation boundary:

1. Persist explicit conversation selections as canonical `<provider_key>:<model_id>` references.
   Pickers display both the friendly provider label and model id. Telegram's free-form `/model`
   command requires the canonical form; legacy bare session values and older web clients are
   pinned to the current default provider once at the selection/read boundary. An explicit
   selection revalidates provider enabled-state and its preference write fails loudly; a channel
   must never report success for a disabled provider or a selection that was not persisted.
2. Enter provider execution through `ai.conversation.stream_turn`. That boundary resolves one
   immutable model context, builds the provider-specific system prompt, and calls
   `assistant.run`. Web and connector processors no longer duplicate those steps.
3. Prepare images and persisted uploads through `ai.attachments.build_user_content` before the
   shared turn starts. The same `stream_chat` capability check and configured `chat_vision`
   `complete()` fallback apply to direct Telegram images and web upload references.
4. Keep channel adapters responsible for transport work: authentication, ingress normalization,
   session addressing, reactions/typing indicators, and rendering canonical turn events. They do
   not choose a different provider path or attachment policy.
5. Resolve persisted uploads and validate per-image and aggregate raw-image bytes at the shared
   attachment boundary before paid extraction or transcript admission. Native-image requests also
   enforce the shared image-count ceiling; reader fallback retains its stricter explicit page-count
   refusal without making a model call. Provider adapters repeat validation defensively when
   converting historical image blocks to their native wire format; non-base64 image sources are
   rejected rather than passed through unbounded.

An explicit Telegram selection is conversation-scoped. It does not rewrite the instance default
or the model used by unrelated web, Telegram, agent, or background conversations.

## Consequences

### Positive

- The same conversation model and image fallback semantics now apply on web and Telegram.
- Provider identity survives picker display, callback payload handling, persistence, status, and
  provider resolution; duplicate friendly model names no longer collide.
- Adding another chat adapter does not require copying model resolution, prompt construction, or
  image extraction policy.
- Models known to be text-only keep using the configured `complete(images=...)` reader fallback;
  native-image CLI chat paths receive the original attachment through the same capability seam.

### Negative

- The shared conversation and attachment modules become critical cross-channel seams and require
  cross-surface regression tests whenever their contract changes.
- Legacy bare values are interpreted using the default provider that exists when they are first
  read, because their original provider identity is unrecoverable.
- Web-only enrichments such as screen context, mentions, and direct skill invocation are still
  assembled by the web request layer before it enters the shared turn. They are optional request
  capabilities, not alternate provider execution paths, but a future channel that supports them
  must normalize them into the same turn inputs.

### Follow-ups

- Add token-aware preflight budgeting and bounded synthetic/proactive entries at the same shared
  turn boundary; the existing raw-message window alone does not bound prompt size.
- Native CLI image streaming is recorded in
  [2026-08-15-cli-stream-chat-carries-native-images](2026-08-15-cli-stream-chat-carries-native-images.md);
  keep the channel-neutral capability check for genuinely text-only models.

## Alternatives considered

- Fix Telegram's image handler locally — rejected because the next interactive adapter would
  repeat the web/Telegram divergence.
- Teach only Claude CLI `stream_chat` to carry images — rejected as the cross-channel fix because
  it would not unify attachment policy or help other text-only streaming transports; it remains a
  valid provider improvement later.
- Store only the friendly model id and infer the provider at send time — rejected because a
  settings change can silently change an existing conversation's provider and credentials.
- Route Telegram messages through the FastAPI web endpoint — rejected because HTTP is a transport
  concern and would couple connector execution upward into `api/`, violating the dependency rule.

## Agent provenance

- **Decided by:** orchestrating Codex agent + repo owner
- **Lane / branch:** `main` in the shared `/Users/owner/code/my-net-worth` workspace
- **Alternatives weighed:** local Telegram fallback, native CLI streaming images only, late provider inference, and routing connectors through the web API
- **Driving artifacts:** generic chat-channel RFC, [vision fallback ADR](2026-07-26-vision-capability-and-image-fallback.md), [CLI image correction ADR](2026-08-08-cli-transports-accept-images-via-complete.md), and the owner's Telegram/web incident report
- **Why this way (one line):** a conversation's semantic behavior belongs below every transport adapter, with provider identity fixed before the turn starts.
