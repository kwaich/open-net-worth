---
title: A replied-to chat message is carried into the turn as FENCED context
status: Accepted
date: 2026-08-22
deciders: owner + implementing Claude agent
---

# A replied-to chat message is carried into the turn as FENCED context

## Context

Owner report, 2026-08-22: *"sometimes, when i reply to an old message, it doesn't refer to
that."*

It never did. `update.message.reply_to_message` was read **nowhere** in the backend (zero
hits repo-wide), and `connectors/chat/base.py::InboundMessage` had no field to carry it, so
a Telegram reply arrived as bare text and the quote was discarded at the door. On a long
conversation — or one the fold window has moved past — "why?" under a specific alert was
indistinguishable from "why?" under anything else.

A native reply is the main way a person disambiguates on a chat channel. It is the channel's
equivalent of pointing.

## Decision

`InboundMessage` gains `reply_to: Optional[QuotedMessage]` (`text`, `from_bot`,
`message_id`), Telegram's handlers populate it, and `processor.process_message` prepends a
rendered block **before** the user's own text so the model reads the quote first.

Three choices worth recording:

### The quote is fenced as untrusted DATA

`core.untrusted.fence_untrusted_block("reply_quote", …)` with a per-call nonce, body first
passed through `sanitize_untrusted`.

The obvious objection is that this is the operator's own conversation on an allowlisted
private bot, so nothing here is external. That is true of the *reply* and false of the
*quote*: replying to a Lucius message quotes back whatever that message relayed, which may
have originated at an external MCP server, a fetched page, or a parsed statement. A quoted
body is therefore exactly the kind of content that must not be able to close its own fence
or forge `«user_msg:…»`, the one marker meaning *the operator is speaking*.

`sanitize_untrusted` strips the guillemets outright, so a hostile body's
`«/reply_quote:0000»` and `«user_msg:deadbeef»` both arrive inert while the real 32-hex
nonce fence stays intact. Pinned in `tests/test_telegram_reply_context.py`.

### It names WHOSE message was quoted

`from_bot` selects between "your own earlier message" and "their own earlier message". This
is not decoration: "why?" under our own claim asks us to justify it; "why?" under the
person's own earlier message asks about their reasoning. Collapsing the two would make the
feature actively misleading on the follow-up it is most used for.

### The body is capped, and an empty quote is dropped

1200 characters. A reply to a long report should carry enough to identify what was meant,
not re-inject the whole report into every follow-up turn. A reply to a bare sticker (no text,
no caption) contributes nothing and is dropped rather than sent as an empty block.

## Consequences

- The field is on the channel-neutral `InboundMessage`, and the rendering lives in the shared
  processor, so any future channel with a reply affordance gets this by populating one field.
- A split paste (`_SPLIT_THRESHOLD` debounce) keeps the **first** fragment's quote — that is
  the message the person actually replied from.
- The quoted text is included in what the model reads, which counts against the context
  budget; the cap is what bounds it.
