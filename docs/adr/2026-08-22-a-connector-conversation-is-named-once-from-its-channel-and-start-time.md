---
title: A connector conversation is named ONCE, from its channel and start time
status: Accepted
date: 2026-08-22
deciders: owner + implementing Claude agent
---

# A connector conversation is named ONCE, from its channel and start time

## Context

Owner, 2026-08-22:

> "for telegram conversation — it shouldn't rename based on the first message. should we
> have some fixed conversation name like telegram - chat id - timestamp or something?"

Two facts had to be established first, because the obvious mental model was wrong.

**Nothing was naming a connector conversation at all.** The backend never sets a title on
the connector path — the only meta write there was `channel`. Titles are generated
exclusively in the frontend (`useConversations.updateConversationMeta` →
`conversationTitleFromFirstMessage`), and the frontend *filters connector conversations out
of the list it titles*: both `ChatPanel` (`!c.source && !c.channel`) and
`ConversationBrowser` drop any row carrying a `channel`. So a Telegram conversation was
permanently `"New conversation"`. The owner's live thread had been that since 06:29 that morning.

The one Telegram conversation that *did* have a different name only had it because the
Wave-5E migration ran `session_io.synthesize_meta` over it once — and what that wrote was
`"[Automated monitor — the following message was delivered to"`, a harness note (see ADR
`2026-08-22-harness-authored-messages-are-recognised-by-shape-not-only-by-marker`).

**First-message naming is right for web and wrong here.** A web chat is topic-scoped: you
open a new one per subject, so its opening line genuinely describes it. A Telegram chat is
ONE continuous thread per chat id, running for weeks. The owner's real conversation opens
with `Hello`; naming a 218-message thread `Hello` says nothing.

## Decision

**`Telegram · 22 Aug 2026, 06:29`** — the channel, then when the thread began. Set once, at
first sight of the session on the shared inbound path, and never rewritten.

### The chat id is deliberately NOT in the name

The owner's sketch included it. It was rejected on a concrete security ground, not taste:

- The `chat_id → session_id` map lives in `secret_config`, a `core.sensitive.SECRET_TABLES`
  member, and `services.backup.write_sanitised_copy` DELETEs those rows from the `onw.db`
  inside a downloaded DR archive — precisely so a leaked backup cannot be used to resume the
  owner's chat session.
- Sessions themselves ARE portable (`services.portability.session_bundle_entries`).

So a chat id in a session title would ride into the very archive that strips the mapping,
quietly defeating an existing control. A partial id (last 4) was also offered and declined:
the same leak, smaller, and it buys nothing while the instance has one chat.

The start time does the disambiguating work regardless — `/new` mints a fresh session for
the same chat id, so start time, not chat id, is what separates one thread from the next.

### Where it lives

`connectors/session_title.py`, called from `chat.run.handle_inbound` — the ONE path every
chat connector's inbound turn goes through, so Slack/Discord/WhatsApp are covered the day
they are added rather than the day someone remembers. It replaces the bare `channel` stamp
that already sat there, setting both in one call.

Separate from `session_map.py`, which must stay AI-FREE (`services.connectors` imports it,
and `services` must never reach `backend.ai`, even transitively — import-linter contract 1).
This module needs `ai.assistant` to read and write session meta, exactly like its sibling
`session_reset.py`.

### Once, and never again

`ensure_session_titled` no-ops on any conversation that already carries a title — including
one an operator typed by hand. Stability is the point of a fixed name, and it means the
call is safe to make on every inbound turn rather than needing a "was this just created?"
signal that `get_or_create_session` cannot give without breaking its AI-free rule.

It prefers the session's own recorded `created_at` over "now", so an EXISTING untitled
conversation is named after when it actually began rather than whenever this first ran.
That is what lets the owner's live thread self-heal to its true 06:29 start on its next
message, with no migration.

The frontend cannot clobber it either: it only renames a conversation still called exactly
`"New conversation"`.

## Consequences

- Every connector conversation gets a stable, readable, sortable name with no backfill.
- No new secret reaches a portable bundle.
- A conversation renamed by the operator stays renamed.
- `%-d` (no zero-pad) is a glibc/BSD strftime extension; both platforms this ships on have
  it, but a Windows port would need `%d`.
- `tests/test_connector_conversation.py::test_every_connector_stamps_its_channel_on_the_session`
  was rewritten from source-text inspection (`"channel=adapter.connector" in src`) to a
  behavioural assertion. It broke on this refactor while the invariant it guards held
  perfectly — the stamp simply moved one call deeper. Asserting the stamp LANDS is both
  stronger and refactor-proof.
