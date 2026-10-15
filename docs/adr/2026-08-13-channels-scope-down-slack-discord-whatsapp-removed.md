---
title: Channels scope-down — Slack, Discord, and WhatsApp removed; Webhook and Email(SMTP) hidden from discovery
status: Accepted
date: 2026-08-13
deciders: repo owner (product decision) + implementing agent (Claude)
---

# Channels scope-down: Slack/Discord/WhatsApp removed; Webhook + Email(SMTP) catalog cards hidden

## Context

The Connectors "Channels" family had grown to seven surfaces: Telegram, Webhook, Email (SMTP
send), and two-way Slack, Discord, and WhatsApp (each with its own inbound webhook/gateway/socket
transport, config store, allowlist + access-policy + chat-pairing support, and confirm-card
rendering path). The owner made a deliberate product-scope decision to run ONE supported chat
channel (Telegram) going forward, per the Hardening Loop's "drive one flow at a time" philosophy —
maintaining three additional inbound trust boundaries (HMAC/Ed25519 verification, shared-workspace
untrusted-turn scoping, per-connector operator-trust opt-in) for channels with negligible real
usage was pure surface area, not shipped value.

## Decision

1. **Telegram** — kept fully working, backend + frontend, byte-identical.
2. **Webhook** — the backend (API route, `ChannelConfigStore["webhook"]`, the outbound adapter)
   stays fully functional; an already-added Webhook card still appears on the main Connectors tab
   (it is a built-in, always-present transport, same as Telegram/Email). It is removed ONLY from
   the "Browse & add" Directory catalog (`DirectoryModal.tsx`) — not advertised as a new thing to
   add, since it now has no first-class chat/alert consumer of its own (Slack/Discord alerts,
   which used to route through it, are gone). A user who wants it can still reach it because the
   card is always rendered; there is deliberately no route left that discovers it fresh.
3. **Email (SMTP send)** — same treatment for the redundant "Channels" catalog entry only (key
   `email-send`); the unified Email connector (IMAP read + SMTP send in ONE card) keeps its single
   discovery entry point under Data sources (key `email-read`, `onFocusConnector('email')`) — both
   catalog rows always pointed at the same underlying card, so this removes a duplicate front door,
   not a capability.
4. **Slack, Discord, WhatsApp — removed entirely**, chat AND alerts: backend connector packages
   (`backend/connectors/{slack,discord,whatsapp}/`), API routers (`slack_api.py`/`discord_api.py`/
   `whatsapp_inbound_api.py`), config stores (`SlackConfigStore`/`DiscordConfigStore`/
   `WhatsAppInboundConfigStore`), the WhatsApp *alert* channel inside `ChannelConfigStore`, the
   `services/connectors.py` synthesized views + ids, the frontend sections/catalog cards/logos, and
   the msgfmt dialects. Their dedicated test suites were removed with them; shared-infrastructure
   tests that merely used "slack"/"discord" as example connector-id strings for otherwise-generic
   seam behavior were left as-is (the behavior under test is provider-agnostic and still exercised
   by Telegram).

## What was deliberately NOT touched (and why)

- **No DB schema change, no Alembic migration.** `secret_config`'s `slack_config`/
  `discord_config`/`whatsapp_inbound_config` namespace rows and `channels_config`'s `"whatsapp"`
  key can remain in an existing installation's database; nothing reads or writes them anymore, but
  dropping columns/rows is a separate, deliberately deferred decision (per CLAUDE.md's "no
  destructive schema change without explicit ask").
- **Legacy file-path constants stay** (`core/paths.py`'s `SLACK_CONFIG_JSON` /
  `DISCORD_CONFIG_JSON` / `WHATSAPP_INBOUND_CONFIG_JSON`, and their `core.sensitive`/
  `ai.checkpoint._policy` registrations) — they are permanent, additive-only migration/security
  infrastructure (`db/alembic/versions_evidence_source.py` imports them by name forever, per that
  module's own "never rename one" contract), not live feature code.
- **`core/txn_sources.py`'s `_LEGACY_WRITER_ALIASES`** (`"slack"|"discord"|"whatsapp" → "manual"`)
  and **`services/audit.py`'s `_CHANNEL_LABELS`** stay — a transaction booked years ago over Slack
  still carries `source: "slack"` / `surface: "chat:slack"` in storage forever, and these are the
  read-side maps that keep rendering it correctly. Removing them would misrender real history, not
  clean up dead code.
- **Doc comments** in shared/generic infrastructure (`connectors/chat/base.py`, `chat/run.py`,
  `chat/confirm.py`, `ai/_confirm_gate.py`, etc.) that use "Slack/Discord" as an illustrative
  example of "a shared third-party workspace" were left alone where they still correctly describe
  the *mechanism* (which remains generic and could serve a future channel) — only comments that
  asserted something now factually false (e.g. "a config flag on the Slack/Discord store") were
  reworded.
- **`WEBHOOK_PRESETS`** (`presets.ts`) still offers Slack/Discord as URL-shape presets for the
  *webhook* feature — POSTing to a Slack/Discord incoming-webhook URL is a legitimate use of the
  generic webhook transport we kept, independent of whether we run our own Slack/Discord bot.

## Ambiguous call made without blocking

"Don't offer [Webhook] as addable on the main Connectors tab" was read as "remove it from the
Directory's discovery flow," not as "hide the connector card that already renders unconditionally
on the main tab" (matching Telegram/Email's existing always-present-built-in-transport treatment)
— changing THAT would be a materially bigger backend/UI behavior change than the stated scope
("KEEP Webhook's backend implementation fully working").

## Agent provenance

- **Decided by:** repo owner (the product scope-down itself); implementing agent (Claude) executed
  and made the file-level calls documented above.
- **Driving artifact:** owner instruction, 2026-08-13.
- **Why this way (one line):** delete the three unused channels completely rather than
  feature-flagging them off, because dead-but-present code (inbound trust boundaries especially)
  is a maintenance and security liability CLAUDE.md's "no backward-compat shims" convention argues
  against keeping.
