"""
Allowlist-based auth for the Telegram connector.

The chat-id allowlist comes from ``TelegramConfigStore`` (``allowed_chat_ids``), which is
UI-editable.
  - Positive integers = DM chat IDs (from individual users)
  - Negative integers = group chat IDs
  - If unset, the connector allows NO ONE (fail-closed)

Fail-closed by default (owner review, 2026-08-22): an empty allowlist used to mean "open —
anyone who finds the bot" (a private-operator-bot legacy contract). That is the exact behavior
the ``open`` policy exists for, now dropped from the UI as a foot-gun — reproducing the same
danger silently via a blank field defeated the point. ``/id`` (``handlers.handle_id``) is
NEVER gated by this, so a brand-new user can still learn their own chat id and ask the operator
to add it before anything reaches Lucius.

It is read fresh on each check (no cache) so a UI edit takes effect immediately,
mirroring how the store is read elsewhere in the connector.
"""

from __future__ import annotations


def is_allowed(chat_id: int | str) -> bool:
    from backend.db.telegram_config_store import TelegramConfigStore

    allowed = TelegramConfigStore().allowed_chat_ids()
    return str(chat_id) in allowed
