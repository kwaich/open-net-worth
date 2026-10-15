"""Telegram notification channel (P2-SK2 §B.5).

Registers an async ``_send`` on the ``core.channels`` seam so ``services.notify``
can fan a message out to Telegram WITHOUT ``services`` importing ``connectors``
(the inversion that keeps import-linter Contract 1 green — services depends only on
``core``; this connector module depends DOWN on ``core``). ``_send`` wraps the
existing best-effort ``bot.notify`` coroutine (which already chunks + renders
markdown→HTML and never raises), so a no-Telegram instance just sends 0.

Also registers a proposed-change push fn on ``core.change_push`` so agent findings
with a ``proposed_change`` surface an in-channel confirm button to Telegram users.

``register_channel`` / ``register_change_push`` are invoked at import; ``main.py``'s
lifespan imports this module right after ``telegram_bot.start()`` so both channels
are only wired when the connector boots.
"""

from __future__ import annotations

from backend.core.change_push import register_change_push
from backend.core.channels import register_channel, register_document_channel


async def _send(message: str) -> dict:
    """Broadcast a message to the Telegram notification recipients. Returns {sent: n}.

    Best-effort: ``bot.notify`` never raises and is a safe no-op when the bot isn't
    running, so this returns ``{"sent": 0}`` on a Telegram-less instance.

    When the broadcast actually goes out we ALSO record it into each recipient's chat
    session (the proactive-context bridge): a price/portfolio alert, agent finding, or
    intel update is pushed here OUTSIDE the per-user request loop, so without this the
    user's reply would run on a session with no record the alert was ever sent — Lucius
    would have no context. ``record_proactive_message`` appends it to the conversation as an ordinary
    assistant message, in its true chronological place (it used to STAGE it for the next
    turn, behind a synthetic trigger and a coalescing budget — see that function)."""
    from backend.connectors.telegram import bot

    sent = await bot.notify(message)
    if sent:
        await _record_for_recipients(message)
    return {"sent": sent}


async def _send_document(content: bytes, filename: str, caption: str) -> dict:
    """Broadcast a file (a report artifact) to the notification recipients. Returns {sent: n}.

    Recorded into each recipient's chat session like a text broadcast, naming the file, so
    a reply about the report has the report's caption in context."""
    from backend.connectors.telegram import bot

    sent = await bot.notify_document(content, filename, caption)
    if sent:
        await _record_for_recipients(f"{caption}\n\n[Attached file: {filename}]".strip())
    return {"sent": sent}


async def _record_for_recipients(message: str) -> None:
    """Stage a just-sent broadcast into every recipient's chat session. Best-effort —
    a bookkeeping failure must never break notification delivery."""
    from backend.ai import assistant
    from backend.connectors import session_map
    from backend.connectors.telegram import bot

    for chat_id in bot._notify_recipients():
        try:
            session_id = session_map.get_or_create_session("telegram", str(chat_id))
            # Stamp the CHANNEL on the session. `session_map` knows this conversation is
            # Telegram's, but that mapping lives in the connector's own row and the session row
            # itself named no transport — so the web conversation list had nothing to filter
            # on and showed a phone chat among the owner's browser chats (owner,
            # 2026-08-21). Deliberately NOT `source`: that field is an ownership claim a
            # security gate reads (`automation_cleanup` fail-closes on it), and a transport
            # is a different axis from a runner. Stamped HERE rather than in
            # `session_map.get_or_create_session`
            # because that module is deliberately AI-FREE: `services.connectors` imports it
            # directly, and `services` must never reach `backend.ai`, even transitively
            # (security review 2026-08-17, finding 10/1). This module already imports
            # `assistant`, so the stamp costs nothing and crosses no layer.
            assistant.upsert_session_meta(session_id, channel="telegram")
            await assistant.record_proactive_message(session_id, message)
        except Exception:
            continue


async def _push_change(finding_id: str, proposed_change: dict, summary: str) -> dict:
    """Push an agent-finding proposed change to Telegram notify recipients.

    Each recipient gets a message with ✓ Apply / ✗ Discard inline buttons. The
    ``change_id`` stored in the button callback is the ``finding_id`` (a durable
    store key), so a tap routes to the agent-finding apply/dismiss path rather than
    an ephemeral pending-change entry. Best-effort: never raises."""
    from backend.connectors.telegram import bot as _bot
    from backend.connectors.telegram.bot import _notify_recipients

    app = _bot._app
    if app is None:
        return {"sent": 0}

    from telegram import InlineKeyboardButton, InlineKeyboardMarkup

    keyboard = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("✓ Apply", callback_data=f"confirm:{finding_id}"),
                InlineKeyboardButton("✗ Discard", callback_data=f"discard:{finding_id}"),
            ]
        ]
    )
    text = f"Agent proposed change:\n\n{summary or 'Review in the Inbox.'}"
    sent = 0
    for chat_id in _notify_recipients():
        try:
            await app.bot.send_message(chat_id=chat_id, text=text, reply_markup=keyboard)
            sent += 1
        except Exception:
            pass
    return {"sent": sent}


register_channel("telegram", _send)
register_document_channel("telegram", _send_document)
register_change_push("telegram", _push_change)
